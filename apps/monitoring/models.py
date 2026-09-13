"""Alerts, notifications and performance scoring (specification sections 7, 11, 12, 14)."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.enums import (
    AlertPriority,
    AlertStatus,
    NotificationChannel,
    NotificationDeliveryStatus,
    PerformanceBand,
    TargetComparator,
    TargetScope,
    TargetStatus,
)


class AlertRule(models.Model):
    """
    Admin-configurable rules. `condition` holds the evaluator key plus its
    thresholds, so tuning "inactive for N days" is a settings edit and adding a
    rule that reuses an existing evaluator is a database row.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(null=True, blank=True)
    # {"evaluator": "RESEARCH_DEADLINE_MISSED", "params": {"grace_days": 0}}
    condition = models.JSONField()
    priority = models.CharField(max_length=16, choices=AlertPriority.choices, default=AlertPriority.MEDIUM)
    # Delivery channels to attempt, in order.
    channels = models.JSONField(default=list)
    recommended_action = models.TextField(null=True, blank=True)
    # Suppress re-firing for the same student within this many hours.
    cooldown_hours = models.PositiveIntegerField(default=24)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "alert_rules"
        ordering = ["key"]

    def __str__(self):
        return self.name

    @property
    def evaluator_key(self) -> str:
        return (self.condition or {}).get("evaluator", "")

    @property
    def params(self) -> dict:
        return (self.condition or {}).get("params", {}) or {}


class AlertQuerySet(models.QuerySet):
    def open(self):
        return self.filter(status__in=[AlertStatus.NEW, AlertStatus.IN_PROGRESS, AlertStatus.ESCALATED])

    def for_actor(self, actor):
        from apps.core.permissions import SCOPE_ALL, SCOPE_ASSIGNED, scope_for

        scope = scope_for(actor.role, "alert")
        if scope == SCOPE_ALL:
            return self
        if scope == SCOPE_ASSIGNED:
            return self.filter(models.Q(assigned_to=actor) | models.Q(student__instructor=actor))
        return self.none()


class Alert(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(AlertRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="alerts")
    student = models.ForeignKey(
        "academy.Student", null=True, blank=True, on_delete=models.CASCADE, related_name="alerts"
    )
    batch = models.ForeignKey(
        "academy.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="alerts"
    )
    title = models.CharField(max_length=200)
    problem = models.TextField()
    detected_at = models.DateTimeField(default=timezone.now, db_index=True)
    priority = models.CharField(max_length=16, choices=AlertPriority.choices, default=AlertPriority.MEDIUM)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_alerts"
    )
    recommended_action = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=AlertStatus.choices, default=AlertStatus.NEW, db_index=True)
    resolution_note = models.TextField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="resolved_alerts"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    # Deduplication key: rule key + entity id. With cooldown_hours this stops
    # the same problem generating an alert on every scan.
    dedupe_key = models.CharField(max_length=200, null=True, blank=True, db_index=True)
    context = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AlertQuerySet.as_manager()

    class Meta:
        db_table = "alerts"
        ordering = ["status", "-priority", "-detected_at"]
        indexes = [
            models.Index(fields=["status", "priority"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["dedupe_key", "-detected_at"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_open(self) -> bool:
        return self.status in {AlertStatus.NEW, AlertStatus.IN_PROGRESS, AlertStatus.ESCALATED}


class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    alert = models.ForeignKey(Alert, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField(max_length=16, choices=NotificationChannel.choices)
    title = models.CharField(max_length=200)
    body = models.TextField()
    link_url = models.CharField(max_length=500, null=True, blank=True)
    delivery_status = models.CharField(
        max_length=16, choices=NotificationDeliveryStatus.choices, default=NotificationDeliveryStatus.PENDING
    )
    # Why a delivery was skipped or failed, e.g. "SMS adapter not configured".
    delivery_detail = models.TextField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=200, null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "read_at"]),
            models.Index(fields=["delivery_status"]),
        ]

    def __str__(self):
        return self.title


class ScoringFactor(models.Model):
    """Admin-weighted scoring inputs. Disabling a factor renormalises the rest."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=64, unique=True)
    label = models.CharField(max_length=160)
    description = models.TextField(null=True, blank=True)
    weightage = models.DecimalField(max_digits=6, decimal_places=2)
    # Requires an OAuth grant to compute. Excluded automatically when absent.
    requires_analytics = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "scoring_factors"
        ordering = ["display_order", "label"]

    def __str__(self):
        return self.label


class PerformanceEvaluation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey("academy.Student", on_delete=models.CASCADE, related_name="evaluations")
    period_start = models.DateField()
    period_end = models.DateField()
    overall_score = models.DecimalField(max_digits=6, decimal_places=2)
    band = models.CharField(max_length=24, choices=PerformanceBand.choices, db_index=True)
    # Per-factor breakdown: key, label, weight, raw value, points, reason.
    # This is what makes a score explainable rather than a bare number.
    breakdown = models.JSONField()
    # Factors that could not be computed, e.g. no Analytics grant.
    excluded_factors = models.JSONField(null=True, blank=True)
    summary = models.TextField(null=True, blank=True)
    computed_at = models.DateTimeField(default=timezone.now)
    is_automated = models.BooleanField(default=True)

    class Meta:
        db_table = "performance_evaluations"
        ordering = ["-computed_at"]
        indexes = [models.Index(fields=["student", "-computed_at"])]

    def __str__(self):
        return f"{self.student_id}: {self.overall_score} ({self.band})"


class PerformanceTarget(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=16, choices=TargetScope.choices)
    # Batch, student or channel id depending on `scope`. Null = academy default.
    scope_id = models.CharField(max_length=64, null=True, blank=True)
    # Machine key, e.g. "videos_per_week", "ctr_percent".
    metric = models.CharField(max_length=64)
    label = models.CharField(max_length=200)
    comparator = models.CharField(max_length=16, choices=TargetComparator.choices)
    target_value = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=40, null=True, blank=True)
    # Rolling evaluation window. Null for one-off milestone targets.
    window_days = models.PositiveIntegerField(null=True, blank=True)
    requires_analytics = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "performance_targets"
        indexes = [models.Index(fields=["scope", "scope_id"])]

    def __str__(self):
        return self.label


class TargetResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    target = models.ForeignKey(PerformanceTarget, on_delete=models.CASCADE, related_name="results")
    student = models.ForeignKey(
        "academy.Student", null=True, blank=True, on_delete=models.CASCADE, related_name="target_results"
    )
    channel = models.ForeignKey(
        "youtube.YoutubeChannel", null=True, blank=True, on_delete=models.CASCADE, related_name="target_results"
    )
    actual_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=24, choices=TargetStatus.choices, db_index=True)
    instructor_comment = models.TextField(null=True, blank=True)
    commented_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="target_comments"
    )
    evaluated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "target_results"
        ordering = ["-evaluated_at"]
        indexes = [models.Index(fields=["target", "-evaluated_at"])]

    def __str__(self):
        return f"{self.target_id}: {self.status}"
