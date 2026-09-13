"""
Assignment records mirrored from the LMS (specification section 6).

Same append-only rule as research: an attempt is never updated once recorded,
so an LMS-side edit cannot erase feedback already given.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.enums import AssignmentType, LmsProvider, SubmissionStatus, SyncStatus
from apps.core.models import SoftDeleteModel


class Assignment(SoftDeleteModel):
    batch = models.ForeignKey(
        "academy.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="assignments"
    )
    title = models.CharField(max_length=200)
    type = models.CharField(max_length=24, choices=AssignmentType.choices, default=AssignmentType.RESEARCH)
    description = models.TextField(null=True, blank=True)
    # Identifier in the external LMS, when linked.
    lms_assignment_id = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    due_at = models.DateTimeField(null=True, blank=True)
    max_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "assignments"
        ordering = ["due_at", "-created_at"]

    def __str__(self):
        return self.title


class AssignmentSubmission(models.Model):
    """Append-only, exactly like ResearchSubmission."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey("academy.Student", on_delete=models.CASCADE, related_name="assignment_submissions")
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=24, choices=SubmissionStatus.choices, default=SubmissionStatus.NOT_STARTED, db_index=True
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    evaluator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_assignments"
    )
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    # Identifier of this attempt in the external LMS. Unique so a repeated
    # import or a webhook retry cannot duplicate an attempt.
    lms_submission_id = models.CharField(max_length=200, null=True, blank=True, unique=True)
    # Raw payload from the LMS adapter, kept for traceability.
    lms_raw_payload = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assignment_submissions"
        ordering = ["-submitted_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "student", "version"], name="uniq_assignment_student_version"
            )
        ]
        indexes = [models.Index(fields=["student", "status"])]

    def __str__(self):
        return f"{self.assignment_id} v{self.version}"


class AssignmentFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(AssignmentSubmission, on_delete=models.CASCADE, related_name="files")
    file_name = models.CharField(max_length=255)
    file = models.FileField(upload_to="assignments/%Y/%m/", null=True, blank=True)
    file_url = models.URLField(max_length=500, null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)
    mime_type = models.CharField(max_length=120, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "assignment_files"

    def __str__(self):
        return self.file_name


class LmsConnection(models.Model):
    """
    One configured LMS. The adapter interface means adding a vendor is a new
    module, not a schema change.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    provider = models.CharField(max_length=32, choices=LmsProvider.choices, default=LmsProvider.MANUAL)
    base_url = models.URLField(max_length=500, null=True, blank=True)
    # AES-256-GCM ciphertext of the API token or client secret.
    encrypted_credentials = models.TextField(null=True, blank=True)
    # Adapter-specific settings (course ids, field mappings).
    config = models.JSONField(null=True, blank=True)
    is_active = models.BooleanField(default=False)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=16, choices=SyncStatus.choices, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "lms_connections"

    def __str__(self):
        return f"{self.name} ({self.provider})"


class LmsSyncLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(LmsConnection, on_delete=models.CASCADE, related_name="sync_logs")
    status = models.CharField(max_length=16, choices=SyncStatus.choices)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    records_read = models.IntegerField(default=0)
    records_written = models.IntegerField(default=0)
    error_message = models.TextField(null=True, blank=True)
    details = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "lms_sync_logs"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.connection_id} {self.status} @ {self.started_at:%Y-%m-%d %H:%M}"
