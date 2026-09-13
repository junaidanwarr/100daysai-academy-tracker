"""Identity: users, lockout state, and the TOTP second factor."""

import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.enums import UserRole


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("role", UserRole.STUDENT)
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("role", UserRole.SUPER_ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("full_name", "Administrator")
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("A superuser must have is_staff and is_superuser set.")
        return self._create(email, password, **extra)

    def active(self):
        return self.filter(is_active=True, deleted_at__isnull=True)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Email is the username. `role` drives the application permission matrix in
    apps.core.permissions; Django's own is_staff/is_superuser only govern
    access to the Django admin.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=160)
    phone = models.CharField(max_length=40, null=True, blank=True)
    role = models.CharField(max_length=32, choices=UserRole.choices, default=UserRole.STUDENT, db_index=True)
    avatar_url = models.URLField(max_length=500, null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    last_login_at = models.DateTimeField(null=True, blank=True)
    # Consecutive failed logins; cleared on success. Drives lockout.
    failed_login_count = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        db_table = "users"
        ordering = ["full_name"]
        indexes = [models.Index(fields=["role", "is_active"])]

    def __str__(self):
        return f"{self.full_name} <{self.email}>"

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email

    @property
    def initials(self) -> str:
        parts = [p for p in (self.full_name or "").split() if p][:2]
        return "".join(p[0].upper() for p in parts) or self.email[:1].upper()

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())

    @property
    def mfa_enrolled(self) -> bool:
        credential = getattr(self, "mfa", None)
        return bool(credential and credential.confirmed_at)


class MfaCredential(models.Model):
    """
    TOTP second factor. Required for SUPER_ADMIN, optional for other roles
    (specification section 21).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="mfa")
    # AES-256-GCM ciphertext of the TOTP shared secret.
    encrypted_secret = models.TextField()
    # Hashed, single-use recovery codes.
    recovery_codes = models.JSONField(default=list)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mfa_credentials"

    def __str__(self):
        state = "confirmed" if self.confirmed_at else "pending"
        return f"MFA for {self.user.email} ({state})"
