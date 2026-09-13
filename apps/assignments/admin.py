from django.contrib import admin

from apps.assignments.models import (
    Assignment,
    AssignmentFile,
    AssignmentSubmission,
    LmsConnection,
    LmsSyncLog,
)


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ["title", "type", "batch", "due_at", "lms_assignment_id", "is_active"]
    list_filter = ["type", "is_active"]
    search_fields = ["title", "lms_assignment_id"]


class AssignmentFileInline(admin.TabularInline):
    model = AssignmentFile
    extra = 0


@admin.register(AssignmentSubmission)
class AssignmentSubmissionAdmin(admin.ModelAdmin):
    list_display = ["assignment", "student", "version", "status", "score", "submitted_at"]
    list_filter = ["status"]
    search_fields = ["student__full_name", "student__enrollment_id", "assignment__title", "lms_submission_id"]
    readonly_fields = ["version", "lms_raw_payload", "created_at", "updated_at", "superseded_at"]
    inlines = [AssignmentFileInline]


@admin.register(LmsConnection)
class LmsConnectionAdmin(admin.ModelAdmin):
    list_display = ["name", "provider", "is_active", "last_sync_at", "last_sync_status"]
    list_filter = ["provider", "is_active"]
    # Credentials are never displayed or editable in the admin.
    exclude = ["encrypted_credentials"]
    readonly_fields = ["last_sync_at", "last_sync_status", "created_at", "updated_at"]


@admin.register(LmsSyncLog)
class LmsSyncLogAdmin(admin.ModelAdmin):
    list_display = ["connection", "status", "started_at", "finished_at", "records_read", "records_written"]
    list_filter = ["status"]
    readonly_fields = [f.name for f in LmsSyncLog._meta.fields]

    def has_add_permission(self, request):
        return False
