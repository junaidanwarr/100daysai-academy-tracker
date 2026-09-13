"""
Student lifecycle operations.

Every function takes the acting user so authorization, row scoping and audit
attribution are handled in one place rather than re-derived by each caller.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.academy.enrollment_id import next_enrollment_id
from apps.academy.filters import PresetContext, STUDENT_PRESETS, get_preset
from apps.academy.models import Batch, Student, StudentStatusHistory
from apps.academy.status import STATUS_TO_STAGE, assert_transition
from apps.core.audit import diff_fields, snapshot, write_audit
from apps.core.enums import AuditAction, StudentStatus
from apps.core.middleware import current_request_meta
from apps.core.permissions import assert_can
from apps.core.settings_service import ENROLLMENT_ID_PREFIX, effective_research_days, get_setting

AUDITED_STUDENT_FIELDS = [
    "full_name", "guardian_name", "email", "phone", "city", "country",
    "enrollment_date", "batch", "instructor", "course_start_date",
    "expected_completion_date", "research_start_date", "research_deadline",
    "status", "roadmap_stage", "lms_student_id", "notes",
]


@transaction.atomic
def allocate_enrollment_id(year: int) -> str:
    """
    Allocates the next Enrollment ID under a row lock, so two concurrent
    enrollments cannot be handed the same number. The unique constraint would
    catch it, but the user would see a confusing error instead of a student.
    """
    prefix = str(get_setting(ENROLLMENT_ID_PREFIX) or "100DAI").upper()

    latest = (
        Student.all_objects.select_for_update()
        .filter(enrollment_id__startswith=f"{prefix}-{year}-")
        .order_by("-enrollment_id")
        .values_list("enrollment_id", flat=True)
        .first()
    )
    return next_enrollment_id(prefix, year, latest)


@transaction.atomic
def create_student(actor, data: dict) -> Student:
    assert_can(actor.role, "student", "create")

    batch: Batch = data["batch"]
    research_days = effective_research_days(batch.research_days)
    research_start = data.get("research_start_date")

    # Deadline is derived here rather than in the form so it always honours the
    # configured window, and stays editable per student afterwards.
    deadline = data.get("research_deadline") or (
        research_start + timedelta(days=research_days) if research_start else None
    )

    status = StudentStatus.RESEARCH_IN_PROGRESS if research_start else StudentStatus.ENROLLED

    student = Student.objects.create(
        enrollment_id=allocate_enrollment_id(data["enrollment_date"].year),
        full_name=data["full_name"],
        guardian_name=data.get("guardian_name"),
        email=data["email"],
        phone=data.get("phone"),
        city=data.get("city"),
        country=data.get("country"),
        enrollment_date=data["enrollment_date"],
        batch=batch,
        # Fall back to the batch instructor so nobody is unassigned by accident.
        instructor=data.get("instructor") or batch.instructor,
        course_start_date=data.get("course_start_date"),
        expected_completion_date=data.get("expected_completion_date"),
        research_start_date=research_start,
        research_deadline=deadline,
        lms_student_id=data.get("lms_student_id"),
        profile_image_url=data.get("profile_image_url"),
        notes=data.get("notes"),
        status=status,
        roadmap_stage=STATUS_TO_STAGE[status],
        last_activity_at=timezone.now(),
    )

    StudentStatusHistory.objects.create(
        student=student,
        from_status=None,
        to_status=student.status,
        to_stage=student.roadmap_stage,
        reason="Student enrolled",
        changed_by=actor if getattr(actor, "pk", None) else None,
    )

    write_audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="Student",
        entity_id=student.pk,
        summary=f"Enrolled {student.full_name} ({student.enrollment_id})",
        after={"enrollment_id": student.enrollment_id, "full_name": student.full_name, "batch": batch.code},
        **current_request_meta(),
    )
    return student


def update_student(actor, student: Student, data: dict) -> Student:
    assert_can(actor.role, "student", "update")

    before = snapshot(student, AUDITED_STUDENT_FIELDS)
    for field, value in data.items():
        setattr(student, field, value)
    student.save()

    changed_before, changed_after = diff_fields(before, snapshot(student, AUDITED_STUDENT_FIELDS))

    write_audit(
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="Student",
        entity_id=student.pk,
        summary=f"Updated {student.full_name} ({student.enrollment_id})",
        before=changed_before,
        after=changed_after,
        **current_request_meta(),
    )
    return student


@transaction.atomic
def change_status(
    actor,
    student: Student,
    to_status: str,
    reason: str | None = None,
    override: bool = False,
    propagated: bool = False,
) -> Student:
    """
    Status change with the state machine enforced and a history row written.
    `override` is a Super Admin correction and is audited as OVERRIDE.

    `propagated` marks a transition that is a *consequence* of an action the
    calling service has already authorized — a student submitting their own
    research moves themselves to Assignment Submitted. Without it, that path
    fails: a student holds `research: create` but deliberately not
    `student: update`, and correctly so, since they must not be able to set
    their own status directly. Only the `student: update` check is skipped;
    the state machine, the history row and the audit entry all still apply,
    and the transition is still attributed to the student who caused it.
    """
    if not propagated:
        assert_can(actor.role, "student", "update")
    if override:
        assert_can(actor.role, "student", "override")

    assert_transition(student.status, to_status, override)

    from_status = student.status
    from_stage = student.roadmap_stage
    to_stage = STATUS_TO_STAGE[to_status]

    student.status = to_status
    student.roadmap_stage = to_stage
    student.last_activity_at = timezone.now()
    student.save(update_fields=["status", "roadmap_stage", "last_activity_at", "updated_at"])

    StudentStatusHistory.objects.create(
        student=student,
        from_status=from_status,
        to_status=to_status,
        from_stage=from_stage,
        to_stage=to_stage,
        reason=reason,
        changed_by=actor if getattr(actor, "pk", None) else None,
        is_automated=not getattr(actor, "pk", None),
    )

    write_audit(
        actor=actor,
        action=AuditAction.OVERRIDE if override else AuditAction.UPDATE,
        entity_type="Student",
        entity_id=student.pk,
        summary=f"Status {from_status} to {to_status}" + (" (override)" if override else ""),
        before={"status": from_status, "roadmap_stage": from_stage},
        after={"status": to_status, "roadmap_stage": to_stage},
        **current_request_meta(),
    )
    return student


def archive_student(actor, student: Student, reason: str) -> None:
    """Soft delete. History and audit rows are deliberately left intact."""
    assert_can(actor.role, "student", "delete")

    student.soft_delete()

    write_audit(
        actor=actor,
        action=AuditAction.DELETE,
        entity_type="Student",
        entity_id=student.pk,
        summary=f"Archived {student.full_name} ({student.enrollment_id}): {reason}",
        before={"deleted_at": None},
        after={"deleted_at": student.deleted_at},
        **current_request_meta(),
    )


def preset_counts(actor) -> dict[str, int]:
    """Counts for each saved filter, for the sidebar badges."""
    ctx = PresetContext.current()
    base = Student.objects.for_actor(actor)
    counts = {}
    for preset in STUDENT_PRESETS:
        query = base.filter(preset.build(ctx)).distinct()
        if preset.key == "rejected-repeatedly":
            # Prisma-style relation counts are not expressible in a single Q,
            # so the exact threshold is applied here by annotation.
            query = (
                base.annotate(rejections=Count("research_submissions", filter=Q(research_submissions__status="REJECTED")))
                .filter(rejections__gt=ctx.max_rejections)
                .distinct()
            )
        counts[preset.key] = query.count()
    return counts


def filtered_students(actor, *, preset: str | None = None, search: str | None = None,
                      batch_id: str | None = None, status: str | None = None):
    """Advanced search per specification section 19."""
    assert_can(actor.role, "student", "read")

    queryset = Student.objects.for_actor(actor).select_related("batch", "instructor")

    if preset:
        found = get_preset(preset)
        if found:
            ctx = PresetContext.current()
            if preset == "rejected-repeatedly":
                queryset = queryset.annotate(
                    rejections=Count("research_submissions", filter=Q(research_submissions__status="REJECTED"))
                ).filter(rejections__gt=ctx.max_rejections)
            else:
                queryset = queryset.filter(found.build(ctx))

    if search:
        queryset = queryset.filter(
            Q(enrollment_id__icontains=search)
            | Q(full_name__icontains=search)
            | Q(email__icontains=search)
            | Q(phone__icontains=search)
            | Q(lms_student_id__icontains=search)
            | Q(channels__channel_name__icontains=search)
            | Q(channels__channel_url__icontains=search)
            | Q(channels__niche__icontains=search)
        )

    if batch_id:
        queryset = queryset.filter(batch_id=batch_id)
    if status:
        queryset = queryset.filter(status=status)

    return queryset.annotate(
        channel_count=Count("channels", filter=Q(channels__deleted_at__isnull=True), distinct=True),
        open_alert_count=Count(
            "alerts", filter=Q(alerts__status__in=["NEW", "IN_PROGRESS", "ESCALATED"]), distinct=True
        ),
    ).distinct()


def touch_activity(student: Student) -> None:
    """Marks observable activity. Called by submission, upload and login paths."""
    Student.objects.filter(pk=student.pk).update(last_activity_at=timezone.now())
