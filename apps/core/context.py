"""
Identity for work that no signed-in user initiated: scheduled jobs, webhook
ingestion, management commands.

Deliberately *not* a User instance. Django assigns a UUID default the moment a
model is constructed, so an unsaved User looks persisted to anything checking
``pk`` — which produced a foreign-key violation when the audit log tried to
reference it. A plain object with no ``pk`` fails that check honestly, and the
action is attributed to a named system identity in the trail.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.core.enums import UserRole


@dataclass(frozen=True)
class SystemActor:
    """Satisfies the ``.role`` / ``.email`` / ``.pk`` interface services expect."""

    label: str = "system"
    # Permission checks pass unconditionally: a scheduled job is not acting on
    # behalf of a role, and its reach is bounded by the code that calls it.
    role: str = UserRole.SUPER_ADMIN

    @property
    def email(self) -> str:
        return f"{self.label}@internal"

    @property
    def full_name(self) -> str:
        return self.label

    @property
    def pk(self):
        return None

    @property
    def is_authenticated(self) -> bool:
        return False

    def __str__(self) -> str:
        return self.email


def system_actor(label: str = "system") -> SystemActor:
    return SystemActor(label=label)
