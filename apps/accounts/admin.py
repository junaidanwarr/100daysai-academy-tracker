from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm, UserCreationForm

from apps.accounts.models import MfaCredential, User


# A password typed in by an administrator is one the user did not choose, so
# both admin password forms flag the account for a change at next sign-in.


class IssuedPasswordCreationForm(UserCreationForm):
    # Bound to this project's user model: the stock form targets auth.User,
    # which is swapped out here, so the admin "Add user" page could not work.
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email", "full_name", "role")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = True
        if commit:
            user.save()
        return user


class IssuedPasswordChangeForm(AdminPasswordChangeForm):
    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = True
        if commit:
            user.save()
        return user


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = IssuedPasswordCreationForm
    change_password_form = IssuedPasswordChangeForm
    ordering = ["full_name"]
    list_display = ["full_name", "email", "role", "is_active", "mfa_enrolled", "last_login_at"]
    list_filter = ["role", "is_active", "is_staff"]
    search_fields = ["full_name", "email"]
    readonly_fields = ["last_login_at", "password_changed_at", "failed_login_count", "locked_until", "created_at", "updated_at"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "phone", "avatar_url")}),
        ("Role and access", {"fields": ("role", "is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Security", {"fields": ("must_change_password", "password_changed_at", "last_login_at", "failed_login_count", "locked_until")}),
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
