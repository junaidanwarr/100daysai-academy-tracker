"""Template helpers for badges, charts and honest empty values."""

from django import template
from django.utils.safestring import mark_safe

from apps.academy.status import STATUS_TONES
from apps.core.enums import AlertPriority, AlertStatus, ChannelStatus, SubmissionStatus
from apps.core.permissions import can

register = template.Library()

CHANNEL_TONES = {
    ChannelStatus.PENDING: "slate",
    ChannelStatus.UNDER_REVIEW: "blue",
    ChannelStatus.APPROVED: "emerald",
    ChannelStatus.ACTIVE: "emerald",
    ChannelStatus.INACTIVE: "slate",
    ChannelStatus.MONETIZED: "violet",
    ChannelStatus.SUSPENDED: "red",
    ChannelStatus.TERMINATED: "red",
    ChannelStatus.ABANDONED: "orange",
    ChannelStatus.COMPLETED: "teal",
}

SUBMISSION_TONES = {
    SubmissionStatus.NOT_STARTED: "slate",
    SubmissionStatus.DRAFT: "slate",
    SubmissionStatus.SUBMITTED: "blue",
    SubmissionStatus.UNDER_REVIEW: "indigo",
    SubmissionStatus.APPROVED: "emerald",
    SubmissionStatus.REJECTED: "red",
    SubmissionStatus.REVISION_REQUESTED: "orange",
    SubmissionStatus.RESUBMITTED: "cyan",
    SubmissionStatus.OVERDUE: "red",
}

PRIORITY_TONES = {
    AlertPriority.LOW: "slate",
    AlertPriority.MEDIUM: "amber",
    AlertPriority.HIGH: "orange",
    AlertPriority.CRITICAL: "red",
}

ALERT_STATUS_TONES = {
    AlertStatus.NEW: "blue",
    AlertStatus.IN_PROGRESS: "amber",
    AlertStatus.RESOLVED: "emerald",
    AlertStatus.IGNORED: "slate",
    AlertStatus.ESCALATED: "red",
}

AUDIT_TONES = {
    "DELETE": "red", "OVERRIDE": "red", "LOGIN_FAILED": "red",
    "APPROVE": "emerald", "SIGN": "emerald", "CREATE": "emerald",
    "EXPORT": "amber", "SETTING_CHANGE": "amber",
}


def _badge(label: str, tone: str) -> str:
    return mark_safe(f'<span class="badge badge-{tone}">{label}</span>')


@register.simple_tag
def student_badge(status, label):
    return _badge(label, STATUS_TONES.get(status, "slate"))


@register.simple_tag
def channel_badge(status, label):
    return _badge(label, CHANNEL_TONES.get(status, "slate"))


@register.simple_tag
def submission_badge(status, label):
    return _badge(label, SUBMISSION_TONES.get(status, "slate"))


@register.simple_tag
def priority_badge(priority, label):
    return _badge(label, PRIORITY_TONES.get(priority, "slate"))


@register.simple_tag
def alert_status_badge(status, label):
    return _badge(label, ALERT_STATUS_TONES.get(status, "slate"))


@register.simple_tag
def audit_badge(action, label):
    return _badge(label, AUDIT_TONES.get(action, "slate"))


@register.simple_tag
def tone_badge(label, tone="slate"):
    return _badge(label, tone)


@register.simple_tag
def may(user, resource, action):
    """`{% may user 'student' 'create' as can_create %}` — reads the same matrix the services enforce."""
    return can(user.role, resource, action)


@register.filter
def bar_height(value, maximum):
    """Percentage height for a CSS bar chart, with a visible floor for non-zero values."""
    try:
        value, maximum = float(value), float(maximum)
    except (TypeError, ValueError):
        return 0
    if maximum <= 0:
        return 0
    if value == 0:
        return 0
    return max(3, round(value / maximum * 100))


@register.filter
def dash(value):
    """
    Renders None as an em dash rather than an empty cell or a zero.

    A metric we cannot obtain must never look like a measured zero — see the
    public/private YouTube data split.
    """
    if value is None or value == "":
        return "—"
    return value


@register.filter
def max_count(rows):
    """Largest `count` across chart rows, for scaling bars."""
    try:
        return max((row["count"] for row in rows), default=0)
    except (TypeError, KeyError):
        return 0


@register.filter
def get_item(mapping, key):
    if hasattr(mapping, "get"):
        return mapping.get(key, 0)
    return 0
