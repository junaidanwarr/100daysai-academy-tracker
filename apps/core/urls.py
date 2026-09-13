from django.urls import path

from apps.core import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("settings/", views.settings_view, name="settings"),
    path("audit-logs/", views.audit_log, name="audit_log"),
    path("staff/", views.staff, name="staff"),
    path("notifications/", views.notifications, name="notifications"),
    path("api/cron/<str:job>/", views.cron_endpoint, name="cron"),
    # Sections whose implementation belongs to a later phase. They render an
    # honest placeholder rather than a 404 or a misleading empty table.
    *[
        path(f"{section}/", views.phase_placeholder, {"section": section}, name=f"phase_{section}")
        for section in views.PHASE_SECTIONS
    ],
]
