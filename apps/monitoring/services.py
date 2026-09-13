"""
Alert generation, triage and notification dispatch.

A scan evaluates every active rule, deduplicates against recent alerts, then
writes new ones and queues notifications. Deduplication matters more than it
looks: without it a rule that runs hourly would generate 24 identical "deadline
missed" alerts a day and staff would stop reading the inbox.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import write_audit
from apps.core.context import system_actor
from apps.core.enums import (
    AlertStatus,
    AuditAction,
    NotificationChannel,
    NotificationDeliveryStatus,
    UserRole,
)
from apps.core.permissions import assert_can
from apps.core.settings_service import (
    CHANNEL_CREATION_DAYS,
    FIRST_VIDEO_DAYS,
    INACTIVITY_DAYS,
    MAX_REJECTIONS_BEFORE_ALERT,
    NO_UPLOAD_DAYS,
    RESEARCH_DAYS,
    RESEARCH_WARNING_DAYS,
    get_int,
)
from apps.monitoring.adapters import ADAPTERS
from apps.monitoring.evaluators import EVALUATORS, AlertCandidate, EvaluatorContext
from apps.monitoring.models import Alert, AlertRule, Notification

logger = logging.getLogger(__name__)

OPEN_STATUSES = [AlertStatus.NEW, AlertStatus.IN_PROGRESS, AlertStatus.ESCALATED]


@dataclass
class ScanResult:
    rules_evaluated: int = 0
    candidates: int = 0
    created: int = 0
    suppressed: int = 0
    notifications_queued: int = 0
    errors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "rules_evaluated": self.rules_evaluated,
            "candidates": self.candidates,
            "created": self.created,
            "suppressed": self.suppressed,
            "notifications_queued": self.notifications_queued,
            "errors": self.errors,
        }


def _dedupe_key(rule_key: str, candidate: AlertCandidate) -> str:
    suffix = f":{candidate.dedupe_suffix}" if candidate.dedupe_suffix else ""
    return f"{rule_key}:{candidate.student_id}{suffix}"


def run_alert_scan() -> ScanResult:
    """
    Runs every active alert rule. Safe to call repeatedly — that is the point of
    the cooldown window.
    """
    now = timezone.now()
    ctx = EvaluatorContext(
        today=timezone.localdate(),
        now=now,
        defaults={
            "research_days": get_int(RESEARCH_DAYS),
            "research_warning_days": get_int(RESEARCH_WARNING_DAYS),
            "inactivity_days": get_int(INACTIVITY_DAYS),
            "max_rejections": get_int(MAX_REJECTIONS_BEFORE_ALERT),
            "channel_creation_days": get_int(CHANNEL_CREATION_DAYS),
            "first_video_days": get_int(FIRST_VIDEO_DAYS),
            "no_upload_days": get_int(NO_UPLOAD_DAYS),
        },
    )

    result = ScanResult()
    admin_ids = list(
        User.objects.filter(role=UserRole.SUPER_ADMIN, is_active=True, deleted_at__isnull=True).values_list("id", flat=True)
    )

    for rule in AlertRule.objects.filter(is_active=True):
        evaluator = EVALUATORS.get(rule.evaluator_key)
        if not evaluator:
            result.errors.append({"rule": rule.key, "message": f'No evaluator named "{rule.evaluator_key}".'})
            continue

        result.rules_evaluated += 1

        try:
            candidates = evaluator(ctx, rule.params)
        except Exception as exc:  # noqa: BLE001
            # One broken rule must not abort the whole scan.
            logger.exception("Alert rule %s failed", rule.key)
            result.errors.append({"rule": rule.key, "message": str(exc)[:300]})
            continue

        result.candidates += len(candidates)
        cooldown_since = now - timedelta(hours=rule.cooldown_hours)

        for candidate in candidates:
            key = _dedupe_key(rule.key, candidate)

            # Suppress if the same problem fired recently, or is still open.
            recent = Alert.objects.filter(dedupe_key=key).filter(
                Q(detected_at__gte=cooldown_since) | Q(status__in=OPEN_STATUSES)
            ).exists()

            if recent:
                result.suppressed += 1
                continue

            alert = Alert.objects.create(
                rule=rule,
                student_id=candidate.student_id,
                batch_id=candidate.batch_id,
                title=candidate.title,
                problem=candidate.problem,
                priority=candidate.priority or rule.priority,
                assigned_to_id=candidate.assigned_to_id,
                recommended_action=rule.recommended_action,
                detected_at=now,
                dedupe_key=key,
                context=candidate.context,
            )
            result.created += 1

            # Notify the assigned instructor, or every admin when unassigned.
            recipients = [candidate.assigned_to_id] if candidate.assigned_to_id else admin_ids
            for user_id in recipients:
                for channel in rule.channels or [NotificationChannel.IN_APP]:
                    Notification.objects.create(
                        user_id=user_id,
                        alert=alert,
                        channel=channel,
                        title=alert.title,
                        body=alert.problem,
                        link_url=f"/alerts/{alert.pk}/",
                    )
                    result.notifications_queued += 1

    write_audit(
        actor=system_actor("alert-scan"),
        action=AuditAction.CREATE,
        entity_type="AlertScan",
        summary=(
            f"Alert scan: {result.created} created, {result.suppressed} suppressed, "
            f"{result.rules_evaluated} rule(s) evaluated"
            + (f", {len(result.errors)} error(s)" if result.errors else "")
            + "."
        ),
        after=result.as_dict(),
    )

    return result


def dispatch_pending(limit: int = 200) -> dict:
    """
    Delivers pending notifications. Called by the scheduled job and by
    /api/cron/dispatch-notifications.
    """
    pending = Notification.objects.filter(delivery_status=NotificationDeliveryStatus.PENDING).select_related("user")[:limit]

    processed = sent = failed = skipped = 0

    for notification in pending:
        processed += 1
        adapter = ADAPTERS.get(notification.channel)
        user = notification.user

        # Route to the right destination. A missing phone number is a skip, not
        # a failure — there is nothing to retry.
        if notification.channel in (NotificationChannel.SMS, NotificationChannel.WHATSAPP):
            destination = user.phone
        else:
            destination = user.email

        if not user.is_active:
            outcome_status, detail, message_id = NotificationDeliveryStatus.SKIPPED, "Recipient account is inactive.", None
        elif not destination:
            outcome_status, detail, message_id = (
                NotificationDeliveryStatus.SKIPPED,
                f"No {notification.get_channel_display().lower()} address on file.",
                None,
            )
        elif adapter is None:
            outcome_status, detail, message_id = NotificationDeliveryStatus.SKIPPED, "No adapter for this channel.", None
        else:
            outcome = adapter.send(destination, notification.title, notification.body, notification.link_url)
            outcome_status, detail, message_id = outcome.status, outcome.detail, outcome.provider_message_id

        notification.delivery_status = outcome_status
        notification.delivery_detail = detail
        notification.provider_message_id = message_id
        notification.sent_at = timezone.now() if outcome_status == NotificationDeliveryStatus.SENT else None
        notification.save(update_fields=["delivery_status", "delivery_detail", "provider_message_id", "sent_at"])

        if outcome_status == NotificationDeliveryStatus.SENT:
            sent += 1
        elif outcome_status == NotificationDeliveryStatus.FAILED:
            failed += 1
        else:
            skipped += 1

    return {"processed": processed, "sent": sent, "failed": failed, "skipped": skipped}


def update_alert(actor, alert: Alert, *, status=None, assigned_to=None, priority=None, resolution_note=None) -> Alert:
    assert_can(actor.role, "alert", "update")

    before = {"status": alert.status, "assigned_to": str(alert.assigned_to_id), "priority": alert.priority}
    closing = status in (AlertStatus.RESOLVED, AlertStatus.IGNORED)

    if status:
        alert.status = status
    if assigned_to is not None:
        alert.assigned_to = assigned_to
    if priority:
        alert.priority = priority
    if resolution_note:
        alert.resolution_note = resolution_note
    if closing:
        alert.resolved_by = actor
        alert.resolved_at = timezone.now()

    alert.save()

    write_audit(
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="Alert",
        entity_id=alert.pk,
        summary=f'Alert "{alert.title}" set to {alert.status}',
        before=before,
        after={"status": alert.status, "assigned_to": str(alert.assigned_to_id), "priority": alert.priority},
    )
    return alert
