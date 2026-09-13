from django.contrib import admin

from apps.core.models import AuditLog, SavedFilter, SystemSetting


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ["label", "key", "value", "category", "value_type", "updated_by", "updated_at"]
    list_filter = ["category", "value_type"]
    search_fields = ["key", "label"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """The audit trail is append-only — read-only everywhere, including here."""

    list_display = ["created_at", "action", "entity_type", "summary", "actor", "ip_address"]
    list_filter = ["action", "entity_type"]
    search_fields = ["summary", "entity_id", "actor_email"]
    readonly_fields = [f.name for f in AuditLog._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SavedFilter)
class SavedFilterAdmin(admin.ModelAdmin):
    list_display = ["name", "entity", "user", "is_shared", "is_system"]
    list_filter = ["entity", "is_shared", "is_system"]
