"""Batches, students, their documents and their transition history."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.enums import (
    AgreementStatus,
    BatchStatus,
    CompletionRecordStatus,
    DocumentType,
    RoadmapStage,
    StudentStatus,
)
from apps.core.models import SoftDeleteModel
from apps.academy.status import ARCHIVED_STATUSES, RESEARCH_PENDING_STATUSES, roadmap_progress


class Batch(SoftDeleteModel):
    code = models.CharField(max_length=40, unique=True, help_text="Human-facing code, e.g. YTA-2026-01.")
    name = models.CharField(max_length=160)
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="led_batches"
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=BatchStatus.choices, default=BatchStatus.PLANNED, db_index=True)
    research_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Per-batch override of the academy research window. Blank uses the global setting.",
    )
    description = models.TextField(null=True, blank=True)
    criteria_set = models.ForeignKey(
        "research.ResearchCriteriaSet", null=True, blank=True, on_delete=models.SET_NULL, related_name="batches"
    )

    class Meta:
        db_table = "batches"
        verbose_name_plural = "batches"
        ordering = ["status", "-start_date"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def effective_research_days(self) -> int:
        from apps.core.settings_service import effective_research_days

        return effective_research_days(self.research_days)

    @property
    def uses_override(self) -> bool:
        return bool(self.research_days and self.research_days > 0)


class StudentQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def tracked(self):
        """Students still actively being followed."""
        return self.exclude(status__in=ARCHIVED_STATUSES)

    def for_actor(self, actor):
        """
        Row scoping, applied to every list query. Fails closed: an unresolvable
        scope returns nothing rather than everything.
        """
        from apps.core.permissions import SCOPE_ALL, SCOPE_ASSIGNED, SCOPE_OWN, scope_for

        scope = scope_for(actor.role, "student")
        if scope == SCOPE_ALL:
            return self
        if scope == SCOPE_ASSIGNED:
            return self.filter(instructor=actor)
        if scope == SCOPE_OWN:
            profile = getattr(actor, "student_profile", None)
            return self.filter(pk=profile.pk) if profile else self.none()
        return self.none()

    def research_outstanding(self):
        return self.filter(status__in=RESEARCH_PENDING_STATUSES)


class StudentManager(models.Manager):
    def get_queryset(self):
        return StudentQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)

    def tracked(self):
        return self.get_queryset().tracked()

    def for_actor(self, actor):
        return self.get_queryset().for_actor(actor)


class Student(SoftDeleteModel):
    """The central record. Everything else hangs off this."""

    enrollment_id = models.CharField(
        max_length=32, unique=True, db_index=True, help_text="Generated. PREFIX-YYYY-NNNN."
    )
    # Optional login. Students without one are tracked but cannot sign in.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="student_profile"
    )

    full_name = models.CharField(max_length=160)
    guardian_name = models.CharField(max_length=160, null=True, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=40, null=True, blank=True)
    city = models.CharField(max_length=120, null=True, blank=True)
    country = models.CharField(max_length=120, null=True, blank=True)

    enrollment_date = models.DateField()
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="students")
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_students"
    )

    course_start_date = models.DateField(null=True, blank=True)
    expected_completion_date = models.DateField(null=True, blank=True)
    research_start_date = models.DateField(null=True, blank=True)
    # Derived from research_start_date + the effective window, but stored so a
    # deadline can be extended for one student without side effects.
    research_deadline = models.DateField(null=True, blank=True, db_index=True)

    roadmap_stage = models.CharField(
        max_length=32, choices=RoadmapStage.choices, default=RoadmapStage.ENROLLMENT, db_index=True
    )
    status = models.CharField(
        max_length=32, choices=StudentStatus.choices, default=StudentStatus.ENROLLED, db_index=True
    )

    lms_student_id = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    profile_image_url = models.URLField(max_length=500, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    agreement_status = models.CharField(
        max_length=24, choices=AgreementStatus.choices, default=AgreementStatus.NOT_ISSUED
    )
    completion_status = models.CharField(
        max_length=24, choices=CompletionRecordStatus.choices, null=True, blank=True
    )
    # Last time the student did anything observable (submission, upload, login).
    last_activity_at = models.DateTimeField(null=True, blank=True)

    objects = StudentManager()
    all_objects = models.Manager()

    class Meta:
        db_table = "students"
        ordering = ["-enrollment_date", "full_name"]
        indexes = [
            models.Index(fields=["batch", "status"]),
            models.Index(fields=["instructor"]),
            models.Index(fields=["research_deadline"]),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.enrollment_id})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("academy:student_detail", args=[self.pk])

    @property
    def progress_percent(self) -> int:
        return roadmap_progress(self.roadmap_stage)

    @property
    def is_research_overdue(self) -> bool:
        return bool(
            self.research_deadline
            and self.research_deadline < timezone.localdate()
            and self.status in RESEARCH_PENDING_STATUSES
        )

    @property
    def days_until_deadline(self) -> int | None:
        if not self.research_deadline:
            return None
        return (self.research_deadline - timezone.localdate()).days


class StudentDocument(SoftDeleteModel):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="documents")
    type = models.CharField(max_length=24, choices=DocumentType.choices, default=DocumentType.OTHER)
    file_name = models.CharField(max_length=255)
    file = models.FileField(upload_to="student-documents/%Y/%m/", null=True, blank=True)
    file_url = models.URLField(max_length=500, null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)
    mime_type = models.CharField(max_length=120, null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploaded_documents"
    )

    class Meta:
        db_table = "student_documents"
        ordering = ["-created_at"]

    def __str__(self):
        return self.file_name


class StudentStatusHistory(models.Model):
    """Append-only record of every status transition (specification section 18)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=32, choices=StudentStatus.choices, null=True, blank=True)
    to_status = models.CharField(max_length=32, choices=StudentStatus.choices)
    from_stage = models.CharField(max_length=32, choices=RoadmapStage.choices, null=True, blank=True)
    to_stage = models.CharField(max_length=32, choices=RoadmapStage.choices, null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="status_changes"
    )
    # True when written by a background job rather than a person.
    is_automated = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "student_status_history"
        ordering = ["-created_at"]
        verbose_name_plural = "student status history"
        indexes = [models.Index(fields=["student", "-created_at"])]

    def __str__(self):
        return f"{self.student_id}: {self.from_status or 'new'} to {self.to_status}"
