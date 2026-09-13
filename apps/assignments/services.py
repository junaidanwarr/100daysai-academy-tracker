"""
Assignment records mirrored from the LMS (specification section 6).

Same append-only rule as research: an attempt is never updated once recorded,
so an LMS-side edit cannot erase feedback already given.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.academy.models import Student
from apps.academy.services import touch_activity
from apps.assignments.lms import LmsSubmissionRow
from apps.assignments.models import Assignment, AssignmentSubmission, LmsConnection, LmsSyncLog
from apps.core.audit import write_audit
from apps.core.enums import AssignmentType, AuditAction, SubmissionStatus, SyncStatus
from apps.core.middleware import current_request_meta
from apps.core.permissions import assert_can, scope_for, SCOPE_ALL, SCOPE_ASSIGNED, SCOPE_OWN

REVIEWED_STATUSES = [
    SubmissionStatus.APPROVED,
    SubmissionStatus.REJECTED,
    SubmissionStatus.REVISION_REQUESTED,
]


def scoped_submissions(actor):
    queryset = AssignmentSubmission.objects.select_related("assignment", "student", "evaluator")
    scope = scope_for(actor.role, "assignment")

    if scope == SCOPE_ALL:
        return queryset.filter(student__deleted_at__isnull=True)
    if scope == SCOPE_ASSIGNED:
        return queryset.filter(student__deleted_at__isnull=True, student__instructor=actor)
    if scope == SCOPE_OWN:
        profile = getattr(actor, "student_profile", None)
        return queryset.filter(student=profile) if profile else queryset.none()
    return queryset.none()


@transaction.atomic
def record_submission(actor, assignment: Assignment, student: Student, *, status: str,
                      submitted_at=None, score=None, feedback=None, rejection_reason=None,
                      lms_submission_id: str | None = None, raw_payload: dict | None = None):
    """
    Records an attempt. Always a new version — a prior attempt is never touched.

    Returns ``(submission, created)``. Idempotent on the LMS identifier, so a
    repeated import or a webhook retry cannot duplicate an attempt.
    """
    assert_can(actor.role, "assignment", "create")

    if lms_submission_id:
        duplicate = AssignmentSubmission.objects.filter(lms_submission_id=lms_submission_id).first()
        if duplicate:
            return duplicate, False

    latest = (
        AssignmentSubmission.objects.filter(assignment=assignment, student=student)
        .order_by("-version")
        .first()
    )
    version = (latest.version if latest else 0) + 1

    if latest:
        AssignmentSubmission.objects.filter(
            assignment=assignment, student=student, superseded_at__isnull=True
        ).update(superseded_at=timezone.now())

    reviewed = status in REVIEWED_STATUSES

    submission = AssignmentSubmission.objects.create(
        assignment=assignment,
        student=student,
        version=version,
        status=status,
        submitted_at=submitted_at or timezone.now(),
        score=score,
        feedback=feedback,
        rejection_reason=rejection_reason,
        lms_submission_id=lms_submission_id,
        lms_raw_payload=raw_payload,
        reviewed_at=timezone.now() if reviewed else None,
        evaluator=actor if reviewed and getattr(actor, "pk", None) else None,
        approved_at=timezone.now() if status == SubmissionStatus.APPROVED else None,
    )

    touch_activity(student)

    write_audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="AssignmentSubmission",
        entity_id=submission.pk,
        summary=(
            f'Recorded "{assignment.title}" attempt {version} for '
            f"{student.full_name} ({student.enrollment_id}): {status}"
        ),
        after={"version": version, "status": status, "score": score},
        **current_request_meta(),
    )
    return submission, True


def ingest_submissions(actor, rows: list[LmsSubmissionRow], connection: LmsConnection | None = None) -> dict:
    """
    Ingests submissions from any adapter. Matches on ``lms_student_id`` first,
    then email; an unmatched row is reported rather than guessed at, because
    attaching one student's work to another's record is worse than a queue to
    resolve.
    """
    assert_can(actor.role, "assignment", "create")

    started = timezone.now()
    unmatched: list[str] = []
    written = skipped = 0

    for row in rows:
        lookup = Q(pk=None)
        if row.student.external_id:
            lookup |= Q(lms_student_id=row.student.external_id)
        if row.student.email:
            lookup |= Q(email__iexact=row.student.email)

        student = Student.objects.filter(lookup).first()
        if not student:
            unmatched.append(row.student.external_id or row.student.email or row.external_id)
            continue

        assignment = Assignment.objects.filter(lms_assignment_id=row.assignment_external_id).first()
        if not assignment:
            # Create a shell assignment rather than dropping the submission;
            # staff can rename it later and the work is not lost meanwhile.
            assignment = Assignment.objects.create(
                title=f"Imported: {row.assignment_external_id}",
                lms_assignment_id=row.assignment_external_id,
                type=AssignmentType.OTHER,
            )

        status = row.status
        if status in (SubmissionStatus.NOT_STARTED, SubmissionStatus.DRAFT):
            status = SubmissionStatus.SUBMITTED

        _, created = record_submission(
            actor,
            assignment,
            student,
            status=status,
            submitted_at=row.submitted_at,
            score=row.score,
            feedback=row.feedback,
            rejection_reason=row.rejection_reason,
            lms_submission_id=row.external_id,
            raw_payload=row.raw,
        )
        written += 1 if created else 0
        skipped += 0 if created else 1

    if connection:
        status = SyncStatus.PARTIAL if unmatched else SyncStatus.SUCCESS
        LmsSyncLog.objects.create(
            connection=connection,
            status=status,
            started_at=started,
            finished_at=timezone.now(),
            records_read=len(rows),
            records_written=written,
            error_message=f"{len(unmatched)} row(s) matched no student." if unmatched else None,
            details={"unmatched": unmatched[:50]},
        )
        connection.last_sync_at = timezone.now()
        connection.last_sync_status = status
        connection.save(update_fields=["last_sync_at", "last_sync_status", "updated_at"])

    return {"read": len(rows), "written": written, "skipped": skipped, "unmatched": unmatched}
