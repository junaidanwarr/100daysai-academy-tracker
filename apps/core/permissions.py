"""
Single source of truth for authorization (specification section 3).

Two independent questions are answered here:

  1. CAN this role perform this action on this resource?  -> ``can()``
  2. WHICH rows may it see?                               -> ``scope_for()``

Both are enforced in the service layer, not in templates. The navigation reads
the same matrix so the menu can never offer a page that would refuse the user.
"""

from __future__ import annotations

from apps.core.enums import UserRole

RESOURCES = (
    "student",
    "batch",
    "research",
    "assignment",
    "channel",
    "video",
    "analytics",
    "performance",
    "alert",
    "report",
    "agreement",
    "grievance",
    "communication",
    "staff",
    "setting",
    "audit",
    "lms",
    "notification",
)

ACTIONS = (
    "read",
    "create",
    "update",
    "delete",
    "review",     # approve / reject / request revision
    "export",
    "sign",
    "configure",  # edit the rules governing a resource
    "override",   # deliberately break a constraint; always audited
)

# Row visibility.
SCOPE_ALL = "ALL"
SCOPE_ASSIGNED = "ASSIGNED"
SCOPE_OWN = "OWN"
SCOPE_NONE = "NONE"

_ALL_ACTIONS = set(ACTIONS)

SUPER_ADMIN = {resource: (_ALL_ACTIONS, SCOPE_ALL) for resource in RESOURCES}

INSTRUCTOR = {
    "student": ({"read", "update"}, SCOPE_ASSIGNED),
    "batch": ({"read"}, SCOPE_ASSIGNED),
    "research": ({"read", "review", "update"}, SCOPE_ASSIGNED),
    "assignment": ({"read", "review", "update", "create"}, SCOPE_ASSIGNED),
    "channel": ({"read", "update", "review"}, SCOPE_ASSIGNED),
    "video": ({"read"}, SCOPE_ASSIGNED),
    "analytics": ({"read"}, SCOPE_ASSIGNED),
    "performance": ({"read", "create", "update"}, SCOPE_ASSIGNED),
    "alert": ({"read", "update"}, SCOPE_ASSIGNED),
    "report": ({"read", "export"}, SCOPE_ASSIGNED),
    "agreement": ({"read", "create", "sign"}, SCOPE_ASSIGNED),
    "grievance": ({"read", "update"}, SCOPE_ASSIGNED),
    "communication": ({"read", "create"}, SCOPE_ASSIGNED),
    "notification": ({"read", "update"}, SCOPE_OWN),
}

STUDENT = {
    "student": ({"read"}, SCOPE_OWN),
    "batch": ({"read"}, SCOPE_OWN),
    # Students create and resubmit; they never review.
    "research": ({"read", "create", "update"}, SCOPE_OWN),
    "assignment": ({"read", "create", "update"}, SCOPE_OWN),
    # Read-only. The channel record is created by an instructor once research
    # is approved, so that a channel cannot exist against unapproved research.
    "channel": ({"read"}, SCOPE_OWN),
    "video": ({"read"}, SCOPE_OWN),
    "analytics": ({"read"}, SCOPE_OWN),
    "performance": ({"read"}, SCOPE_OWN),
    "agreement": ({"read", "sign"}, SCOPE_OWN),
    "grievance": ({"read", "create"}, SCOPE_OWN),
    "notification": ({"read", "update"}, SCOPE_OWN),
}

# Sees everything, changes nothing. `export` is granted because reporting is the
# point of the role; every export is audited.
MANAGEMENT_READONLY = {
    "student": ({"read"}, SCOPE_ALL),
    "batch": ({"read"}, SCOPE_ALL),
    "research": ({"read"}, SCOPE_ALL),
    "assignment": ({"read"}, SCOPE_ALL),
    "channel": ({"read"}, SCOPE_ALL),
    "video": ({"read"}, SCOPE_ALL),
    "analytics": ({"read"}, SCOPE_ALL),
    "performance": ({"read"}, SCOPE_ALL),
    "alert": ({"read"}, SCOPE_ALL),
    "report": ({"read", "export"}, SCOPE_ALL),
    "agreement": ({"read"}, SCOPE_ALL),
    "grievance": ({"read"}, SCOPE_ALL),
    "communication": ({"read"}, SCOPE_ALL),
    "notification": ({"read", "update"}, SCOPE_OWN),
}

MATRIX = {
    UserRole.SUPER_ADMIN: SUPER_ADMIN,
    UserRole.INSTRUCTOR: INSTRUCTOR,
    UserRole.STUDENT: STUDENT,
    UserRole.MANAGEMENT_READONLY: MANAGEMENT_READONLY,
}


# Roles that belong in the staff console. A student holds `read` on several of
# the same resources, scoped to their own rows, so `can()` alone cannot tell the
# two apart — the console is a different jurisdiction, not a smaller one.
STAFF_ROLES = frozenset({UserRole.SUPER_ADMIN, UserRole.INSTRUCTOR, UserRole.MANAGEMENT_READONLY})


def is_staff_role(role: str) -> bool:
    return role in STAFF_ROLES


class PermissionDenied(Exception):
    """Raised by ``assert_can``. Views translate this into a 403."""

    status_code = 403

    def __init__(self, role: str, resource: str, action: str):
        self.role = role
        self.resource = resource
        self.action = action
        super().__init__(f"Role {role} may not {action} {resource}.")


def can(role: str, resource: str, action: str) -> bool:
    grant = MATRIX.get(role, {}).get(resource)
    return bool(grant and action in grant[0])


def scope_for(role: str, resource: str) -> str:
    grant = MATRIX.get(role, {}).get(resource)
    return grant[1] if grant else SCOPE_NONE


def can_access(role: str, resource: str) -> bool:
    """Any access at all — used to build the navigation."""
    return can(role, resource, "read")


def assert_can(role: str, resource: str, action: str) -> None:
    if not can(role, resource, action):
        raise PermissionDenied(role, resource, action)


def requires_mfa(role: str) -> bool:
    """True when the role must clear a TOTP challenge before holding a session."""
    return role == UserRole.SUPER_ADMIN
