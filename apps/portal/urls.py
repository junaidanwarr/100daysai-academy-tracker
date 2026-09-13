"""
Every student-facing route, under one prefix.

No route here takes a student identifier. The signed-in user *is* the scope, so
there is nothing in a portal URL to tamper with.
"""

from django.urls import path

from apps.portal import views

app_name = "portal"

urlpatterns = [
    path("portal/", views.overview, name="overview"),
    path("portal/research/", views.research, name="research"),
    path("portal/assignments/", views.assignments, name="assignments"),
    path("portal/channels/", views.channels, name="channels"),
    path("portal/channels/<uuid:pk>/", views.channel_detail, name="channel_detail"),
    path("portal/videos/", views.videos, name="videos"),
    path("portal/videos/<uuid:pk>/", views.video_detail, name="video_detail"),
    path("portal/analytics/", views.analytics, name="analytics"),
    path("portal/notifications/", views.notifications, name="notifications"),
    path("portal/notifications/read/", views.mark_notifications_read, name="mark_notifications_read"),
    *[
        path(f"portal/{section}/", views.placeholder, {"section": section}, name=section)
        for section in views.PORTAL_PLACEHOLDERS
    ],
]
