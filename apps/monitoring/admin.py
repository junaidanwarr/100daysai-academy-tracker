from django.contrib import admin

from apps.monitoring.models import (
    Alert,
    AlertRule,
    Notification,
    PerformanceEvaluation,
    PerformanceTarget,
    ScoringFactor,
    TargetResult,
)


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "key", "evaluator_key", "priority", "cooldown_hours", "is_active"]
    list_filter = ["is_active", "priority"]
    search_fields = ["key", "name"]

    @admin.display(description="Evaluator")
    def evaluator_key(self, obj):
        return obj.evaluator_key


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ["title", "student", "priority", "status", "assigned_to", "detected_at"]
    list_filter = ["status", "priority", "rule"]
    search_fields = ["title", "problem", "student__full_name", "student__enrollment_id"]
    readonly_fields = ["dedupe_key", "context", "detected_at", "created_at", "updated_at"]
    date_hierarchy = "detected_at"


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "channel", "delivery_status", "sent_at", "read_at"]
    list_filter = ["channel", "delivery_status"]
    search_fields = ["title", "user__email"]
    readonly_fields = ["provider_message_id", "sent_at", "created_at"]


@admin.register(ScoringFactor)
class ScoringFactorAdmin(admin.ModelAdmin):
    list_display = ["label", "key", "weightage", "requires_analytics", "is_active", "display_order"]
    list_filter = ["is_active", "requires_analytics"]


@admin.register(PerformanceEvaluation)
class PerformanceEvaluationAdmin(admin.ModelAdmin):
    list_display = ["student", "overall_score", "band", "computed_at", "is_automated"]
    list_filter = ["band", "is_automated"]
    readonly_fields = ["breakdown", "excluded_factors", "computed_at"]


@admin.register(PerformanceTarget)
class PerformanceTargetAdmin(admin.ModelAdmin):
    list_display = ["label", "scope", "metric", "comparator", "target_value", "is_active"]
    list_filter = ["scope", "is_active", "requires_analytics"]


@admin.register(TargetResult)
class TargetResultAdmin(admin.ModelAdmin):
    list_display = ["target", "student", "actual_value", "status", "evaluated_at"]
    list_filter = ["status"]
