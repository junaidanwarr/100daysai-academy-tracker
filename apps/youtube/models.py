"""
YouTube channels, videos and analytics (specification sections 8-10).

The distinction that governs this whole module: the public Data API needs only
a channel ID, while impressions, CTR, average view duration, retention, traffic
sources and revenue come only from the Analytics API after the channel owner
grants OAuth. Analytics-only columns are therefore nullable and written solely
by ANALYTICS_API rows, so "we cannot obtain this" is representable and distinct
from a measured zero.
"""

import uuid

from django.db import models
from django.utils import timezone

from apps.core.enums import ChannelStatus, ContentType, MetricSource, MonetizationStatus, SyncStatus, VideoType
from apps.core.models import SoftDeleteModel


class YoutubeChannelQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def for_actor(self, actor):
        from apps.core.permissions import SCOPE_ALL, SCOPE_ASSIGNED, SCOPE_OWN, scope_for

        scope = scope_for(actor.role, "channel")
        if scope == SCOPE_ALL:
            return self
        if scope == SCOPE_ASSIGNED:
            return self.filter(student__instructor=actor)
        if scope == SCOPE_OWN:
            profile = getattr(actor, "student_profile", None)
            return self.filter(student=profile) if profile else self.none()
        return self.none()


class YoutubeChannelManager(models.Manager):
    def get_queryset(self):
        return YoutubeChannelQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)

    def for_actor(self, actor):
        return self.get_queryset().for_actor(actor)


class YoutubeChannel(SoftDeleteModel):
    student = models.ForeignKey("academy.Student", on_delete=models.CASCADE, related_name="channels")
    channel_name = models.CharField(max_length=200)
    channel_url = models.URLField(max_length=500, null=True, blank=True)
    # YouTube's own UC... identifier. Unique so one channel cannot silently
    # belong to two students (specification 23.6); a Super Admin override
    # records an audit entry and sets link_override_reason.
    youtube_channel_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    link_override_reason = models.TextField(null=True, blank=True)

    channel_creation_date = models.DateField(null=True, blank=True)
    niche = models.CharField(max_length=160, null=True, blank=True)
    sub_niche = models.CharField(max_length=160, null=True, blank=True)
    content_format = models.CharField(max_length=160, null=True, blank=True)
    target_audience = models.CharField(max_length=300, null=True, blank=True)
    target_country = models.CharField(max_length=120, null=True, blank=True)
    primary_language = models.CharField(max_length=80, null=True, blank=True)
    content_type = models.CharField(max_length=16, choices=ContentType.choices, default=ContentType.MIXED)
    upload_schedule = models.CharField(max_length=200, null=True, blank=True)

    ownership_verified = models.BooleanField(default=False)
    ownership_verified_at = models.DateTimeField(null=True, blank=True)
    is_brand_account = models.BooleanField(default=False)
    monetization_status = models.CharField(
        max_length=24, choices=MonetizationStatus.choices, default=MonetizationStatus.UNKNOWN
    )
    monetization_approved_at = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=24, choices=ChannelStatus.choices, default=ChannelStatus.PENDING, db_index=True
    )
    notes = models.TextField(null=True, blank=True)

    # Denormalised from the newest snapshot so lists and filters stay fast.
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_upload_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = YoutubeChannelManager()
    all_objects = models.Manager()

    class Meta:
        db_table = "youtube_channels"
        ordering = ["-created_at"]

    def __str__(self):
        return self.channel_name

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("youtube:channel_detail", args=[self.pk])

    @property
    def has_public_data(self) -> bool:
        return bool(self.youtube_channel_id)

    @property
    def has_analytics_access(self) -> bool:
        grant = getattr(self, "oauth_grant", None)
        return bool(grant and grant.revoked_at is None)

    @property
    def availability_reason(self) -> str | None:
        """Plain-English explanation of what cannot be fetched, and why."""
        if not self.has_public_data:
            return "No YouTube channel ID recorded, so no data can be fetched."
        if not self.has_analytics_access:
            return (
                "The student has not authorized YouTube Analytics access, "
                "so only public metrics are available."
            )
        return None


class ChannelOauthGrant(models.Model):
    """
    The student's explicit, revocable consent to private Analytics access
    (specification sections 9 and 21). Absent a grant, only PUBLIC_API data
    exists for the channel.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.OneToOneField(YoutubeChannel, on_delete=models.CASCADE, related_name="oauth_grant")
    google_account_email = models.EmailField(null=True, blank=True)
    # AES-256-GCM ciphertext. Never logged, never returned to the client.
    encrypted_refresh_token = models.TextField()
    scopes = models.JSONField(default=list)
    # Version of the consent text the student agreed to.
    consent_version = models.CharField(max_length=40)
    consented_at = models.DateTimeField(default=timezone.now)
    consent_ip_address = models.GenericIPAddressField(null=True, blank=True)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    # Set when the student disconnects or Google revokes the grant.
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_oauth_grants"

    def __str__(self):
        return f"OAuth grant for {self.channel_id}"


class Video(SoftDeleteModel):
    channel = models.ForeignKey(YoutubeChannel, on_delete=models.CASCADE, related_name="videos")
    youtube_video_id = models.CharField(max_length=32)
    title = models.CharField(max_length=300)
    url = models.URLField(max_length=500, null=True, blank=True)
    thumbnail_url = models.URLField(max_length=500, null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    video_type = models.CharField(max_length=16, choices=VideoType.choices, default=VideoType.LONG_FORM)
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "videos"
        ordering = ["-published_at"]
        constraints = [
            models.UniqueConstraint(fields=["channel", "youtube_video_id"], name="uniq_channel_video")
        ]
        indexes = [models.Index(fields=["channel", "-published_at"])]

    def __str__(self):
        return self.title


class ChannelSnapshot(models.Model):
    """Time series. One row per sync, never updated (specification section 10)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(YoutubeChannel, on_delete=models.CASCADE, related_name="snapshots")
    captured_at = models.DateTimeField(default=timezone.now, db_index=True)
    source = models.CharField(max_length=20, choices=MetricSource.choices)

    # --- Available from the public Data API ---
    subscriber_count = models.BigIntegerField(null=True, blank=True)
    view_count = models.BigIntegerField(null=True, blank=True)
    video_count = models.IntegerField(null=True, blank=True)
    public_video_count = models.IntegerField(null=True, blank=True)
    shorts_count = models.IntegerField(null=True, blank=True)
    long_form_count = models.IntegerField(null=True, blank=True)
    last_upload_at = models.DateTimeField(null=True, blank=True)

    # --- Analytics API only (null unless source = ANALYTICS_API) ---
    watch_time_minutes = models.BigIntegerField(null=True, blank=True)
    average_view_duration = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_revenue = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # --- Derived at write time ---
    avg_views_per_video = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    uploads_last_30_days = models.IntegerField(null=True, blank=True)
    days_since_last_upload = models.IntegerField(null=True, blank=True)
    channel_age_days = models.IntegerField(null=True, blank=True)
    growth_rate_percent = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "channel_snapshots"
        ordering = ["-captured_at"]
        indexes = [models.Index(fields=["channel", "-captured_at"])]

    def __str__(self):
        return f"{self.channel_id} @ {self.captured_at:%Y-%m-%d}"


class VideoSnapshot(models.Model):
    """
    Time series per video. Public columns are always populated; the rest only
    when an OAuth grant exists.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name="snapshots")
    captured_at = models.DateTimeField(default=timezone.now, db_index=True)
    source = models.CharField(max_length=20, choices=MetricSource.choices)

    # --- Public Data API ---
    views = models.BigIntegerField(null=True, blank=True)
    likes = models.BigIntegerField(null=True, blank=True)
    comments = models.BigIntegerField(null=True, blank=True)

    # --- Analytics API only ---
    impressions = models.BigIntegerField(null=True, blank=True)
    impressions_ctr_percent = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    watch_time_minutes = models.BigIntegerField(null=True, blank=True)
    average_view_duration = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    average_percentage_viewed = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    shares = models.BigIntegerField(null=True, blank=True)
    subscribers_gained = models.IntegerField(null=True, blank=True)
    subscribers_lost = models.IntegerField(null=True, blank=True)
    returning_viewers = models.BigIntegerField(null=True, blank=True)
    unique_viewers = models.BigIntegerField(null=True, blank=True)
    estimated_revenue = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "video_snapshots"
        ordering = ["-captured_at"]
        indexes = [models.Index(fields=["video", "-captured_at"])]

    def __str__(self):
        return f"{self.video_id} @ {self.captured_at:%Y-%m-%d}"


class VideoTrafficSource(models.Model):
    """Analytics API only."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot = models.ForeignKey(VideoSnapshot, on_delete=models.CASCADE, related_name="traffic_sources")
    # YouTube insightTrafficSourceType, e.g. YT_SEARCH, SUGGESTED_VIDEO.
    source_type = models.CharField(max_length=48)
    views = models.BigIntegerField()
    watch_time_minutes = models.BigIntegerField(null=True, blank=True)
    share_percent = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)

    class Meta:
        db_table = "video_traffic_sources"
        constraints = [
            models.UniqueConstraint(fields=["snapshot", "source_type"], name="uniq_snapshot_traffic_source")
        ]

    def __str__(self):
        return self.source_type


class VideoRetentionPoint(models.Model):
    """Analytics API only. One row per elapsed-time bucket of the retention curve."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot = models.ForeignKey(VideoSnapshot, on_delete=models.CASCADE, related_name="retention_points")
    elapsed_ratio = models.DecimalField(max_digits=6, decimal_places=4)
    audience_watch_ratio = models.DecimalField(max_digits=6, decimal_places=4)
    relative_retention_performance = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)

    class Meta:
        db_table = "video_retention_points"
        ordering = ["elapsed_ratio"]
        constraints = [
            models.UniqueConstraint(fields=["snapshot", "elapsed_ratio"], name="uniq_snapshot_elapsed_ratio")
        ]

    def __str__(self):
        return f"{self.elapsed_ratio}"


class SyncJobRun(models.Model):
    """
    One row per synchronisation attempt, for quota accounting and for showing
    "last successful sync" honestly (specification section 10).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(
        YoutubeChannel, null=True, blank=True, on_delete=models.CASCADE, related_name="sync_runs"
    )
    job_type = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=SyncStatus.choices, default=SyncStatus.PENDING)
    source = models.CharField(max_length=20, choices=MetricSource.choices, null=True, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    quota_units_used = models.IntegerField(default=0)
    items_processed = models.IntegerField(default=0)
    error_message = models.TextField(null=True, blank=True)
    details = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "sync_job_runs"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["channel", "-started_at"]),
            models.Index(fields=["job_type", "status"]),
        ]

    def __str__(self):
        return f"{self.job_type} {self.status}"
