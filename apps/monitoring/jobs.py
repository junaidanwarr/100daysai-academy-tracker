"""
Background job handlers.

Plain functions that know nothing about django-q2 or HTTP. The scheduler
invokes these; ``/api/cron/<job>/`` invokes the same functions behind a shared
secret. There is one implementation, so the two paths cannot drift.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from apps.academy.models import Student, StudentStatusHistory
from apps.academy.status import ARCHIVED_STATUSES, STATUS_TO_STAGE
from apps.core.audit import write_audit
from apps.core.context import system_actor
from apps.core.enums import AuditAction, StudentStatus
from apps.core.settings_service import INACTIVITY_DAYS, effective_research_days, get_int
from apps.monitoring.services import dispatch_pending, run_alert_scan

logger = logging.getLogger(__name__)


def recalculate_deadlines() -> dict:
    """
    Recomputes research deadlines from each student's research start date and
    the window in force for their batch.

    This is what makes the window genuinely configurable: changing
    `research_days` from 15 to 21 re-dates every student who has not been given
    an individual extension, rather than leaving stale deadlines behind.
    """
    students = (
        Student.objects.filter(research_start_date__isnull=False)
        .exclude(status__in=ARCHIVED_STATUSES)
        .select_related("batch")
    )

    examined = updated = 0
    for student in students:
        examined += 1
        days = effective_research_days(student.batch.research_days)
        expected = student.research_start_date + timedelta(days=days)
        if student.research_deadline != expected:
            student.research_deadline = expected
            student.save(update_fields=["research_deadline", "updated_at"])
            updated += 1

    return {"examined": examined, "updated": updated}


def deadline_scan() -> dict:
    """
    The scheduled monitoring pass from specification section 7: refresh
    deadlines, then evaluate every alert rule against current state.
    """
    deadlines = recalculate_deadlines()
    scan = run_alert_scan()
    return {"deadlines": deadlines, "scan": scan.as_dict()}


def dispatch_notifications() -> dict:
    return dispatch_pending(200)


def flag_inactive_students() -> dict:
    """
    Marks students inactive when they pass the configured window. Kept separate
    from alerting: the alert tells staff, this changes the record.
    """
    days = get_int(INACTIVITY_DAYS)
    cutoff = timezone.now() - timedelta(days=days)

    candidates = Student.objects.filter(
        status__in=[StudentStatus.ACTIVE, StudentStatus.CONTENT_PRODUCTION_STARTED]
    ).filter(last_activity_at__lt=cutoff)

    flagged = 0
    for student in candidates:
        StudentStatusHistory.objects.create(
            student=student,
            from_status=student.status,
            to_status=StudentStatus.INACTIVE,
            from_stage=student.roadmap_stage,
            to_stage=STATUS_TO_STAGE[StudentStatus.INACTIVE],
            reason=f"No recorded activity for {days} days.",
            is_automated=True,
        )
        student.status = StudentStatus.INACTIVE
        student.roadmap_stage = STATUS_TO_STAGE[StudentStatus.INACTIVE]
        student.save(update_fields=["status", "roadmap_stage", "updated_at"])
        flagged += 1

    if flagged:
        write_audit(
            actor=system_actor("inactivity-scan"),
            action=AuditAction.UPDATE,
            entity_type="Student",
            summary=f"Flagged {flagged} student(s) inactive after {days} days.",
        )

    return {"inactivity_days": days, "flagged": flagged}


def prune_sessions() -> dict:
    """Removes expired sessions so the table does not grow forever."""
    from django.contrib.sessions.models import Session

    deleted, _ = Session.objects.filter(expire_date__lt=timezone.now()).delete()
    return {"deleted": deleted}


def youtube_sync() -> dict:
    """
    The scheduled YouTube pass.

    Runs every 12 hours by default, but only touches channels whose configured
    cadence says they are due, so changing `youtube_sync_cadence` from the
    settings screen takes effect without rescheduling anything. Reports
    "skipped" with a reason rather than pretending to have synced.
    """
    from apps.youtube.services import SyncNotConfigured, cadence, sync_all  # noqa: PLC0415

    try:
        result = sync_all()
    except SyncNotConfigured as exc:
        return {"skipped": True, "reason": str(exc)}

    payload = result.as_dict()
    payload["cadence"] = cadence()
    if payload["considered"] == 0:
        payload["reason"] = (
            "No channel was due this cycle."
            if payload["cadence"] != "MANUAL"
            else "Synchronisation is set to manual only."
        )
    return payload


def run_named_job(name: str) -> dict:
    """
    Entry point for the scheduler. django-q2 stores a dotted path plus
    arguments, so schedules reference this one function by job name rather than
    needing a separate importable per job.
    """
    handler = JOBS.get(name)
    if not handler:
        raise ValueError(f'Unknown job "{name}". Available: {", ".join(JOBS)}')
    result = handler()
    logger.info("job %s finished: %s", name, result)
    return result


JOBS = {
    "deadline-scan": deadline_scan,
    "recalculate-deadlines": recalculate_deadlines,
    "dispatch-notifications": dispatch_notifications,
    "flag-inactive": flag_inactive_students,
    "prune-sessions": prune_sessions,
    "youtube-sync": youtube_sync,
}

# Default cadences, registered as django-q2 schedules by `manage.py setup_schedules`.
JOB_SCHEDULES = {
    "deadline-scan": ("H", 6),           # every 6 hours
    "recalculate-deadlines": ("D", 1),   # daily
    "dispatch-notifications": ("I", 5),  # every 5 minutes
    "flag-inactive": ("D", 1),
    "prune-sessions": ("W", 1),
    "youtube-sync": ("H", 12),
}
