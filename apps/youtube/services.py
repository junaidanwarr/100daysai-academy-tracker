"""
YouTube synchronisation, consent lifecycle and growth (specification sections 9-10).

Nothing here reaches the ORM without an acting user, and nothing writes a
metric without recording which API it came from. The two rules that govern the
module:

**A missing metric is written as null, never zero.** The database distinguishes
"we measured this and it is zero" from "we cannot obtain this". Every screen
depends on that distinction being preserved at write time — it cannot be
recovered later.

**A run always closes.** Every sync opens a SyncJobRun and finishes it as
SUCCESS, PARTIAL or FAILED with the quota it spent. "Last successful sync:
never" is then a fact on the page rather than an inference from silence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.core.audit import write_audit
from apps.core.context import system_actor
from apps.core.crypto import decrypt, encrypt
from apps.core.enums import AuditAction, MetricSource, SyncCadence, SyncStatus, VideoType
from apps.core.middleware import current_request_meta
from apps.core.permissions import assert_can
from apps.core.settings_service import YOUTUBE_SYNC_CADENCE, get_setting
from apps.youtube import api, oauth
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

logger = logging.getLogger(__name__)

JOB_CHANNEL_SYNC = "channel-sync"

# How many uploads to pull per sync. A channel with hundreds of videos is
# covered over successive runs rather than in one quota-devouring pass.
UPLOADS_PER_SYNC = 50

# Analytics reports are windowed; YouTube has no "all time to date" shorthand.
ANALYTICS_WINDOW_DAYS = 28

# YouTube's own reporting lags roughly two days, so asking for today returns
# rows that will change. The window ends here.
ANALYTICS_LAG_DAYS = 2

CADENCE_HOURS = {
    SyncCadence.EVERY_6_HOURS: 6,
    SyncCadence.EVERY_12_HOURS: 12,
    SyncCadence.DAILY: 24,
    SyncCadence.WEEKLY: 168,
}


class SyncNotConfigured(RuntimeError):
    """Raised rather than reporting a sync that could not have happened."""


@dataclass
class ChannelSyncResult:
    channel_id: str
    status: str
    quota_used: int = 0
    videos_seen: int = 0
    analytics: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "channel": self.channel_id,
            "status": self.status,
            "quota_used": self.quota_used,
            "videos": self.videos_seen,
            "analytics": self.analytics,
            "error": self.error,
        }


@dataclass
class SweepResult:
    considered: int = 0
    synced: int = 0
    failed: int = 0
    skipped: int = 0
    quota_used: int = 0
    quota_ceiling_reached: bool = False
    channels: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "considered": self.considered,
            "synced": self.synced,
            "failed": self.failed,
            "skipped": self.skipped,
            "quota_used": self.quota_used,
            "quota_ceiling_reached": self.quota_ceiling_reached,
            "channels": self.channels,
        }


# --- Configuration ----------------------------------------------------------


def public_sync_available() -> bool:
    return bool(settings.YOUTUBE_API_KEY)


def track_revenue() -> bool:
    return bool(getattr(settings, "YOUTUBE_TRACK_REVENUE", False))


def cadence() -> str:
    value = get_setting(YOUTUBE_SYNC_CADENCE)
    return value if value in SyncCadence.values else SyncCadence.DAILY


def cadence_hours() -> int | None:
    """None means manual-only: the scheduler runs, finds nothing due, and stops."""
    return CADENCE_HOURS.get(cadence())


def quota_used_today() -> int:
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = SyncJobRun.objects.filter(started_at__gte=start).aggregate(total=Sum("quota_units_used"))["total"]
    return total or 0


def quota_remaining_today() -> int:
    return max(0, settings.YOUTUBE_DAILY_QUOTA_UNITS - quota_used_today())


def analytics_window(today: date | None = None) -> tuple[date, date]:
    end = (today or timezone.localdate()) - timedelta(days=ANALYTICS_LAG_DAYS)
    return end - timedelta(days=ANALYTICS_WINDOW_DAYS), end


# --- Consent lifecycle ------------------------------------------------------


def start_connection(actor, channel: YoutubeChannel) -> str:
    """The consent URL for this channel. The student is the one who must click it."""
    assert_can(actor.role, "channel", "update")
    if not channel.youtube_channel_id:
        raise SyncNotConfigured(
            "Record the channel's YouTube channel ID before connecting analytics — "
            "without it there is nothing to attach the authorization to."
        )
    return oauth.consent_url(channel.pk, getattr(actor, "pk", None), include_revenue=track_revenue())


@transaction.atomic
def complete_connection(actor, channel: YoutubeChannel, code: str, *, ip_address: str | None = None) -> ChannelOauthGrant:
    """
    Exchanges the authorization code and stores the grant.

    Refuses when the Google account does not own the channel on record: a
    student who authorizes the wrong account would otherwise get a grant that
    quietly returns nothing for the life of the batch.
    """
    assert_can(actor.role, "channel", "update")
    token = oauth.exchange_code(code)

    if not token.refresh_token:
        raise SyncNotConfigured(
            "Google returned no refresh token. Disconnect the app at "
            "myaccount.google.com/permissions and try connecting again."
        )

    owned = oauth.owned_channel_ids(token.access_token)
    if owned and channel.youtube_channel_id and channel.youtube_channel_id not in owned:
        raise SyncNotConfigured(
            "That Google account does not own this channel. Sign in with the account "
            "that manages the channel and try again."
        )

    email = oauth.fetch_account_email(token.access_token)

    grant, _ = ChannelOauthGrant.objects.update_or_create(
        channel=channel,
        defaults={
            "google_account_email": email,
            "encrypted_refresh_token": encrypt(token.refresh_token),
            "scopes": token.scopes or oauth.scopes_for(include_revenue=track_revenue()),
            "consent_version": oauth.CONSENT_VERSION,
            "consented_at": timezone.now(),
            "consent_ip_address": ip_address,
            "access_token_expires_at": token.expires_at,
            "last_refreshed_at": timezone.now(),
            # A reconnection clears the prior revocation rather than leaving a
            # revoked-looking row that suppresses analytics forever.
            "revoked_at": None,
            "revoked_reason": None,
        },
    )

    write_audit(
        actor=actor,
        action=AuditAction.OAUTH_GRANT,
        entity_type="ChannelOauthGrant",
        entity_id=grant.pk,
        summary=(
            f'Analytics access authorized for "{channel.channel_name}"'
            + (f" via {email}" if email else "")
            + f" (consent {oauth.CONSENT_VERSION})"
        ),
        after={"scopes": grant.scopes, "consent_version": grant.consent_version},
        **current_request_meta(),
    )
    return grant


@transaction.atomic
def disconnect(actor, channel: YoutubeChannel, *, reason: str = "Disconnected by the account holder.") -> bool:
    """
    Revokes at Google and marks the grant revoked here.

    Snapshots already collected are deliberately kept: they are the historical
    record of what was true when it was measured, and deleting them would erase
    a student's own progress alongside the consent.
    """
    assert_can(actor.role, "channel", "update")
    grant = getattr(channel, "oauth_grant", None)
    if not grant or grant.revoked_at:
        return False

    revoked_at_google = False
    try:
        revoked_at_google = oauth.revoke(decrypt(grant.encrypted_refresh_token))
    except Exception:  # noqa: BLE001 — a local disconnect must always succeed
        logger.exception("could not decrypt or revoke the refresh token for channel %s", channel.pk)

    grant.revoked_at = timezone.now()
    grant.revoked_reason = reason
    # The token is useless now and there is no reason to keep ciphertext of a
    # credential that will never be exchanged again.
    grant.encrypted_refresh_token = ""
    grant.save(update_fields=["revoked_at", "revoked_reason", "encrypted_refresh_token", "updated_at"])

    write_audit(
        actor=actor,
        action=AuditAction.OAUTH_REVOKE,
        entity_type="ChannelOauthGrant",
        entity_id=grant.pk,
        summary=(
            f'Analytics access disconnected for "{channel.channel_name}": {reason}'
            + ("" if revoked_at_google else " (Google did not confirm the revocation)")
        ),
        **current_request_meta(),
    )
    return True


def _mark_grant_revoked(grant: ChannelOauthGrant, reason: str) -> None:
    """Google rejected the refresh token. Record it so the alert rule fires."""
    grant.revoked_at = timezone.now()
    grant.revoked_reason = reason
    grant.encrypted_refresh_token = ""
    grant.save(update_fields=["revoked_at", "revoked_reason", "encrypted_refresh_token", "updated_at"])
    write_audit(
        actor=system_actor("youtube-sync"),
        action=AuditAction.OAUTH_REVOKE,
        entity_type="ChannelOauthGrant",
        entity_id=grant.pk,
        summary=f"Analytics access stopped working and was marked revoked: {reason}",
    )


def active_grant(channel: YoutubeChannel) -> ChannelOauthGrant | None:
    grant = getattr(channel, "oauth_grant", None)
    if grant and grant.revoked_at is None and grant.encrypted_refresh_token:
        return grant
    return None


# --- The sync itself --------------------------------------------------------


def sync_channel(channel: YoutubeChannel, *, quota_budget: int | None = None) -> ChannelSyncResult:
    """
    One channel, public data then analytics if authorized.

    Runs as the system actor: it is a scheduled job, not a user action. Callers
    that expose it to a user check permission before getting here.
    """
    if not public_sync_available():
        raise SyncNotConfigured("YOUTUBE_API_KEY is not set, so no channel data can be fetched.")
    if not channel.youtube_channel_id:
        raise SyncNotConfigured("This channel has no YouTube channel ID recorded.")

    run = SyncJobRun.objects.create(
        channel=channel,
        job_type=JOB_CHANNEL_SYNC,
        status=SyncStatus.RUNNING,
        source=MetricSource.PUBLIC_API,
    )
    result = ChannelSyncResult(channel_id=str(channel.pk), status=SyncStatus.RUNNING)
    key = settings.YOUTUBE_API_KEY
    details: dict = {}

    try:
        stats = api.fetch_channel(key, channel.youtube_channel_id)
        result.quota_used += api.QUOTA_CHANNELS_LIST

        if stats is None:
            return _close(
                run,
                result,
                SyncStatus.FAILED,
                error="The channel could not be found. It may have been deleted, renamed or made private.",
                details={"youtube_channel_id": channel.youtube_channel_id},
            )

        videos: list[api.VideoData] = []
        if stats.uploads_playlist_id:
            budget_left = None if quota_budget is None else quota_budget - result.quota_used
            if budget_left is None or budget_left >= 2:
                video_ids, spent = api.fetch_upload_ids(
                    key, stats.uploads_playlist_id, limit=UPLOADS_PER_SYNC
                )
                result.quota_used += spent
                if video_ids:
                    videos, spent = api.fetch_videos(key, video_ids)
                    result.quota_used += spent
            else:
                details["uploads_skipped"] = "Daily quota ceiling reached before uploads could be fetched."

        with transaction.atomic():
            stored = _write_public(channel, stats, videos)
        result.videos_seen = len(videos)
        details.update(stored)

        grant = active_grant(channel)
        if grant:
            try:
                analytics_written = _sync_analytics(channel, grant, run)
                result.analytics = True
                details["analytics"] = analytics_written
            except api.YoutubeApiError as exc:
                if exc.is_auth_failure or exc.reason == "invalid_grant":
                    _mark_grant_revoked(grant, str(exc))
                    details["analytics_error"] = "Authorization is no longer valid; the student must reconnect."
                elif exc.is_quota_exceeded:
                    details["analytics_error"] = "Analytics quota exhausted for today."
                    return _close(run, result, SyncStatus.PARTIAL, details=details)
                else:
                    details["analytics_error"] = str(exc)
                    logger.warning("analytics sync failed for channel %s: %s", channel.pk, exc)
                return _close(run, result, SyncStatus.PARTIAL, details=details)

        partial = bool(details.get("uploads_skipped"))
        return _close(run, result, SyncStatus.PARTIAL if partial else SyncStatus.SUCCESS, details=details)

    except api.YoutubeApiError as exc:
        status = SyncStatus.PARTIAL if exc.is_quota_exceeded else SyncStatus.FAILED
        return _close(run, result, status, error=str(exc), details=details)
    except Exception as exc:  # noqa: BLE001 — the run must close whatever happens
        logger.exception("channel sync crashed for %s", channel.pk)
        return _close(run, result, SyncStatus.FAILED, error=str(exc), details=details)


def _close(
    run: SyncJobRun,
    result: ChannelSyncResult,
    status: str,
    *,
    error: str | None = None,
    details: dict | None = None,
) -> ChannelSyncResult:
    run.status = status
    run.finished_at = timezone.now()
    run.quota_units_used = result.quota_used
    run.items_processed = result.videos_seen
    run.error_message = error
    run.details = details or None
    run.save(
        update_fields=[
            "status", "finished_at", "quota_units_used", "items_processed", "error_message", "details"
        ]
    )
    result.status = status
    result.error = error
    return result


def _video_type(video: api.VideoData) -> str:
    if video.is_live:
        return VideoType.LIVE
    if video.duration_seconds and video.duration_seconds <= api.SHORT_MAX_SECONDS:
        return VideoType.SHORT
    return VideoType.LONG_FORM


def _write_public(channel: YoutubeChannel, stats: api.ChannelStats, videos: list[api.VideoData]) -> dict:
    """Upserts videos, writes one channel snapshot and one snapshot per video."""
    now = timezone.now()
    created = updated = 0
    shorts = long_form = 0
    last_upload = None

    for item in videos:
        if not item.youtube_video_id:
            continue
        video, was_created = Video.all_objects.update_or_create(
            channel=channel,
            youtube_video_id=item.youtube_video_id,
            defaults={
                "title": item.title or "(untitled)",
                "url": f"https://www.youtube.com/watch?v={item.youtube_video_id}",
                "thumbnail_url": item.thumbnail_url,
                "published_at": item.published_at,
                "duration_seconds": item.duration_seconds,
                "video_type": _video_type(item),
                "description": item.description,
                "deleted_at": None,
            },
        )
        created += was_created
        updated += not was_created

        if video.video_type == VideoType.SHORT:
            shorts += 1
        elif video.video_type == VideoType.LONG_FORM:
            long_form += 1
        if item.published_at and (last_upload is None or item.published_at > last_upload):
            last_upload = item.published_at

        VideoSnapshot.objects.create(
            video=video,
            captured_at=now,
            source=MetricSource.PUBLIC_API,
            views=item.views,
            likes=item.likes,
            comments=item.comments,
        )

    # Derived figures, computed here so every screen reads the same numbers
    # rather than each recomputing them slightly differently.
    thirty_days_ago = now - timedelta(days=30)
    recent_uploads = sum(1 for v in videos if v.published_at and v.published_at >= thirty_days_ago)
    age_days = (now - stats.published_at).days if stats.published_at else None
    avg_views = None
    if stats.view_count is not None and stats.video_count:
        avg_views = Decimal(stats.view_count) / Decimal(stats.video_count)

    snapshot = ChannelSnapshot.objects.create(
        channel=channel,
        captured_at=now,
        source=MetricSource.PUBLIC_API,
        subscriber_count=stats.subscriber_count,
        view_count=stats.view_count,
        video_count=stats.video_count,
        public_video_count=len(videos) or None,
        shorts_count=shorts or None,
        long_form_count=long_form or None,
        last_upload_at=last_upload,
        avg_views_per_video=avg_views.quantize(Decimal("0.01")) if avg_views is not None else None,
        uploads_last_30_days=recent_uploads,
        days_since_last_upload=(now - last_upload).days if last_upload else None,
        channel_age_days=age_days,
        growth_rate_percent=_growth_rate(channel, stats.subscriber_count),
    )

    fields = ["last_synced_at", "updated_at"]
    channel.last_synced_at = now
    if last_upload and (channel.last_upload_at is None or last_upload > channel.last_upload_at):
        channel.last_upload_at = last_upload
        fields.insert(1, "last_upload_at")
    if stats.published_at and not channel.channel_creation_date:
        channel.channel_creation_date = stats.published_at.date()
        fields.insert(1, "channel_creation_date")
    channel.save(update_fields=fields)

    return {
        "snapshot": str(snapshot.pk),
        "videos_created": created,
        "videos_updated": updated,
        "subscriber_count_hidden": stats.subscriber_count_hidden,
    }


def _growth_rate(channel: YoutubeChannel, subscribers: int | None) -> Decimal | None:
    """Subscriber growth against the closest snapshot from roughly 30 days ago."""
    if subscribers is None:
        return None
    baseline = (
        ChannelSnapshot.objects.filter(
            channel=channel,
            source=MetricSource.PUBLIC_API,
            subscriber_count__isnull=False,
            captured_at__lte=timezone.now() - timedelta(days=30),
        )
        .order_by("-captured_at")
        .first()
    )
    if not baseline or not baseline.subscriber_count:
        return None
    delta = Decimal(subscribers - baseline.subscriber_count) / Decimal(baseline.subscriber_count) * 100
    return delta.quantize(Decimal("0.01"))


def _sync_analytics(channel: YoutubeChannel, grant: ChannelOauthGrant, run: SyncJobRun) -> dict:
    """
    Private metrics. Written as ANALYTICS_API rows so they can never be
    confused with public figures, and so a later disconnection leaves the
    historical record intact and correctly attributed.
    """
    token = oauth.refresh_access_token(decrypt(grant.encrypted_refresh_token))
    grant.access_token_expires_at = token.expires_at
    grant.last_refreshed_at = timezone.now()
    grant.save(update_fields=["access_token_expires_at", "last_refreshed_at", "updated_at"])

    start, end = analytics_window()
    revenue = track_revenue() and oauth.MONETARY_SCOPE in (grant.scopes or [])
    now = timezone.now()

    channel_report = api.fetch_channel_analytics(
        token.access_token, channel.youtube_channel_id, start, end, include_revenue=revenue
    )

    videos = list(channel.videos.all()[:UPLOADS_PER_SYNC])
    by_youtube_id = {v.youtube_video_id: v for v in videos}
    video_reports = api.fetch_video_analytics(
        token.access_token,
        channel.youtube_channel_id,
        list(by_youtube_id),
        start,
        end,
        include_revenue=revenue,
    )

    with transaction.atomic():
        ChannelSnapshot.objects.create(
            channel=channel,
            captured_at=now,
            source=MetricSource.ANALYTICS_API,
            watch_time_minutes=channel_report.watch_time_minutes,
            average_view_duration=_dec(channel_report.average_view_duration),
            estimated_revenue=_dec(channel_report.estimated_revenue),
        )

        written = 0
        for youtube_id, report in video_reports.items():
            video = by_youtube_id.get(youtube_id)
            if not video:
                continue
            snapshot = VideoSnapshot.objects.create(
                video=video,
                captured_at=now,
                source=MetricSource.ANALYTICS_API,
                impressions=report.impressions,
                impressions_ctr_percent=_dec(report.impressions_ctr_percent),
                watch_time_minutes=report.watch_time_minutes,
                average_view_duration=_dec(report.average_view_duration),
                average_percentage_viewed=_dec(report.average_percentage_viewed),
                shares=report.shares,
                subscribers_gained=report.subscribers_gained,
                subscribers_lost=report.subscribers_lost,
                estimated_revenue=_dec(report.estimated_revenue),
            )
            written += 1

            for source in report.traffic_sources:
                VideoTrafficSource.objects.update_or_create(
                    snapshot=snapshot,
                    source_type=source["source_type"],
                    defaults={
                        "views": source.get("views") or 0,
                        "watch_time_minutes": source.get("watch_time_minutes"),
                    },
                )
            _write_share_percentages(snapshot)

    run.source = MetricSource.ANALYTICS_API
    run.save(update_fields=["source"])
    return {"video_snapshots": written, "window": f"{start} to {end}", "revenue": revenue}


def _write_share_percentages(snapshot: VideoSnapshot) -> None:
    rows = list(snapshot.traffic_sources.all())
    total = sum(row.views for row in rows)
    if not total:
        return
    for row in rows:
        row.share_percent = (Decimal(row.views) / Decimal(total) * 100).quantize(Decimal("0.001"))
        row.save(update_fields=["share_percent"])


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def sync_retention(actor, video: Video) -> int:
    """
    Fetches the retention curve for one video, on request.

    Deliberately not part of the scheduled sweep: it is one Analytics report per
    video, which would multiply the cost of every run by the size of the channel.
    """
    assert_can(actor.role, "analytics", "read")
    channel = video.channel
    grant = active_grant(channel)
    if not grant:
        raise SyncNotConfigured(
            "Audience retention requires the student's YouTube Analytics authorization."
        )

    token = oauth.refresh_access_token(decrypt(grant.encrypted_refresh_token))
    start, end = analytics_window()
    points = api.fetch_retention(token.access_token, channel.youtube_channel_id, video.youtube_video_id, start, end)
    if not points:
        return 0

    with transaction.atomic():
        snapshot = VideoSnapshot.objects.create(
            video=video, captured_at=timezone.now(), source=MetricSource.ANALYTICS_API
        )
        VideoRetentionPoint.objects.bulk_create(
            [
                VideoRetentionPoint(
                    snapshot=snapshot,
                    elapsed_ratio=_dec(point["elapsed_ratio"]),
                    audience_watch_ratio=_dec(point["audience_watch_ratio"]),
                    relative_retention_performance=_dec(point.get("relative_retention_performance")),
                )
                for point in points
            ]
        )
    return len(points)


# --- The scheduled sweep ----------------------------------------------------


def due_channels(*, force: bool = False):
    """
    Channels eligible this cycle, oldest-synced first so nothing starves when
    the quota ceiling cuts a run short.
    """
    queryset = YoutubeChannel.objects.filter(youtube_channel_id__isnull=False).exclude(youtube_channel_id="")

    if not force:
        hours = cadence_hours()
        if hours is None:  # cadence is MANUAL: the sweep finds nothing due
            return queryset.none()
        cutoff = timezone.now() - timedelta(hours=hours)
        queryset = queryset.filter(Q(last_synced_at__isnull=True) | Q(last_synced_at__lt=cutoff))

    # Never-synced channels first, then the stalest.
    return queryset.select_related("oauth_grant").order_by(F("last_synced_at").asc(nulls_first=True))


def sync_all(*, force: bool = False, limit: int | None = None) -> SweepResult:
    """
    The scheduled pass. Stops at the daily quota ceiling and marks the run
    PARTIAL rather than producing a wall of 403s.
    """
    result = SweepResult()
    if not public_sync_available():
        raise SyncNotConfigured("YOUTUBE_API_KEY is not set, so no channel data can be fetched.")

    channels = due_channels(force=force)
    if limit:
        channels = channels[:limit]

    budget = quota_remaining_today()

    for channel in channels:
        result.considered += 1
        if budget <= 0:
            result.skipped += 1
            result.quota_ceiling_reached = True
            continue
        try:
            outcome = sync_channel(channel, quota_budget=budget)
        except SyncNotConfigured as exc:
            result.skipped += 1
            result.channels.append({"channel": str(channel.pk), "skipped": str(exc)})
            continue

        budget -= outcome.quota_used
        result.quota_used += outcome.quota_used
        result.channels.append(outcome.as_dict())
        if outcome.status == SyncStatus.FAILED:
            result.failed += 1
        else:
            result.synced += 1

    if result.quota_ceiling_reached:
        logger.warning(
            "YouTube daily quota ceiling reached; %s channel(s) deferred to the next cycle.", result.skipped
        )
    return result


def sync_now(actor, channel: YoutubeChannel) -> ChannelSyncResult:
    """Manual sync from the channel page. Audited, because a person chose to spend quota."""
    assert_can(actor.role, "channel", "update")
    if quota_remaining_today() <= 0:
        raise SyncNotConfigured(
            "The daily YouTube quota has been used. Synchronisation resumes automatically tomorrow."
        )

    outcome = sync_channel(channel, quota_budget=quota_remaining_today())
    write_audit(
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="YoutubeChannel",
        entity_id=channel.pk,
        summary=(
            f'Manual YouTube sync of "{channel.channel_name}": {outcome.status.lower()}, '
            f"{outcome.videos_seen} video(s), {outcome.quota_used} quota unit(s)"
            + (f" — {outcome.error}" if outcome.error else "")
        ),
        **current_request_meta(),
    )
    return outcome


# --- Reading -----------------------------------------------------------------


def latest_snapshots(channel: YoutubeChannel) -> tuple[ChannelSnapshot | None, ChannelSnapshot | None]:
    """
    The newest of each kind, kept separate on purpose. Merging them would let
    an analytics row's nulls overwrite public figures on the page.
    """
    public = channel.snapshots.filter(source=MetricSource.PUBLIC_API).first()
    private = channel.snapshots.filter(source=MetricSource.ANALYTICS_API).first()
    return public, private


def growth_series(channel: YoutubeChannel, *, days: int = 30) -> list[dict]:
    """
    The subscriber and view history. Derived by querying snapshots rather than
    storing deltas, so any two dates can be compared for the life of the record.
    """
    since = timezone.now() - timedelta(days=days)
    rows = (
        channel.snapshots.filter(source=MetricSource.PUBLIC_API, captured_at__gte=since)
        .order_by("captured_at")
        .values("captured_at", "subscriber_count", "view_count", "video_count")
    )
    return list(rows)


def growth_summary(channel: YoutubeChannel) -> dict:
    """Daily, weekly and monthly change, each labelled when the baseline is missing."""
    latest = channel.snapshots.filter(source=MetricSource.PUBLIC_API).first()
    if not latest:
        return {
            "available": False,
            "reason": "No public snapshot has been recorded for this channel yet.",
            "periods": [],
        }

    periods = []
    for label, days in (("Last 24 hours", 1), ("Last 7 days", 7), ("Last 30 days", 30)):
        baseline = (
            channel.snapshots.filter(
                source=MetricSource.PUBLIC_API, captured_at__lte=latest.captured_at - timedelta(days=days)
            )
            .order_by("-captured_at")
            .first()
        )
        if not baseline:
            periods.append(
                {
                    "label": label,
                    "available": False,
                    "reason": f"No snapshot from {days} day(s) ago to compare against yet.",
                }
            )
            continue
        periods.append(
            {
                "label": label,
                "available": True,
                "subscribers": _delta(latest.subscriber_count, baseline.subscriber_count),
                "views": _delta(latest.view_count, baseline.view_count),
                "videos": _delta(latest.video_count, baseline.video_count),
                "since": baseline.captured_at,
            }
        )

    return {"available": True, "reason": None, "periods": periods, "captured_at": latest.captured_at}


def _delta(current: int | None, previous: int | None) -> int | None:
    """None in, None out. A missing figure never becomes a zero change."""
    if current is None or previous is None:
        return None
    return current - previous


def availability(channel: YoutubeChannel) -> dict:
    """
    Exactly which metrics this channel can report, and a plain-English reason
    for each that it cannot. The screens render both lists so a blank is never
    ambiguous.
    """
    public_ready = bool(channel.youtube_channel_id) and public_sync_available()
    grant = active_grant(channel)

    if not channel.youtube_channel_id:
        public_reason = "No YouTube channel ID is recorded, so nothing can be fetched."
    elif not public_sync_available():
        public_reason = "YOUTUBE_API_KEY is not configured on this deployment."
    else:
        public_reason = None

    if grant:
        private_reason = None
    elif not oauth.is_configured():
        private_reason = "Google OAuth is not configured on this deployment, so consent cannot be requested."
    elif getattr(channel, "oauth_grant", None) and channel.oauth_grant.revoked_at:
        private_reason = (
            "Analytics access was disconnected on "
            f"{channel.oauth_grant.revoked_at:%d %b %Y}. The student must reconnect to resume."
        )
    else:
        private_reason = (
            "The student has not authorized YouTube Analytics access. "
            "There is no other lawful route to these figures."
        )

    return {
        "public_available": public_ready,
        "public_reason": public_reason,
        "private_available": bool(grant),
        "private_reason": private_reason,
        "grant": grant,
        "connected_email": grant.google_account_email if grant else None,
        "consented_at": grant.consented_at if grant else None,
        "revenue_authorized": bool(grant and oauth.MONETARY_SCOPE in (grant.scopes or [])),
    }
