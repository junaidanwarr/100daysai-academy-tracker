"""
Blocks a half-authenticated administrator.

A Super Admin who has signed in but not cleared the TOTP challenge holds a
Django session, so without this every ``login_required`` view would let them
straight through. This is enforcement, not decoration.
"""

from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.services import mfa_satisfied

# Paths a half-authenticated user may still reach.
EXEMPT_PREFIXES = ("/mfa", "/logout", "/login", "/static", "/media", "/api/cron", "/api/webhooks")


class MfaEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if (
            user
            and user.is_authenticated
            and not request.path.startswith(EXEMPT_PREFIXES)
            and not mfa_satisfied(request)
        ):
            return redirect(reverse("accounts:mfa_challenge"))

        return self.get_response(request)


# Paths a user who must change their password may still reach. The admin is
# not on the list: an issued password must not open any door but this one.
PASSWORD_CHANGE_EXEMPT_PREFIXES = (
    "/password", "/logout", "/login", "/mfa", "/static", "/media", "/api/cron", "/api/webhooks",
)


class PasswordChangeRequiredMiddleware:
    """
    Holds a user on the change-password page while their password is one
    somebody else chose (issued by staff, seeded, or set in the admin).

    Runs after MfaEnforcementMiddleware, so a Super Admin clears the second
    factor first and is then asked for a new password.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if (
            user
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
            and not request.path.startswith(PASSWORD_CHANGE_EXEMPT_PREFIXES)
        ):
            return redirect(reverse("accounts:password_change"))

        return self.get_response(request)
