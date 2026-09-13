"""
The student's own menu.

Deliberately a separate list from the staff navigation rather than a filtered
view of it. Filtering the staff menu is what produced the leak this replaces:
a student holds ``read`` on ``student`` and ``research`` — scoped to their own
rows — so the staff menu happily offered them /students/ and the rubric editor.

Same invariant as the staff menu, though: every item is tied to a resource in
the permission matrix, so the portal can never offer a page its own role would
be refused.
"""

from apps.core.navigation import NavItem
from apps.core.permissions import can_access
from apps.core.enums import UserRole

PORTAL_NAV_ITEMS: list[NavItem] = [
    NavItem("My roadmap", "portal:overview", "student", 1, "My progress"),
    NavItem("My research", "portal:research", "research", 2, "My progress"),
    NavItem("My assignments", "portal:assignments", "assignment", 2, "My progress"),
    NavItem("My channels", "portal:channels", "channel", 1, "My channel"),
    NavItem("My videos", "portal:videos", "video", 3, "My channel"),
    NavItem("My analytics", "portal:analytics", "analytics", 3, "My channel"),
    NavItem("Notifications", "portal:notifications", "notification", 1, "Account"),
    NavItem("My agreement", "portal:agreements", "agreement", 5, "Account", delivered=False),
    NavItem("Raise a concern", "portal:grievances", "grievance", 5, "Account", delivered=False),
]

PORTAL_NAV_GROUPS = ["My progress", "My channel", "Account"]


def portal_nav() -> list[tuple[str, list[NavItem]]]:
    permitted = [item for item in PORTAL_NAV_ITEMS if can_access(UserRole.STUDENT, item.resource)]
    grouped = []
    for group in PORTAL_NAV_GROUPS:
        items = [i for i in permitted if i.group == group]
        if items:
            grouped.append((group, items))
    return grouped
