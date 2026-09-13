from apps.core.navigation import nav_for


def navigation(request):
    """Sidebar and header context, available to every template."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    from apps.core.enums import UserRole
    from apps.monitoring.models import Alert, Notification

    is_student = getattr(user, "role", None) == UserRole.STUDENT

    open_alerts = 0
    # Alerts are an oversight tool. A student is the subject of an alert, not a
    # handler of one, so the badge is never shown to them.
    if hasattr(user, "role") and not is_student:
        open_alerts = Alert.objects.for_actor(user).open().count()

    return {
        "nav_groups": nav_for(user.role),
        "is_student": is_student,
        "notifications_url": "portal:notifications" if is_student else "core:notifications",
        "brand_label": "My portal" if is_student else "Academy Tracker",
        "open_alert_count": open_alerts,
        "unread_notification_count": Notification.objects.filter(
            user=user, channel="IN_APP", read_at__isnull=True
        ).count(),
    }
