"""Base model behaviour plus the system-wide tables."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.enums import AuditAction


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        return self.filter(deleted_at__isnull=False)


class SoftDeleteManager(models.Manager):
    """
    Default manager returning only live rows.

    `all_objects` remains available on every soft-deletable model for the
    admin and for audit queries, so archived rows are recoverable rather than
    invisible.
    """

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)


class TimestampedModel(models.Model):
    """UUID primary keys so identifiers are safe to expose in URLs."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(TimestampedModel):
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])

    def restore(self):
        self.deleted_at = None
        self.save(update_fields=["deleted_at", "updated_at"])

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class AuditLog(TimestampedModel):
    """
    Append-only trail (specification section 18).

    `before` and `after` hold only the fields that actually changed, which keeps
    the log readable. Sensitive fields are redacted before write — see
    apps.core.audit.REDACTED_FIELDS.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    # Kept even if the user row is later removed.
    actor_email = models.EmailField(null=True, blank=True)
    action = models.CharField(max_length=32, choices=AuditAction.choices)
    entity_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64, null=True, blank=True)
    summary = models.TextField(null=True, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.action} {self.entity_type} @ {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def changed_fields(self) -> list[str]:
        return sorted({*(self.before or {}).keys(), *(self.after or {}).keys()})


class SystemSetting(TimestampedModel):
    """
    Operational configuration held in rows, not constants.

    This is what makes the research window genuinely configurable rather than
    "fixed at 15 days" (specification section 7).
    """

    VALUE_TYPES = [
        ("int", "Whole number"),
        ("string", "Text"),
        ("bool", "True/false"),
        ("json", "JSON"),
    ]

    key = models.SlugField(max_length=64, unique=True)
    value = models.JSONField()
    label = models.CharField(max_length=160)
    description = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=40, default="general", db_index=True)
    value_type = models.CharField(max_length=10, choices=VALUE_TYPES, default="string")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="setting_updates"
    )

    class Meta:
        db_table = "system_settings"
        ordering = ["category", "key"]

    def __str__(self):
        return f"{self.label} ({self.key})"


class SavedFilter(TimestampedModel):
    """Named, reusable list filters (specification section 19)."""

    # Null owner = a system preset available to everyone.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="saved_filters"
    )
    name = models.CharField(max_length=120)
    entity = models.CharField(max_length=40, db_index=True)
    query = models.JSONField()
    is_shared = models.BooleanField(default=False)
    is_system = models.BooleanField(default=False)
    icon = models.CharField(max_length=40, null=True, blank=True)
    display_order = models.IntegerField(default=0)

    class Meta:
        db_table = "saved_filters"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name
