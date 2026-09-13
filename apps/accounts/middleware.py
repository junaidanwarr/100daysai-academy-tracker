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
