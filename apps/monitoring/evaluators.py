"""
Alert condition evaluators (specification section 7).

Each evaluator is a named, parameterised query. An AlertRule row stores
``{"evaluator": "STUDENT_INACTIVE", "params": {"inactive_days": 7}}``, so
thresholds are tuned from the settings screen and new rules are added without
touching this file — only genuinely new *kinds* of check need code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from django.db.models import Count, Q
from django.utils import timezone

from apps.academy.models import Student
from apps.academy.status import ARCHIVED_STATUSES, RESEARCH_PENDING_STATUSES
from apps.core.enums import AlertPriority, ChannelStatus, SubmissionStatus
from apps.youtube.models import ChannelOauthGrant, YoutubeChannel


@dataclass
class EvaluatorContext:
    today: date
    now: Any
    defaults: dict[str, int]


@dataclass
class AlertCandidate:
    student_id: Any
    batch_id: Any
    title: str
    problem: str
    assigned_to_id: Any = None
    # Rule priority is used unless an evaluator escalates a specific case.
    priority: str | None = None
    context: dict = field(default_factory=dict)
    dedupe_suffix: str = ""


Evaluator = Callable[[EvaluatorContext, dict], list[AlertCandidate]]

ACTIVE_STUDENTS = Q(deleted_at__isnull=True) & ~Q(status__in=ARCHIVED_STATUSES)


def _num(params: dict, key: str, fallback: int) -> int:
    try:
        value = int(params.get(key, fallback))
        return value if value >= 0 else fallback
    except (TypeError, ValueError):
        return fallback


def research_deadline_missed(ctx: EvaluatorContext, params: dict) -> list[AlertCandidate]:
    """The 15-day check from specification section 7, generalised."""
    grace = _num(params, "grace_days", 0)
    cutoff = ctx.today - timedelta(days=grace)

    students = (
        Student.objects.filter(ACTIVE_STUDENTS)
        .filter(research_deadline__lt=cutoff, status__in=RESEARCH_PENDING_STATUSES)
        .annotate(submission_count=Count("research_submissions"))
    )

    out = []
    for s in students:
        days_late = (ctx.today - s.research_deadline).days
        out.append(
            AlertCandidate(
                student_id=s.pk,
                batch_id=s.batch_id,
                title="Research deadline missed",
                problem=(
                    f"{s.full_name} ({s.enrollment_id}) passed the research deadline {days_late} day(s) ago "
                    f"with {s.submission_count} submission(s) on record and status {s.status}."
                ),
                # Two weeks past the deadline is a different problem from one day past.
                priority=AlertPriority.CRITICAL if days_late >= 7 else None,
                assigned_to_id=s.instructor_id,
                context={"days_late": days_late, "deadline": str(s.research_deadline)},
            )
        )
    return out


def research_deadline_approaching(ctx: EvaluatorContext, params: dict) -> list[AlertCandidate]:
    warning = _num(params, "warning_days", ctx.defaults["research_warning_days"])

    students = Student.objects.filter(ACTIVE_STUDENTS).filter(
        research_deadline__gte=ctx.today,
        research_deadline__lte=ctx.today + timedelta(days=warning),
        status__in=RESEARCH_PENDING_STATUSES,
    )

    return [
        AlertCandidate(
            student_id=s.pk,
            batch_id=s.batch_id,
            title="Research deadline approaching",
            problem=(
                f"{s.full_name} ({s.enrollment_id}) has {(s.research_deadline - ctx.today).days} day(s) "
                "left to submit research."
            ),
            assigned_to_id=s.instructor_id,
            context={"deadline": str(s.research_deadline)},
        )
        for s in students
    ]


def assignment_not_submitted(ctx: EvaluatorContext, params: dict) -> list[AlertCandidate]:
    after_days = _num(params, "after_days", ctx.defaults["research_days"])

    students = (
        Student.objects.filter(ACTIVE_STUDENTS)
        .filter(research_start_date__lt=ctx.today - timedelta(days=after_days))
        .exclude(research_submissions__status__in=[SubmissionStatus.SUBMITTED, SubmissionStatus.UNDER_REVIEW,
                                                   SubmissionStatus.RESUBMITTED, SubmissionStatus.APPROVED,
                                                   SubmissionStatus.REJECTED, SubmissionStatus.REVISION_REQUESTED])
    )

    return [
        AlertCandidate(
            student_id=s.pk,
            batch_id=s.batch_id,
            title="Assignment not submitted",
            problem=(
                f"{s.full_name} ({s.enrollment_id}) started research "
                f"{(ctx.today - s.research_start_date).days} day(s) ago and has not submitted anything yet."
            ),
            assigned_to_id=s.instructor_id,
            context={"threshold_days": after_days},
        )
        for s in students
    ]


def rejected_repeatedly(ctx: EvaluatorContext, params: dict) -> list[AlertCandidate]:
    threshold = _num(params, "max_rejections", ctx.defaults["max_rejections"])

    students = (
        Student.objects.filter(ACTIVE_STUDENTS)
        .annotate(
            rejections=Count(
                "research_submissions",
                filter=Q(research_submissions__status__in=[SubmissionStatus.REJECTED, SubmissionStatus.REVISION_REQUESTED]),
            )
        )
        .filter(rejections__gt=threshold)
    )

    return [
        AlertCandidate(
            student_id=s.pk,
            batch_id=s.batch_id,
            title="Assignment rejected repeatedly",
            problem=(
                f"{s.full_name} ({s.enrollment_id}) has had research rejected {s.rejections} time(s), "
                f"above the threshold of {threshold}. Consider a one-to-one review."
            ),
            priority=AlertPriority.HIGH,
            assigned_to_id=s.instructor_id,
            context={"rejections": s.rejections, "threshold": threshold},
        )
        for s in students
    ]


def approved_but_no_channel(ctx: EvaluatorContext, params: dict) -> list[AlertCandidate]:
    within = _num(params, "within_days", ctx.defaults["channel_creation_days"])
    cutoff = ctx.now - timedelta(days=within)

    students = (
        Student.objects.filter(ACTIVE_STUDENTS)
        .filter(
            status__in=["RESEARCH_APPROVED", "CHANNEL_CREATION_PENDING"],
            research_submissions__status=SubmissionStatus.APPROVED,
            research_submissions__approved_at__lt=cutoff,
        )
        .annotate(channel_count=Count("channels", filter=Q(channels__deleted_at__isnull=True)))
        .filter(channel_count=0)
        .distinct()
    )

    return [
        AlertCandidate(
            student_id=s.pk,
            batch_id=s.batch_id,
            title="Research approved but no channel created",
            problem=(
                f"{s.full_name} ({s.enrollment_id}) had research approved more than {within} day(s) ago "
                "but no channel has been recorded."
            ),
            assigned_to_id=s.instructor_id,
            context={"target_days": within},
        )
        for s in students
    ]


def channel_without_uploads(ctx: EvaluatorContext, params: dict) -> list[AlertCandidate]:
    within = _num(params, "within_days", ctx.defaults["first_video_days"])

    channels = YoutubeChannel.objects.filter(
        last_upload_at__isnull=True,
        created_at__lt=ctx.now - timedelta(days=within),
        student__deleted_at__isnull=True,
    ).exclude(
        status__in=[ChannelStatus.TERMINATED, ChannelStatus.ABANDONED, ChannelStatus.COMPLETED, ChannelStatus.SUSPENDED]
    ).exclude(student__status__in=ARCHIVED_STATUSES).select_related("student")

    return [
        AlertCandidate(
            student_id=c.student_id,
            batch_id=c.student.batch_id,
            title="Channel created but no videos uploaded",
            problem=(
                f'"{c.channel_name}" ({c.student.full_name}, {c.student.enrollment_id}) was recorded '
                f"{(ctx.now - c.created_at).days} day(s) ago with no uploads. Target is {within} day(s)."
            ),
            assigned_to_id=c.student.instructor_id,
            context={"channel_id": str(c.pk), "channel_name": c.channel_name, "target_days": within},
            dedupe_suffix=str(c.pk),
        )
        for c in channels
    ]


def upload_target_missed(ctx: EvaluatorContext, params: dict) -> list[AlertCandidate]:
    limit = _num(params, "no_upload_days", ctx.defaults["no_upload_days"])

    channels = (
        YoutubeChannel.objects.filter(
            status__in=[ChannelStatus.ACTIVE, ChannelStatus.APPROVED, ChannelStatus.MONETIZED],
            last_upload_at__lt=ctx.now - timedelta(days=limit),
            student__deleted_at__isnull=True,
        )
        .exclude(student__status__in=ARCHIVED_STATUSES)
        .select_related("student")
    )

    return [
        AlertCandidate(
            student_id=c.student_id,
            batch_id=c.student.batch_id,
            title="Upload target missed",
            problem=(
                f'"{c.channel_name}" ({c.student.enrollment_id}) last published '
                f"{(ctx.now - c.last_upload_at).days} day(s) ago, beyond the {limit}-day limit."
            ),
            assigned_to_id=c.student.instructor_id,
            context={"channel_id": str(c.pk), "limit_days": limit},
            dedupe_suffix=str(c.pk),
        )
        for c in channels
    ]


def student_inactive(ctx: EvaluatorContext, params: dict) -> list[AlertCandidate]:
    days = _num(params, "inactive_days", ctx.defaults["inactivity_days"])
    cutoff = ctx.now - timedelta(days=days)

    students = Student.objects.filter(ACTIVE_STUDENTS).filter(
        Q(last_activity_at__lt=cutoff)
        | Q(last_activity_at__isnull=True, enrollment_date__lt=ctx.today - timedelta(days=days))
    )

    out = []
    for s in students:
        since = s.last_activity_at.date() if s.last_activity_at else s.enrollment_date
        out.append(
            AlertCandidate(
                student_id=s.pk,
                batch_id=s.batch_id,
                title="Student inactive",
                problem=(
                    f"{s.full_name} ({s.enrollment_id}) has shown no recorded activity for "
                    f"{(ctx.today - since).days} day(s) (limit {days})."
                ),
                assigned_to_id=s.instructor_id,
                context={"limit_days": days},
            )
        )
    return out


def analytics_disconnected(ctx: EvaluatorContext, params: dict) -> list[AlertCandidate]:
    """
    A revoked or expired OAuth grant silently stops private analytics flowing,
    so it needs to surface as an alert rather than as quietly missing data.
    """
    grants = (
        ChannelOauthGrant.objects.filter(
            Q(revoked_at__isnull=False) | Q(access_token_expires_at__lt=ctx.now - timedelta(days=2)),
            channel__deleted_at__isnull=True,
            channel__student__deleted_at__isnull=True,
        )
        .exclude(channel__student__status__in=ARCHIVED_STATUSES)
        .select_related("channel", "channel__student")
    )

    return [
        AlertCandidate(
            student_id=g.channel.student_id,
            batch_id=g.channel.student.batch_id,
            title="YouTube Analytics disconnected" if g.revoked_at else "YouTube OAuth token expired",
            problem=(
                f'"{g.channel.channel_name}" ({g.channel.student.enrollment_id}) can no longer supply private '
                "analytics. Ask the student to reconnect their Google account."
            ),
            assigned_to_id=g.channel.student.instructor_id,
            context={"channel_id": str(g.channel_id)},
            dedupe_suffix=str(g.channel_id),
        )
        for g in grants
    ]


def agreement_unsigned(ctx: EvaluatorContext, params: dict) -> list[AlertCandidate]:
    after = _num(params, "after_days", 7)

    students = Student.objects.filter(
        deleted_at__isnull=True,
        agreement_status__in=["ISSUED", "PENDING_SIGNATURE"],
        completion_records__issued_at__lt=ctx.now - timedelta(days=after),
    ).distinct()

    return [
        AlertCandidate(
            student_id=s.pk,
            batch_id=s.batch_id,
            title="Final agreement not signed",
            problem=(
                f"{s.full_name} ({s.enrollment_id}) was issued a completion document more than {after} day(s) "
                f"ago and it is still {s.agreement_status}."
            ),
            assigned_to_id=s.instructor_id,
            context={"after_days": after},
        )
        for s in students
    ]


EVALUATORS: dict[str, Evaluator] = {
    "RESEARCH_DEADLINE_MISSED": research_deadline_missed,
    "RESEARCH_DEADLINE_APPROACHING": research_deadline_approaching,
    "ASSIGNMENT_NOT_SUBMITTED": assignment_not_submitted,
    "ASSIGNMENT_REJECTED_REPEATEDLY": rejected_repeatedly,
    "APPROVED_BUT_NO_CHANNEL": approved_but_no_channel,
    "CHANNEL_WITHOUT_UPLOADS": channel_without_uploads,
    "UPLOAD_TARGET_MISSED": upload_target_missed,
    "STUDENT_INACTIVE": student_inactive,
    "ANALYTICS_DISCONNECTED": analytics_disconnected,
    "AGREEMENT_UNSIGNED": agreement_unsigned,
}

EVALUATOR_KEYS = list(EVALUATORS)
