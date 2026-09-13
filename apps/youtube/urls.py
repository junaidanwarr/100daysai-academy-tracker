from django.urls import path

from apps.youtube import views

app_name = "youtube"

urlpatterns = [
    path("channels/", views.channel_list, name="channel_list"),
    path("channels/new/", views.channel_create, name="channel_create"),
    path("channels/<uuid:pk>/", views.channel_detail, name="channel_detail"),
    path("channels/<uuid:pk>/edit/", views.channel_edit, name="channel_edit"),
    path("channels/<uuid:pk>/sync/", views.channel_sync, name="channel_sync"),
    # Consent lifecycle. The callback carries the channel in a signed state
    # parameter, so it needs no identifier of its own in the path — and must
    # match GOOGLE_OAUTH_REDIRECT_URI exactly.
    path("channels/<uuid:pk>/analytics/connect/", views.analytics_connect, name="analytics_connect"),
    path("channels/<uuid:pk>/analytics/disconnect/", views.analytics_disconnect, name="analytics_disconnect"),
    path("api/oauth/youtube/callback/", views.analytics_callback, name="analytics_callback"),
    path("videos/", views.video_list, name="video_list"),
    path("videos/<uuid:pk>/", views.video_detail, name="video_detail"),
    path("videos/<uuid:pk>/retention/", views.video_retention, name="video_retention"),
    path("analytics/", views.analytics_overview, name="analytics_overview"),
]
