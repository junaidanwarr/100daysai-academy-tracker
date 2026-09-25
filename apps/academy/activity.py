"""
The student activity log.

One function writes to it, ``record_activity``, and every service that performs
an action a student or their instructor would want to see calls it. Nothing is
inferred after the fact: an entry exists because the action did.

"Last activity" is derived from the same call, and only from the student's own
actions (see ``STUDENT_ACTIVITY_KINDS``). A review, a status change or a staff
note is logged, but it does not make an absent student look engaged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, F, Q
from django.utils import timezone

from apps.academy.models import Student, StudentActivity
from apps.core.audit import write_audit
from apps.core.enums import (
    MANUAL_ACTIVITY_KINDS,
    STAFF_ONLY_ACTIVITY_KINDS,
    STUDENT_ACTIVITY_KINDS,
    ActivityKind,
    AuditAction,
)
from apps.core.middleware import current_request_meta
from apps.core.permissions import assert_can


def record_activity(
    student: Student,
    kind: str,
    summary: str,
    *,
    actor=None,
    detail: str | None = None,
    target=None,
    occurred_at=None,
    metadata: dict | None = None,
    dedupe: bool = False,
) -> StudentActivity | None:
    """
    Appends one entry. With ``dedupe`` an entry of the same kind about the same
    target is not written twice, which is what makes a re-run sync or a backfill
    safe to repeat. Returns None when deduplicated.
    """
    target_type = type(target).__name__ if target is not None else None
    target_id = str(target.pk) if target is not None else None

    if dedupe and target is not None and StudentActivity.objects.filter(
        student=student, kind=kind, target_type=target_type, target_id=target_id
    ).exists():
        return None

    when = occurred_at or timezone.now()
    is_student_action = kind in STUDENT_ACTIVITY_KINDS

    activity = StudentActivity.objects.create(
        student=student,
        kind=kind,
        summary=summary[:300],
        detail=detail or None,
        # A system identity has no row to point at; the entry is then "System".
        actor=actor if getattr(actor, "pk", None) else None,
        is_student_action=is_student_action,
        occurred_at=when,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
    )

    if is_student_action:
        # Only ever moves forward: backfilling an old upload must not make a
        # student who signed in this morning look idle since last month.
        Student.all_objects.filter(pk=student.pk).filter(
            Q(last_activity_at__isnull=True) | Q(last_activity_at__lt=when)
        ).update(last_activity_at=when)

    return activity


def log_manual_activity(actor, student: Student, *, kind: str, summary: str,
                        detail: str | None = None, occurred_at=None) -> StudentActivity:
    """Attendance, mentoring, contact and notes, recorded by staff."""
    assert_can(actor.role, "student", "update")
    if kind not in MANUAL_ACTIVITY_KINDS:
        raise ValueError(f"{kind} is recorded automatically and cannot be entered by hand.")

    activity = record_activity(
        student, kind, summary, actor=actor, detail=detail, occurred_at=occurred_at,
        metadata={"manual": True},
    )

    write_audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="StudentActivity",
        entity_id=activity.pk,
        summary=f"Logged {activity.get_kind_display().lower()} for {student.full_name} ({student.enrollment_id})",
        after={"kind": kind, "occurred_at": activity.occurred_at},
        **current_request_meta(),
    )
    return activity


# --- Reading ----------------------------------------------------------------


def scoped_activities(actor):
    """Row scoping follows the student scope exactly."""
    assert_can(actor.role, "student", "read")
    return StudentActivity.objects.filter(
        student__in=Student.objects.for_actor(actor)
    ).select_related("student", "student__batch", "actor")


def portal_activities(student: Student):
    """What a student sees of their own log: everything but internal notes."""
    return student.activities.exclude(kind__in=STAFF_ONLY_ACTIVITY_KINDS).select_related("actor")


def filtered_activities(actor, *, student_id=None, batch_id=None, kind=None, days=None,
                        search=None, actor_filter=None):
    queryset = scoped_activities(actor)
    if student_id:
        queryset = queryset.filter(student_id=student_id)
    if batch_id:
        queryset = queryset.filter(student__batch_id=batch_id)
    if kind:
        queryset = queryset.filter(kind=kind)
    if days:
        queryset = queryset.filter(occurred_at__gte=timezone.now() - timedelta(days=days))
    if search:
        queryset = queryset.filter(
            Q(student__full_name__icontains=search)
            | Q(student__enrollment_id__icontains=search)
            | Q(summary__icontains=search)
        )
    if actor_filter == "student":
        queryset = queryset.filter(is_student_action=True)
    elif actor_filter == "staff":
        queryset = queryset.filter(is_student_action=False)
    return queryset


@dataclass
class ActivitySummary:
    days: int
    total: int
    student_actions: int
    active_students: int
    tracked_students: int
    by_kind: list[tuple[str, str, int]]
    most_active: list
    quiet: list

    @property
    def quiet_count(self) -> int:
        return self.tracked_students - self.active_students


def activity_summary(actor, *, days: int = 7, batch_id=None) -> ActivitySummary:
    """
    Who is doing what over a window. ``quiet`` lists tracked students with no
    action of their own in the window, oldest last-activity first — the people
    an instructor should contact.
    """
    since = timezone.now() - timedelta(days=days)

    students = Student.objects.for_actor(actor).tracked()
    if batch_id:
        students = students.filter(batch_id=batch_id)

    window = scoped_activities(actor).filter(occurred_at__gte=since, student__in=students)
    counts = dict(window.values_list("kind").annotate(n=Count("id")).values_list("kind", "n"))
    labels = dict(ActivityKind.choices)

    own = Count("activities", filter=Q(activities__occurred_at__gte=since, activities__is_student_action=True))
    ranked = students.annotate(action_count=own)

    return ActivitySummary(
        days=days,
        total=sum(counts.values()),
        student_actions=window.filter(is_student_action=True).count(),
        active_students=ranked.filter(action_count__gt=0).count(),
        tracked_students=students.count(),
        by_kind=sorted(
            ((kind, labels.get(kind, kind), n) for kind, n in counts.items()),
            key=lambda row: -row[2],
        ),
        most_active=list(ranked.filter(action_count__gt=0).order_by("-action_count", "full_name")[:5]),
        quiet=list(
            ranked.filter(action_count=0)
            .select_related("batch")
            .order_by(F("last_activity_at").asc(nulls_first=True), "full_name")[:10]
        ),
    )
