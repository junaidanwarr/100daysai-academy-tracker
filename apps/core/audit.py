"""
Audit trail helpers (specification section 18).

Every write that matters routes through here, so the log cannot drift from
reality by someone forgetting to call it.
"""

from __future__ import annotations

import datetime
import decimal
import logging
import uuid
from typing import Any

from django.db import models

from apps.core.models import AuditLog

logger = logging.getLogger(__name__)

# Never written to the log, in any entity. A leaked audit table must not leak
# credentials or OAuth tokens.
REDACTED_FIELDS = {
    "password",
    "password_hash",
    "encrypted_secret",
    "encrypted_refresh_token",
    "encrypted_credentials",
    "token_hash",
    "recovery_codes",
    "signature_hash",
    "lms_raw_payload",
}

REDACTION = "[redacted]"


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, models.Model):
        return str(value.pk)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def redact(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        key: (REDACTION if key in REDACTED_FIELDS else _jsonable(value))
        for key, value in payload.items()
    }


def diff_fields(before: dict[str, Any] | None, after: dict[str, Any] | None) -> tuple[dict, dict]:
    """
    Reduce two versions of a record to only the fields that actually changed.
    Logging whole rows makes the trail unreadable and hides the real edit.
    """
    if not before:
        return {}, after or {}
    if not after:
        return before, {}

    changed_before: dict[str, Any] = {}
    changed_after: dict[str, Any] = {}

    for key in set(before) | set(after):
        a = _jsonable(before.get(key))
        b = _jsonable(after.get(key))
        if a != b:
            changed_before[key] = a
            changed_after[key] = b

    return changed_before, changed_after


def snapshot(instance: models.Model, fields: list[str] | None = None) -> dict[str, Any]:
    """Field values of a model instance, for before/after comparison."""
    names = fields or [f.name for f in instance._meta.fields]
    return {name: getattr(instance, name, None) for name in names}


def write_audit(
    actor,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    summary: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """
    Record an action. A failed audit write must not roll back the user's actual
    work, but it must be loud — a silent gap in the trail is worse than a noisy
    log.
    """
    try:
        AuditLog.objects.create(
            actor=actor if getattr(actor, "pk", None) else None,
            actor_email=getattr(actor, "email", None) or (actor if isinstance(actor, str) else None),
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            summary=summary,
            before=redact(before),
            after=redact(after),
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception:  # noqa: BLE001 - deliberately broad
        logger.exception(
            "Failed to write audit entry action=%s entity=%s id=%s", action, entity_type, entity_id
        )
