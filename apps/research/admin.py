from django.contrib import admin

from apps.research.models import (
    ResearchAttachment,
    ResearchCompetitor,
    ResearchCriteriaSet,
    ResearchCriterion,
    ResearchCriterionScore,
    ResearchSubmission,
)


class CriterionInline(admin.TabularInline):
    model = ResearchCriterion
    extra = 0
    fields = ["name", "weightage", "min_passing_score", "max_score", "is_required", "evidence_required", "display_order", "is_active"]


@admin.register(ResearchCriteriaSet)
class ResearchCriteriaSetAdmin(admin.ModelAdmin):
    list_display = ["name", "version", "is_default", "is_active", "total_weight"]
    list_filter = ["is_active", "is_default"]
    inlines = [CriterionInline]


@admin.register(ResearchCriterion)
class ResearchCriterionAdmin(admin.ModelAdmin):
    list_display = ["name", "criteria_set", "weightage", "min_passing_score", "is_required", "is_active"]
    list_filter = ["criteria_set", "is_required", "is_active"]


class CompetitorInline(admin.TabularInline):
    model = ResearchCompetitor
    extra = 0


class ScoreInline(admin.TabularInline):
    model = ResearchCriterionScore
    extra = 0
    readonly_fields = ["criterion", "score", "passed", "evaluator_note", "created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ResearchSubmission)
class ResearchSubmissionAdmin(admin.ModelAdmin):
    list_display = ["student", "version", "status", "topic", "score", "submitted_at", "evaluator"]
    list_filter = ["status"]
    search_fields = ["student__full_name", "student__enrollment_id", "topic", "niche"]
    inlines = [CompetitorInline, ScoreInline]
    readonly_fields = ["version", "created_at", "updated_at", "superseded_at"]

    def has_change_permission(self, request, obj=None):
        # A reviewed attempt is never edited — the student resubmits, which
        # creates version N+1 (specification 23.4).
        if obj and obj.is_closed:
            return False
        return super().has_change_permission(request, obj)


admin.site.register(ResearchAttachment)
