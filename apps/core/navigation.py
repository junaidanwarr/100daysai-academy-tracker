"""
Navigation, matched to specification section 22.

`resource` ties each item to the permission matrix, so the menu can never offer
a page the user would be refused. `phase` records which phase built the section
and `delivered` whether it works today — an undelivered section renders an
honest placeholder rather than a broken page, and carries a tag in the menu so
nobody clicks it expecting data.
"""

from dataclasses import dataclass

from apps.core.permissions import can_access


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str
    resource: str
    phase: int
    group: str
    delivered: bool = True


NAV_ITEMS: list[NavItem] = [
    NavItem("Dashboard", "core:dashboard", "student", 1, "Overview"),
    NavItem("Students", "academy:student_list", "student", 1, "People"),
    NavItem("Batches", "academy:batch_list", "batch", 1, "People"),
    NavItem("Staff", "core:staff", "staff", 1, "People"),
    NavItem("Research", "research:submission_list", "research", 1, "Work"),
    NavItem("Assignments", "assignments:assignment_list", "assignment", 1, "Work"),
    NavItem("Channels", "youtube:channel_list", "channel", 1, "YouTube"),
    NavItem("Videos", "youtube:video_list", "video", 3, "YouTube"),
    NavItem("Analytics", "youtube:analytics_overview", "analytics", 3, "YouTube"),
    NavItem("Alerts", "monitoring:alert_list", "alert", 1, "Oversight"),
    NavItem("Performance", "core:phase_performance", "performance", 4, "Oversight", delivered=False),
    NavItem("Reports", "core:phase_reports", "report", 4, "Oversight", delivered=False),
    NavItem("Agreements", "core:phase_agreements", "agreement", 5, "Admin", delivered=False),
    NavItem("Grievances", "core:phase_grievances", "grievance", 5, "Admin", delivered=False),
    NavItem("Settings", "core:settings", "setting", 1, "Admin"),
    NavItem("Audit Logs", "core:audit_log", "audit", 1, "Admin"),
]

NAV_GROUPS = ["Overview", "People", "Work", "YouTube", "Oversight", "Admin"]


def nav_for(role: str) -> list[tuple[str, list[NavItem]]]:
    """Items grouped for rendering, filtered by what the role may actually read."""
    from apps.core.enums import UserRole

    # Students get their own menu, not a filtered copy of this one. Filtering
    # was the bug: a student's own-row `read` grants made the console's Students
    # and Research entries pass the permission check.
    if role == UserRole.STUDENT:
        from apps.portal.navigation import portal_nav

        return portal_nav()

    permitted = [item for item in NAV_ITEMS if can_access(role, item.resource)]
    grouped = []
    for group in NAV_GROUPS:
        items = [i for i in permitted if i.group == group]
        if items:
            grouped.append((group, items))
    return grouped
