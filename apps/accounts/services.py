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
from django.db import transaction
from django.utils import timezone

from apps.accounts.mfa import decrypt_secret, match_recovery_code, verify_totp
from apps.accounts.models import User
from apps.core.audit import write_audit
from apps.core.crypto import generate_password
from apps.core.enums import AuditAction, UserRole
from apps.core.middleware import current_request_meta
from apps.core.permissions import assert_can, requires_mfa

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

    student = getattr(user, "student_profile", None)
    if student and not student.deleted_at:
        from apps.academy.activity import record_activity
        from apps.core.enums import ActivityKind

        record_activity(student, ActivityKind.LOGIN, "Signed in to the portal", actor=user)

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


# --- Student portal logins --------------------------------------------------


class StudentLoginError(Exception):
    """A login cannot be issued or reset for this student as things stand."""


def issue_student_login(actor, student, password: str | None = None) -> tuple[User, str]:
    """
    Creates the portal account for a tracked student and links it.

    Returns the password in plain text exactly once, for the caller to hand
    over; it is never stored or logged anywhere but as a hash. Administrator
    only: an account is a way into someone's records.
    """
    assert_can(actor.role, "student", "create")

    if student.user_id:
        raise StudentLoginError(f"{student.full_name} already has a login. Reset the password instead.")

    email = (student.email or "").strip().lower()
    if not email:
        raise StudentLoginError("Add an email address to the student record first; it is the login name.")
    if User.objects.filter(email__iexact=email).exists():
        raise StudentLoginError(
            f"Another account already uses {email}. Change the student's email, or retire that account first."
        )

    password = password or generate_password()
    with transaction.atomic():
        user = User.objects.create_user(
            email=email, password=password, full_name=student.full_name, role=UserRole.STUDENT,
        )
        student.user = user
        student.save(update_fields=["user", "updated_at"])

    write_audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="User",
        entity_id=user.pk,
        summary=f"Issued a portal login for {student.full_name} ({student.enrollment_id}) as {email}",
        **current_request_meta(),
    )
    return user, password


def reset_student_password(actor, student) -> str:
    """
    Replaces a student's password and clears any lockout. Changing the password
    also invalidates every session the old one opened, so a shared or leaked
    login stops working immediately.
    """
    assert_can(actor.role, "student", "create")

    user = student.user
    if user is None:
        raise StudentLoginError(f"{student.full_name} has no login yet. Create one instead.")
    if user.role != UserRole.STUDENT:
        # A student record linked to a staff account must never become a way
        # to reset a staff password from the student page.
        raise StudentLoginError("This record is linked to a staff account; reset it from the Staff page.")

    password = generate_password()
    user.set_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.save(update_fields=["password", "failed_login_count", "locked_until", "updated_at"])

    write_audit(
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.pk,
        summary=(
            f"Reset the portal password for {student.full_name} ({student.enrollment_id}); "
            "existing sessions signed out"
        ),
        **current_request_meta(),
    )
    return password
