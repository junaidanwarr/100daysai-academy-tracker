from django.contrib import admin

from apps.completion.models import (
    CommunicationLog,
    CompletionRecord,
    Grievance,
    GrievanceAttachment,
    Signature,
)


class SignatureInline(admin.TabularInline):
    model = Signature
    extra = 0
    readonly_fields = ["signer_role", "signer_name", "signer_user", "signature_hash", "signed_at", "ip_address"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CompletionRecord)
class CompletionRecordAdmin(admin.ModelAdmin):
    list_display = ["student", "version", "status", "issued_at", "completed_at"]
    list_filter = ["status"]
    search_fields = ["student__full_name", "student__enrollment_id"]
    readonly_fields = ["version", "content", "pdf_hash", "created_at", "updated_at"]
    inlines = [SignatureInline]

    def has_change_permission(self, request, obj=None):
        # Signed documents become read-only; a correction issues a new version
        # rather than editing this one (specification 23.11 - 23.12).
        if obj and obj.is_locked:
            return False
        return super().has_change_permission(request, obj)


@admin.register(Signature)
class SignatureAdmin(admin.ModelAdmin):
    list_display = ["completion_record", "signer_role", "signer_name", "signed_at"]
    readonly_fields = [f.name for f in Signature._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class GrievanceAttachmentInline(admin.TabularInline):
    model = GrievanceAttachment
    extra = 0


@admin.register(Grievance)
class GrievanceAdmin(admin.ModelAdmin):
    list_display = ["reference", "student", "category", "status", "assigned_to", "submitted_at"]
    list_filter = ["status", "category"]
    search_fields = ["reference", "subject", "student__full_name", "student__enrollment_id"]
    inlines = [GrievanceAttachmentInline]


@admin.register(CommunicationLog)
class CommunicationLogAdmin(admin.ModelAdmin):
    list_display = ["subject", "student", "type", "occurred_at", "recorded_by"]
    list_filter = ["type"]
    search_fields = ["subject", "summary", "student__full_name"]
