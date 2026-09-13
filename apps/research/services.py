"""
Research submission and evaluation (specification section 5).

The governing rule: an attempt is never edited after review. A resubmission
creates version N+1 and marks the previous one superseded, so the scores and
feedback given on every earlier attempt stay readable forever (spec 23.4).
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.academy.models import Student
from apps.academy.services import change_status, touch_activity
from apps.academy.status import InvalidTransition
from apps.core.audit import write_audit
from apps.core.enums import AuditAction, StudentStatus, SubmissionStatus
from apps.core.middleware import current_request_meta
from apps.core.permissions import assert_can, scope_for, SCOPE_ALL, SCOPE_ASSIGNED, SCOPE_OWN
from apps.research.models import (
    ResearchCriteriaSet,
    ResearchCriterion,
    ResearchCriterionScore,
    ResearchSubmission,
)
from apps.research.scoring import ScoredCriterion, explain_score, score_submission

# Statuses where the attempt is still with the student or the evaluator.
OPEN_STATUSES = [
    SubmissionStatus.DRAFT,
    SubmissionStatus.SUBMITTED,
    SubmissionStatus.UNDER_REVIEW,
    SubmissionStatus.RESUBMITTED,
]

CLOSED_STATUSES = [
    SubmissionStatus.APPROVED,
    SubmissionStatus.REJECTED,
    SubmissionStatus.REVISION_REQUESTED,
]

# Statuses a student may be in when research review completes.
REVIEWABLE_FROM = [
    StudentStatus.ENROLLED,
    StudentStatus.RESEARCH_PENDING,
    StudentStatus.RESEARCH_IN_PROGRESS,
    StudentStatus.REVISION_REQUIRED,
]


class SubmissionStateError(Exception):
    """Raised when an operation would violate the append-only rule."""

    status_code = 422


def scoped_submissions(actor):
    """Row scoping for research, mirroring the student scope."""
    queryset = ResearchSubmission.objects.select_related("student", "student__batch", "evaluator")
    scope = scope_for(actor.role, "research")

    if scope == SCOPE_ALL:
        return queryset.filter(student__deleted_at__isnull=True)
    if scope == SCOPE_ASSIGNED:
        return queryset.filter(student__deleted_at__isnull=True, student__instructor=actor)
    if scope == SCOPE_OWN:
        profile = getattr(actor, "student_profile", None)
        return queryset.filter(student=profile) if profile else queryset.none()
    return queryset.none()


def criteria_for_student(student: Student):
    """
    Criteria in force for a student, via their batch's criteria set. Falls back
    to the default set so a batch with none configured is still reviewable.
    """
    criteria_set = student.batch.criteria_set or ResearchCriteriaSet.objects.filter(is_default=True).first()
    if not criteria_set:
        return None, ResearchCriterion.objects.none()

    criteria = criteria_set.criteria.filter(is_active=True, deleted_at__isnull=True).order_by("display_order")
    return criteria_set, criteria


@transaction.atomic
def submit_research(actor, student: Student, data: dict, competitors: list[dict], as_draft: bool = False):
    """
    Creates a new version. Never updates an existing reviewed attempt.
    An open DRAFT is edited in place, since nobody has seen it.
    """
    assert_can(actor.role, "research", "create")

    existing_draft = (
        ResearchSubmission.objects.filter(student=student, status=SubmissionStatus.DRAFT)
        .order_by("-version")
        .first()
    )
    latest = ResearchSubmission.objects.filter(student=student).order_by("-version").first()

    if latest and not existing_draft and latest.status in OPEN_STATUSES:
        raise SubmissionStateError(
            f"Version {latest.version} is still {latest.get_status_display().lower()}. "
            "Wait for the review before submitting again."
        )

    if as_draft:
        status = SubmissionStatus.DRAFT
    elif latest and not existing_draft:
        status = SubmissionStatus.RESUBMITTED
    else:
        status = SubmissionStatus.SUBMITTED

    fields = {**data, "status": status, "submitted_at": None if as_draft else timezone.now()}

    if existing_draft:
        for key, value in fields.items():
            setattr(existing_draft, key, value)
        existing_draft.save()
        submission = existing_draft
        submission.competitors.all().delete()
    else:
        version = (latest.version if latest else 0) + 1
        if latest:
            ResearchSubmission.objects.filter(
                student=student, superseded_at__isnull=True, version__lt=version
            ).update(superseded_at=timezone.now())
        submission = ResearchSubmission.objects.create(student=student, version=version, **fields)

    for row in competitors:
        submission.competitors.create(**row)

    if not as_draft:
        touch_activity(student)
        # Move the student into review only from a state where that makes sense.
        if student.status in REVIEWABLE_FROM:
            try:
                change_status(
                    actor, student, StudentStatus.ASSIGNMENT_SUBMITTED,
                    reason=f"Research version {submission.version} submitted",
                    # A student may not set their own status, but submitting
                    # their own research does move them along. See change_status.
                    propagated=True,
                )
            except InvalidTransition:
                # The submission is the record that matters; a status that
                # cannot legally advance must not block it.
                pass

    write_audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="ResearchSubmission",
        entity_id=submission.pk,
        summary=(
            f"Research v{submission.version} {status.lower()} for "
            f"{student.full_name} ({student.enrollment_id})"
        ),
        after={"version": submission.version, "status": status},
        **current_request_meta(),
    )
    return submission


@transaction.atomic
def review_research(actor, submission: ResearchSubmission, decision: str,
                    scores: dict[str, float], notes: dict[str, str] | None = None,
                    feedback: str | None = None, rejection_reason: str | None = None):
    """
    Records an evaluation. The attempt is closed out permanently and the
    decision propagates to the student's status.
    """
    assert_can(actor.role, "research", "review")

    if submission.is_closed:
        raise SubmissionStateError(
            f"Version {submission.version} has already been reviewed. Ask the student to resubmit; "
            "a reviewed attempt is never edited."
        )

    notes = notes or {}
    _, criteria = criteria_for_student(submission.student)

    scored = [
        ScoredCriterion(
            criterion_id=str(c.pk),
            name=c.name,
            weightage=float(c.weightage),
            max_score=float(c.max_score),
            min_passing_score=float(c.min_passing_score),
            is_required=c.is_required,
            score=float(scores[str(c.pk)]),
        )
        for c in criteria
        if str(c.pk) in scores
    ]

    result = score_submission(scored)
    now = timezone.now()

    ResearchCriterionScore.objects.filter(submission=submission).delete()
    for detail in result.breakdown:
        ResearchCriterionScore.objects.create(
            submission=submission,
            criterion_id=detail.criterion_id,
            score=detail.score,
            passed=detail.passed,
            evaluator_note=notes.get(detail.criterion_id) or None,
        )

    submission.status = decision
    submission.score = result.total if scored else None
    submission.feedback = feedback
    submission.rejection_reason = None if decision == SubmissionStatus.APPROVED else rejection_reason
    submission.reviewed_at = now
    submission.evaluator = actor
    submission.approved_at = now if decision == SubmissionStatus.APPROVED else None
    submission.save()

    # Propagate to the student. A failure here must not undo the review itself.
    next_status = (
        StudentStatus.RESEARCH_APPROVED
        if decision == SubmissionStatus.APPROVED
        else StudentStatus.REVISION_REQUIRED
    )
    try:
        change_status(
            actor, submission.student, next_status,
            reason=(
                f"Research v{submission.version} approved"
                + (f" ({result.total}%)" if scored else "")
                if decision == SubmissionStatus.APPROVED
                else f"Research v{submission.version} "
                + ("rejected" if decision == SubmissionStatus.REJECTED else "sent back for revision")
            ),
        )
    except InvalidTransition:
        pass

    explanation = explain_score(result)

    write_audit(
        actor=actor,
        action=AuditAction.APPROVE if decision == SubmissionStatus.APPROVED else AuditAction.REJECT,
        entity_type="ResearchSubmission",
        entity_id=submission.pk,
        summary=(
            f"Research v{submission.version} for {submission.student.full_name} "
            f"({submission.student.enrollment_id}): {decision}. {explanation}"
        ),
        before={"status": SubmissionStatus.UNDER_REVIEW},
        after={"status": decision, "score": result.total if scored else None},
        **current_request_meta(),
    )

    return result, explanation


def upsert_criterion(actor, criteria_set: ResearchCriteriaSet, data: dict,
                     criterion: ResearchCriterion | None = None) -> ResearchCriterion:
    assert_can(actor.role, "research", "configure")

    if criterion:
        before = {
            "weightage": float(criterion.weightage),
            "min_passing_score": float(criterion.min_passing_score),
            "is_active": criterion.is_active,
        }
        for key, value in data.items():
            setattr(criterion, key, value)
        criterion.save()
        action, verb = AuditAction.UPDATE, "Updated"
    else:
        criterion = ResearchCriterion.objects.create(criteria_set=criteria_set, **data)
        before, action, verb = None, AuditAction.CREATE, "Added"

    write_audit(
        actor=actor,
        action=action,
        entity_type="ResearchCriterion",
        entity_id=criterion.pk,
        summary=f'{verb} research criterion "{criterion.name}"',
        before=before,
        after={
            "weightage": float(criterion.weightage),
            "min_passing_score": float(criterion.min_passing_score),
            "is_active": criterion.is_active,
        },
        **current_request_meta(),
    )
    return criterion


def archive_criterion(actor, criterion: ResearchCriterion) -> None:
    """Soft delete: past scores reference this row and must stay readable."""
    assert_can(actor.role, "research", "configure")

    criterion.is_active = False
    criterion.deleted_at = timezone.now()
    criterion.save(update_fields=["is_active", "deleted_at", "updated_at"])

    write_audit(
        actor=actor,
        action=AuditAction.DELETE,
        entity_type="ResearchCriterion",
        entity_id=criterion.pk,
        summary=f'Retired research criterion "{criterion.name}"',
        **current_request_meta(),
    )


def review_queue_counts(actor) -> dict[str, int]:
    base = scoped_submissions(actor)
    return {
        "awaiting": base.filter(status__in=[
            SubmissionStatus.SUBMITTED, SubmissionStatus.UNDER_REVIEW, SubmissionStatus.RESUBMITTED
        ]).count(),
        "approved": base.filter(status=SubmissionStatus.APPROVED).count(),
        "returned": base.filter(status__in=[
            SubmissionStatus.REJECTED, SubmissionStatus.REVISION_REQUESTED
        ]).count(),
    }
