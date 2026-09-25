"""
Assignments: set here or mirrored from the LMS (specification section 6).

Two routes in, one record. A student may hand work in through the portal, or
the LMS may report it by CSV or webhook; either way the attempt lands in the
same table and is reviewed on the same screen.

Same append-only rule as research: an attempt is never edited once reviewed. A
resubmission is version N+1 and every earlier decision stays readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.academy.activity import record_activity
from apps.academy.models import Batch, Student
from apps.assignments.lms import LmsSubmissionRow
from apps.assignments.models import Assignment, AssignmentSubmission, LmsConnection, LmsSyncLog
from apps.core.audit import diff_fields, write_audit
from apps.core.enums import ActivityKind, AssignmentType, AuditAction, SubmissionStatus, SyncStatus
from apps.core.middleware import current_request_meta
from apps.core.permissions import assert_can, scope_for, SCOPE_ALL, SCOPE_ASSIGNED, SCOPE_OWN


class AssignmentStateError(Exception):
    """Raised when an operation would violate the append-only rule."""

    status_code = 422


# Still with the evaluator: the student may not start another attempt.
OPEN_STATUSES = [
    SubmissionStatus.SUBMITTED,
    SubmissionStatus.UNDER_REVIEW,
    SubmissionStatus.RESUBMITTED,
]

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

    record_activity(
        student,
        ActivityKind.ASSIGNMENT_SUBMITTED,
        f'Submitted "{assignment.title}" (attempt {version}) via the LMS',
        actor=actor,
        target=submission,
        occurred_at=submission.submitted_at,
        metadata={"source": "lms"},
    )
    if reviewed:
        record_activity(
            student,
            ActivityKind.ASSIGNMENT_REVIEWED,
            f'"{assignment.title}" attempt {version} {submission.get_status_display().lower()} in the LMS',
            actor=actor,
            detail=feedback or rejection_reason,
            target=submission,
            metadata={"source": "lms", "decision": status, "score": float(score) if score is not None else None},
        )

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


# --- Assignments defined in-app ---------------------------------------------

AUDITED_ASSIGNMENT_FIELDS = ["title", "type", "batch", "description", "due_at", "max_score", "is_active"]


def batches_for_actor(actor):
    """Batches an actor may set work for. An instructor sets it for their own."""
    batches = Batch.objects.all()
    if scope_for(actor.role, "assignment") == SCOPE_ALL:
        return batches
    if scope_for(actor.role, "assignment") == SCOPE_ASSIGNED:
        return batches.filter(Q(instructor=actor) | Q(students__instructor=actor)).distinct()
    return batches.none()


def scoped_assignments(actor):
    """
    Assignments an actor may see. An academy-wide assignment (no batch) is
    visible to everyone who can see assignments at all.
    """
    queryset = Assignment.objects.select_related("batch")
    scope = scope_for(actor.role, "assignment")

    if scope == SCOPE_ALL:
        return queryset
    if scope == SCOPE_ASSIGNED:
        return queryset.filter(Q(batch__isnull=True) | Q(batch__in=batches_for_actor(actor)))
    if scope == SCOPE_OWN:
        profile = getattr(actor, "student_profile", None)
        return assignments_for_student(profile) if profile else queryset.none()
    return queryset.none()


def assignments_for_student(student: Student):
    """Active work set for the student's batch, plus anything academy-wide."""
    return Assignment.objects.select_related("batch").filter(
        Q(batch=student.batch) | Q(batch__isnull=True), is_active=True
    )


def _assignment_state(assignment: Assignment) -> dict:
    return {field: getattr(assignment, field) for field in AUDITED_ASSIGNMENT_FIELDS}


@transaction.atomic
def save_assignment(actor, data: dict, assignment: Assignment | None = None) -> Assignment:
    """Creates or edits an assignment. Past attempts are never touched."""
    assert_can(actor.role, "assignment", "update" if assignment else "create")

    batch = data.get("batch")
    allowed = batches_for_actor(actor)
    if batch is None and scope_for(actor.role, "assignment") != SCOPE_ALL:
        raise AssignmentStateError("Only an administrator may set an academy-wide assignment. Choose a batch.")
    if batch is not None and not allowed.filter(pk=batch.pk).exists():
        raise AssignmentStateError(f"You may not set work for batch {batch.code}.")

    before = _assignment_state(assignment) if assignment else None
    if assignment is None:
        assignment = Assignment(**data)
    else:
        for key, value in data.items():
            setattr(assignment, key, value)
    assignment.save()

    changed_before, changed_after = diff_fields(before, _assignment_state(assignment))
    write_audit(
        actor=actor,
        action=AuditAction.UPDATE if before else AuditAction.CREATE,
        entity_type="Assignment",
        entity_id=assignment.pk,
        summary=f'{"Updated" if before else "Created"} assignment "{assignment.title}"',
        before=changed_before or None,
        after=changed_after,
        **current_request_meta(),
    )
    return assignment


# --- Submitting and reviewing -----------------------------------------------


def latest_attempt(assignment: Assignment, student: Student) -> AssignmentSubmission | None:
    return (
        AssignmentSubmission.objects.filter(assignment=assignment, student=student)
        .order_by("-version")
        .first()
    )


def submission_block_reason(assignment: Assignment, student: Student) -> str | None:
    """Why the student may not hand in a new attempt right now, or None."""
    if not assignment.is_active or assignment.deleted_at:
        return "This assignment is closed."
    latest = latest_attempt(assignment, student)
    if latest and latest.status in OPEN_STATUSES:
        return f"Attempt {latest.version} is still {latest.get_status_display().lower()}. Wait for the review."
    if latest and latest.status == SubmissionStatus.APPROVED:
        return f"Attempt {latest.version} was approved. There is nothing more to hand in."
    return None


@transaction.atomic
def submit_assignment(actor, student: Student, assignment: Assignment, *,
                      response_text: str | None = None, submission_url: str | None = None) -> AssignmentSubmission:
    """A student hands in work through the portal. Always a new version."""
    assert_can(actor.role, "assignment", "create")

    if not assignments_for_student(student).filter(pk=assignment.pk).exists():
        raise AssignmentStateError("That assignment is not set for your batch.")

    blocked = submission_block_reason(assignment, student)
    if blocked:
        raise AssignmentStateError(blocked)

    response_text = (response_text or "").strip() or None
    submission_url = (submission_url or "").strip() or None
    if not response_text and not submission_url:
        raise AssignmentStateError("Write a response or add a link to your work.")

    latest = latest_attempt(assignment, student)
    version = (latest.version if latest else 0) + 1
    now = timezone.now()
    if latest:
        AssignmentSubmission.objects.filter(
            assignment=assignment, student=student, superseded_at__isnull=True
        ).update(superseded_at=now)

    status = SubmissionStatus.RESUBMITTED if latest else SubmissionStatus.SUBMITTED
    submission = AssignmentSubmission.objects.create(
        assignment=assignment,
        student=student,
        version=version,
        status=status,
        submitted_at=now,
        response_text=response_text,
        submission_url=submission_url,
    )

    late = submission.is_late
    record_activity(
        student,
        ActivityKind.ASSIGNMENT_SUBMITTED,
        f'{"Resubmitted" if latest else "Submitted"} "{assignment.title}" (attempt {version})'
        + (" after the due date" if late else ""),
        actor=actor,
        target=submission,
        metadata={"source": "portal", "late": late},
    )

    write_audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="AssignmentSubmission",
        entity_id=submission.pk,
        summary=(
            f'"{assignment.title}" attempt {version} submitted by '
            f"{student.full_name} ({student.enrollment_id})" + (" late" if late else "")
        ),
        after={"version": version, "status": status},
        **current_request_meta(),
    )
    return submission


@transaction.atomic
def review_assignment(actor, submission: AssignmentSubmission, *, decision: str,
                      score: Decimal | None = None, feedback: str | None = None,
                      rejection_reason: str | None = None) -> AssignmentSubmission:
    """
    Closes out an attempt. Like research, this is the one and only change an
    attempt ever receives; after it the student resubmits as a new version.
    """
    assert_can(actor.role, "assignment", "review")

    # Re-read under a lock so two evaluators cannot both close the same attempt.
    submission = AssignmentSubmission.objects.select_for_update().select_related(
        "assignment", "student"
    ).get(pk=submission.pk)

    if decision not in REVIEWED_STATUSES:
        raise AssignmentStateError("Choose approve, request revision or reject.")
    if submission.is_closed:
        raise AssignmentStateError(
            f"Attempt {submission.version} has already been reviewed. A reviewed attempt is never edited."
        )
    if submission.superseded_at:
        raise AssignmentStateError(f"Attempt {submission.version} was replaced by a later attempt.")

    max_score = submission.assignment.max_score
    if score is not None:
        if score < 0:
            raise AssignmentStateError("A score cannot be negative.")
        if max_score is not None and score > max_score:
            raise AssignmentStateError(f"The score cannot exceed the maximum of {max_score}.")
    if decision != SubmissionStatus.APPROVED and not (rejection_reason or "").strip():
        raise AssignmentStateError("Give the student a reason when the work is not approved.")

    now = timezone.now()
    previous = submission.status
    submission.status = decision
    submission.score = score
    submission.feedback = feedback or None
    submission.rejection_reason = None if decision == SubmissionStatus.APPROVED else rejection_reason
    submission.reviewed_at = now
    submission.evaluator = actor
    submission.approved_at = now if decision == SubmissionStatus.APPROVED else None
    submission.save()

    title = submission.assignment.title
    record_activity(
        submission.student,
        ActivityKind.ASSIGNMENT_REVIEWED,
        f'"{title}" attempt {submission.version} {submission.get_status_display().lower()}'
        + (f" ({score}" + (f"/{max_score}" if max_score is not None else "") + ")" if score is not None else "")
        + f" by {getattr(actor, 'full_name', None) or 'staff'}",
        actor=actor,
        detail=feedback or rejection_reason,
        target=submission,
        metadata={"decision": decision, "score": float(score) if score is not None else None},
    )

    write_audit(
        actor=actor,
        action=AuditAction.APPROVE if decision == SubmissionStatus.APPROVED else AuditAction.REJECT,
        entity_type="AssignmentSubmission",
        entity_id=submission.pk,
        summary=(
            f'"{title}" attempt {submission.version} for {submission.student.full_name} '
            f"({submission.student.enrollment_id}): {decision}"
        ),
        before={"status": previous},
        after={"status": decision, "score": score},
        **current_request_meta(),
    )
    return submission


# --- Tracking ---------------------------------------------------------------

# One state per student per assignment, as a roster reads it.
ROSTER_APPROVED = "approved"
ROSTER_AWAITING = "awaiting"
ROSTER_RETURNED = "returned"
ROSTER_OVERDUE = "overdue"
ROSTER_MISSING = "missing"


@dataclass
class RosterRow:
    student: Student
    latest: AssignmentSubmission | None
    attempts: int
    state: str


def assignment_roster(actor, assignment: Assignment) -> tuple[list[RosterRow], dict[str, int]]:
    """
    Every student the assignment is set for, with where they stand on it. A
    student who has handed in nothing is a row too — that is the point.
    """
    students = Student.objects.for_actor(actor).tracked().select_related("batch")
    if assignment.batch_id:
        students = students.filter(batch_id=assignment.batch_id)

    attempts: dict = {}
    for s in AssignmentSubmission.objects.filter(assignment=assignment, student__in=students).order_by("version"):
        attempts.setdefault(s.student_id, []).append(s)

    rows = []
    for student in students.order_by("full_name"):
        history = attempts.get(student.pk, [])
        latest = history[-1] if history else None
        if latest is None:
            state = ROSTER_OVERDUE if assignment.is_past_due else ROSTER_MISSING
        elif latest.status == SubmissionStatus.APPROVED:
            state = ROSTER_APPROVED
        elif latest.status in (SubmissionStatus.REJECTED, SubmissionStatus.REVISION_REQUESTED):
            state = ROSTER_RETURNED
        else:
            state = ROSTER_AWAITING
        rows.append(RosterRow(student=student, latest=latest, attempts=len(history), state=state))

    counts = {key: 0 for key in (ROSTER_APPROVED, ROSTER_AWAITING, ROSTER_RETURNED, ROSTER_OVERDUE, ROSTER_MISSING)}
    for row in rows:
        counts[row.state] += 1
    counts["total"] = len(rows)
    counts["submitted"] = counts["total"] - counts[ROSTER_OVERDUE] - counts[ROSTER_MISSING]
    return rows, counts


def student_assignment_board(student: Student) -> list[dict]:
    """Each assignment set for a student, with their latest attempt, for the portal."""
    latest_by_assignment: dict = {}
    for s in AssignmentSubmission.objects.filter(student=student).order_by("version"):
        latest_by_assignment[s.assignment_id] = s

    board = []
    for assignment in assignments_for_student(student).order_by("due_at", "title"):
        latest = latest_by_assignment.get(assignment.pk)
        board.append({
            "assignment": assignment,
            "latest": latest,
            "overdue": latest is None and assignment.is_past_due,
            "can_submit": submission_block_reason(assignment, student) is None,
        })
    return board
