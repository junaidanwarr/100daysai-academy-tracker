"""
Sign-in, lockout and the MFA challenge.

Failure responses are deliberately uniform: an unknown email and a wrong
password produce the same message and comparable timing, so the form cannot be
used to enumerate who has an account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login as django_login, logout as django_logout
from django.contrib.auth.hashers import check_password
from django.utils import timezone

from apps.accounts.mfa import decrypt_secret, match_recovery_code, verify_totp
from apps.accounts.models import User
from apps.core.audit import write_audit
from apps.core.enums import AuditAction
from apps.core.middleware import current_request_meta
from apps.core.permissions import requires_mfa

GENERIC_FAILURE = "Email or password is incorrect."

# Session key holding whether the TOTP challenge has been cleared.
MFA_SESSION_KEY = "mfa_satisfied"


@dataclass
class LoginResult:
    ok: bool
    message: str | None = None
    needs_mfa: bool = False
    user: User | None = None


def attempt_login(request, email: str, password: str) -> LoginResult:
    email = (email or "").strip().lower()
    meta = current_request_meta()
    user = User.objects.filter(email=email).first()

    if not user or not user.is_active or user.deleted_at:
        # Still hash something so a missing account is not detectably faster.
        check_password(password, "pbkdf2_sha256$1$x$" + "a" * 44)
        write_audit(
            actor=None,
            action=AuditAction.LOGIN_FAILED,
            entity_type="User",
            summary=f"Failed sign-in for {email} (no active account).",
            **meta,
        )
        return LoginResult(ok=False, message=GENERIC_FAILURE)

    if user.is_locked:
        minutes = max(1, int((user.locked_until - timezone.now()).total_seconds() // 60) + 1)
        return LoginResult(ok=False, message=f"Too many failed attempts. Try again in {minutes} minute(s).")

    authenticated = authenticate(request, username=email, password=password)

    if authenticated is None:
        user.failed_login_count += 1
        if user.failed_login_count >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            user.locked_until = timezone.now() + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
        user.save(update_fields=["failed_login_count", "locked_until", "updated_at"])

        write_audit(
            actor=user,
            action=AuditAction.LOGIN_FAILED,
            entity_type="User",
            entity_id=user.pk,
            summary=f"Failed sign-in attempt {user.failed_login_count} for {user.email}.",
            **meta,
        )
        return LoginResult(ok=False, message=GENERIC_FAILURE)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = timezone.now()
    user.save(update_fields=["failed_login_count", "locked_until", "last_login_at", "updated_at"])

    django_login(request, authenticated)

    # A Super Admin who has not enrolled a factor yet is let through rather than
    # locked out of the system they administer; the Staff page flags them.
    needs_mfa = requires_mfa(user.role) and user.mfa_enrolled
    request.session[MFA_SESSION_KEY] = not needs_mfa

    write_audit(
        actor=user,
        action=AuditAction.LOGIN,
        entity_type="User",
        entity_id=user.pk,
        summary=f"{user.email} signed in.",
        **meta,
    )

    return LoginResult(ok=True, needs_mfa=needs_mfa, user=user)


def verify_mfa_challenge(request, code: str) -> tuple[bool, str | None]:
    user = request.user
    credential = getattr(user, "mfa", None)
    if not credential or not credential.confirmed_at:
        return False, "Two-factor authentication is not set up on this account."

    secret = decrypt_secret(credential.encrypted_secret)
    supplied = (code or "").strip()

    if verify_totp(supplied, secret):
        request.session[MFA_SESSION_KEY] = True
        return True, None

    # Fall back to a recovery code, which is consumed on use.
    index = match_recovery_code(supplied, credential.recovery_codes or [])
    if index >= 0:
        remaining = [c for i, c in enumerate(credential.recovery_codes) if i != index]
        credential.recovery_codes = remaining
        credential.save(update_fields=["recovery_codes", "updated_at"])
        request.session[MFA_SESSION_KEY] = True

        write_audit(
            actor=user,
            action=AuditAction.LOGIN,
            entity_type="User",
            entity_id=user.pk,
            summary=f"Recovery code used; {len(remaining)} remaining.",
            **current_request_meta(),
        )
        return True, None

    return False, "That code is not valid. Check your authenticator app and try again."


def sign_out(request) -> None:
    user = request.user
    if user.is_authenticated:
        write_audit(
            actor=user,
            action=AuditAction.LOGOUT,
            entity_type="User",
            entity_id=user.pk,
            summary=f"{user.email} signed out.",
            **current_request_meta(),
        )
    django_logout(request)


def mfa_satisfied(request) -> bool:
    """
    True when the session has cleared every gate. A role that does not require
    MFA is satisfied by definition.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if not requires_mfa(user.role):
        return True
    return bool(request.session.get(MFA_SESSION_KEY))
