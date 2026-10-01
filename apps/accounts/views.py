from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.accounts.services import attempt_login, change_own_password, sign_out, verify_mfa_challenge
from apps.core.enums import UserRole


class LoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "input", "placeholder": "you@100daysai.com", "autofocus": True, "autocomplete": "username"})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "input", "autocomplete": "current-password"})
    )


class MfaForm(forms.Form):
    code = forms.CharField(
        label="Authentication code",
        widget=forms.TextInput(
            attrs={
                "class": "input code-input",
                "placeholder": "000000",
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "autofocus": True,
            }
        ),
    )


class ChangePasswordForm(PasswordChangeForm):
    """
    Django's own form: checks the current password and runs every validator in
    AUTH_PASSWORD_VALIDATORS against the new one. Adds one rule — the new
    password must differ from the current one, otherwise a forced change could
    be "completed" by re-typing the password staff handed out.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "Current password"
        self.fields["new_password1"].label = "New password"
        self.fields["new_password2"].label = "Confirm new password"
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"
        self.fields["old_password"].widget.attrs["autofocus"] = True

    def clean_new_password1(self):
        password = self.cleaned_data.get("new_password1")
        if password and self.user.check_password(password):
            raise forms.ValidationError("Choose a password different from your current one.", code="password_unchanged")
        return password

    def save(self, commit=True):
        # Saving is done by the service, so the audit entry and session handling
        # are never skipped.
        return self.user


def _safe_next(raw: str | None) -> str | None:
    """
    Redirect targets must be same-origin relative paths, so a crafted ?next=
    cannot bounce a freshly signed-in user off to another site.
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return None
    return raw


def _home_for(user) -> str:
    return reverse("portal:overview") if user.role == UserRole.STUDENT else reverse("core:dashboard")


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))

    form = LoginForm(request.POST or None)
    error = None
    next_url = _safe_next(request.GET.get("next") or request.POST.get("next"))

    if request.method == "POST" and form.is_valid():
        result = attempt_login(request, form.cleaned_data["email"], form.cleaned_data["password"])
        if result.ok:
            if result.needs_mfa:
                target = reverse("accounts:mfa_challenge")
                return redirect(f"{target}?next={next_url}" if next_url else target)
            return redirect(next_url or _home_for(result.user))
        error = result.message

    return render(request, "accounts/login.html", {"form": form, "error": error, "next": next_url})


@login_required
@require_http_methods(["GET", "POST"])
def mfa_challenge(request):
    from apps.accounts.services import mfa_satisfied

    if mfa_satisfied(request):
        return redirect(_home_for(request.user))

    form = MfaForm(request.POST or None)
    error = None
    next_url = _safe_next(request.GET.get("next") or request.POST.get("next"))

    if request.method == "POST" and form.is_valid():
        ok, message = verify_mfa_challenge(request, form.cleaned_data["code"])
        if ok:
            return redirect(next_url or _home_for(request.user))
        error = message

    return render(request, "accounts/mfa.html", {"form": form, "error": error, "next": next_url})


@require_http_methods(["POST", "GET"])
def logout_view(request):
    sign_out(request)
    return redirect("accounts:login")


@login_required
@require_http_methods(["GET", "POST"])
def password_change(request):
    forced = request.user.must_change_password
    form = ChangePasswordForm(request.user, request.POST or None)

    if request.method == "POST" and form.is_valid():
        change_own_password(request, form.cleaned_data["new_password1"])
        messages.success(request, "Your password has been changed. Any other signed-in devices have been signed out.")
        return redirect(_home_for(request.user))

    return render(
        request,
        "accounts/password_change_forced.html" if forced else "accounts/password_change.html",
        {"form": form, "forced": forced},
    )
