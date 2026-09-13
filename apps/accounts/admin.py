from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm, UserCreationForm

from apps.accounts.models import MfaCredential, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    ordering = ["full_name"]
    list_display = ["full_name", "email", "role", "is_active", "mfa_enrolled", "last_login_at"]
    list_filter = ["role", "is_active", "is_staff"]
    search_fields = ["full_name", "email"]
    readonly_fields = ["last_login_at", "failed_login_count", "locked_until", "created_at", "updated_at"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "phone", "avatar_url")}),
        ("Role and access", {"fields": ("role", "is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Security", {"fields": ("last_login_at", "failed_login_count", "locked_until")}),
        ("Lifecycle", {"fields": ("created_at", "updated_at", "deleted_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "full_name", "role", "password1", "password2")}),
    )

    @admin.display(boolean=True, description="MFA")
    def mfa_enrolled(self, obj):
        return obj.mfa_enrolled


@admin.register(MfaCredential)
class MfaCredentialAdmin(admin.ModelAdmin):
    list_display = ["user", "confirmed_at", "created_at"]
    # The secret and recovery codes are never editable or displayed.
    readonly_fields = ["user", "confirmed_at", "created_at", "updated_at"]
    exclude = ["encrypted_secret", "recovery_codes"]

    def has_add_permission(self, request):
        return False
