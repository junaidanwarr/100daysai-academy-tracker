from django.contrib import admin

from apps.youtube.models import (
    ChannelOauthGrant,
    ChannelSnapshot,
    SyncJobRun,
    Video,
    VideoRetentionPoint,
    VideoSnapshot,
    VideoTrafficSource,
    YoutubeChannel,
)


@admin.register(YoutubeChannel)
class YoutubeChannelAdmin(admin.ModelAdmin):
    list_display = ["channel_name", "student", "status", "content_type", "monetization_status", "last_upload_at", "analytics"]
    list_filter = ["status", "content_type", "monetization_status", "ownership_verified"]
    search_fields = ["channel_name", "youtube_channel_id", "student__full_name", "student__enrollment_id"]
    readonly_fields = ["created_at", "updated_at", "last_synced_at"]

    @admin.display(boolean=True, description="Analytics authorized")
    def analytics(self, obj):
        return obj.has_analytics_access


@admin.register(ChannelOauthGrant)
class ChannelOauthGrantAdmin(admin.ModelAdmin):
    """The refresh token is never displayed or editable, here or anywhere."""

    list_display = ["channel", "google_account_email", "consented_at", "revoked_at"]
    readonly_fields = ["channel", "consent_version", "consented_at", "consent_ip_address",
                       "access_token_expires_at", "last_refreshed_at", "created_at", "updated_at"]
    exclude = ["encrypted_refresh_token"]

    def has_add_permission(self, request):
        return False


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ["title", "channel", "video_type", "published_at", "duration_seconds"]
    list_filter = ["video_type"]
    search_fields = ["title", "youtube_video_id"]


class ReadOnlySnapshotAdmin(admin.ModelAdmin):
    """Snapshots are an immutable time series — never edited after write."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ChannelSnapshot)
class ChannelSnapshotAdmin(ReadOnlySnapshotAdmin):
    list_display = ["channel", "captured_at", "source", "subscriber_count", "view_count", "video_count"]
    list_filter = ["source"]
    date_hierarchy = "captured_at"


@admin.register(VideoSnapshot)
class VideoSnapshotAdmin(ReadOnlySnapshotAdmin):
    list_display = ["video", "captured_at", "source", "views", "likes", "impressions", "impressions_ctr_percent"]
    list_filter = ["source"]
    date_hierarchy = "captured_at"


@admin.register(SyncJobRun)
class SyncJobRunAdmin(ReadOnlySnapshotAdmin):
    list_display = ["job_type", "channel", "status", "started_at", "finished_at", "quota_units_used"]
    list_filter = ["status", "job_type", "source"]


admin.site.register(VideoTrafficSource)
admin.site.register(VideoRetentionPoint)
