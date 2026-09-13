"""
HTTP clients for the two YouTube APIs (specification sections 9 and 10).

Written against the standard library only — no google-api-python-client — for
the same reason the rest of the project has no Node toolchain: one fewer
dependency tree to keep current, and the three endpoints actually used are
plain GETs.

Every call goes through ``transport``, a module-level callable. Tests replace
it with a canned-response function, so the sync engine is exercised end to end
without touching the network or needing credentials.

Quota costs are YouTube's published figures: a ``list`` call costs 1 unit
regardless of how many parts or items it returns, which is why videos are
fetched 50 at a time.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone as dt_timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

DATA_API_ROOT = "https://www.googleapis.com/youtube/v3"
ANALYTICS_API_ROOT = "https://youtubeanalytics.googleapis.com/v2"

# Units charged per call. Used for the daily ceiling in services.sync_channel.
QUOTA_CHANNELS_LIST = 1
QUOTA_PLAYLIST_ITEMS_LIST = 1
QUOTA_VIDEOS_LIST = 1

# The Analytics API bills against a separate quota, so its calls are recorded
# on the run but not counted against YOUTUBE_DAILY_QUOTA_UNITS.
MAX_VIDEO_IDS_PER_CALL = 50

# YouTube treats anything of 3 minutes or less as a Short.
SHORT_MAX_SECONDS = 180


class YoutubeApiError(Exception):
    """A call failed. ``retryable`` tells the sync engine whether to give up."""

    def __init__(self, message: str, *, status: int | None = None, reason: str = ""):
        super().__init__(message)
        self.status = status
        self.reason = reason

    @property
    def is_quota_exceeded(self) -> bool:
        return self.reason in {"quotaExceeded", "rateLimitExceeded", "dailyLimitExceeded"}

    @property
    def is_auth_failure(self) -> bool:
        """Token revoked, expired or scope withdrawn — reconnection required."""
        return self.status in (401, 403) and self.reason in {
            "authError",
            "invalid_grant",
            "forbidden",
            "insufficientPermissions",
            "unauthorized",
        }

    @property
    def is_retryable(self) -> bool:
        return self.status is not None and 500 <= self.status < 600


def _default_transport(url: str, *, headers: dict | None = None, data: bytes | None = None) -> dict:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise YoutubeApiError(
            _error_message(body, exc.code), status=exc.code, reason=_error_reason(body)
        ) from exc
    except urllib.error.URLError as exc:  # DNS, TLS, connection refused
        raise YoutubeApiError(f"Could not reach {url.split('?')[0]}: {exc.reason}", status=None) from exc


def _error_reason(body: str) -> str:
    try:
        payload = json.loads(body)
    except ValueError:
        return ""
    error = payload.get("error", {})
    if isinstance(error, str):  # OAuth token endpoint shape
        return error
    errors = error.get("errors") or []
    return (errors[0].get("reason") if errors else "") or error.get("status", "")


def _error_message(body: str, status: int) -> str:
    try:
        payload = json.loads(body)
    except ValueError:
        return f"HTTP {status}"
    error = payload.get("error", {})
    if isinstance(error, str):
        return payload.get("error_description") or error
    return error.get("message") or f"HTTP {status}"


# Swapped out in tests. Never call urllib directly elsewhere in this package.
transport: Callable[..., dict] = _default_transport


def _get(root: str, path: str, params: dict, *, access_token: str | None = None) -> dict:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    headers = {"Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return transport(f"{root}/{path}?{query}", headers=headers)


# --- Parsing helpers --------------------------------------------------------

_DURATION = re.compile(
    r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?"
)


def parse_duration(value: str | None) -> int | None:
    """ISO 8601 duration to seconds. Returns None for live streams (``P0D``)."""
    if not value:
        return None
    match = _DURATION.fullmatch(value)
    if not match:
        return None
    parts = {k: int(v or 0) for k, v in match.groupdict().items()}
    total = parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]
    return total or None


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt_timezone.utc)
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    """
    None and zero are different facts. ``subscriberCount`` is absent when the
    channel hides it, and that must not become a zero.
    """
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --- Data API (public, needs only an API key) -------------------------------


@dataclass
class ChannelStats:
    youtube_channel_id: str
    title: str
    published_at: datetime | None
    subscriber_count: int | None
    view_count: int | None
    video_count: int | None
    uploads_playlist_id: str | None
    subscriber_count_hidden: bool = False


@dataclass
class VideoData:
    youtube_video_id: str
    title: str
    description: str = ""
    thumbnail_url: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    is_live: bool = False
    views: int | None = None
    likes: int | None = None
    comments: int | None = None


def fetch_channel(api_key: str, youtube_channel_id: str) -> ChannelStats | None:
    """``channels.list``. Returns None when the channel is deleted or private."""
    payload = _get(
        DATA_API_ROOT,
        "channels",
        {"part": "snippet,statistics,contentDetails", "id": youtube_channel_id, "key": api_key},
    )
    items = payload.get("items") or []
    if not items:
        return None
    item = items[0]
    stats = item.get("statistics", {})
    uploads = (item.get("contentDetails", {}).get("relatedPlaylists", {}) or {}).get("uploads")
    return ChannelStats(
        youtube_channel_id=item.get("id", youtube_channel_id),
        title=item.get("snippet", {}).get("title", ""),
        published_at=parse_timestamp(item.get("snippet", {}).get("publishedAt")),
        subscriber_count=None if stats.get("hiddenSubscriberCount") else _int_or_none(stats.get("subscriberCount")),
        view_count=_int_or_none(stats.get("viewCount")),
        video_count=_int_or_none(stats.get("videoCount")),
        uploads_playlist_id=uploads,
        subscriber_count_hidden=bool(stats.get("hiddenSubscriberCount")),
    )


def fetch_upload_ids(api_key: str, uploads_playlist_id: str, *, limit: int = 50) -> tuple[list[str], int]:
    """
    ``playlistItems.list`` over the uploads playlist, newest first.

    Returns the video IDs and the quota spent, so a channel with 400 uploads
    does not silently cost eight times what the caller budgeted.
    """
    ids: list[str] = []
    spent = 0
    page_token = None
    while len(ids) < limit:
        payload = _get(
            DATA_API_ROOT,
            "playlistItems",
            {
                "part": "contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": min(50, limit - len(ids)),
                "pageToken": page_token,
                "key": api_key,
            },
        )
        spent += QUOTA_PLAYLIST_ITEMS_LIST
        for item in payload.get("items") or []:
            video_id = (item.get("contentDetails") or {}).get("videoId")
            if video_id:
                ids.append(video_id)
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
    return ids, spent


def fetch_videos(api_key: str, video_ids: list[str]) -> tuple[list[VideoData], int]:
    """``videos.list`` in batches of 50. Returns the videos and quota spent."""
    out: list[VideoData] = []
    spent = 0
    for start in range(0, len(video_ids), MAX_VIDEO_IDS_PER_CALL):
        batch = video_ids[start : start + MAX_VIDEO_IDS_PER_CALL]
        payload = _get(
            DATA_API_ROOT,
            "videos",
            {"part": "snippet,contentDetails,statistics", "id": ",".join(batch), "key": api_key},
        )
        spent += QUOTA_VIDEOS_LIST
        for item in payload.get("items") or []:
            snippet = item.get("snippet", {})
            details = item.get("contentDetails", {})
            stats = item.get("statistics", {})
            thumbnails = snippet.get("thumbnails", {}) or {}
            best = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
            duration = parse_duration(details.get("duration"))
            out.append(
                VideoData(
                    youtube_video_id=item.get("id", ""),
                    title=snippet.get("title", "")[:300],
                    description=snippet.get("description", "") or "",
                    thumbnail_url=best.get("url"),
                    published_at=parse_timestamp(snippet.get("publishedAt")),
                    duration_seconds=duration,
                    is_live=snippet.get("liveBroadcastContent") in {"live", "upcoming"},
                    views=_int_or_none(stats.get("viewCount")),
                    likes=_int_or_none(stats.get("likeCount")),
                    comments=_int_or_none(stats.get("commentCount")),
                )
            )
    return out, spent


# --- Analytics API (private, needs the owner's OAuth grant) -----------------


@dataclass
class ChannelAnalytics:
    watch_time_minutes: int | None = None
    average_view_duration: float | None = None
    subscribers_gained: int | None = None
    subscribers_lost: int | None = None
    estimated_revenue: float | None = None


@dataclass
class VideoAnalytics:
    youtube_video_id: str
    impressions: int | None = None
    impressions_ctr_percent: float | None = None
    watch_time_minutes: int | None = None
    average_view_duration: float | None = None
    average_percentage_viewed: float | None = None
    shares: int | None = None
    subscribers_gained: int | None = None
    subscribers_lost: int | None = None
    estimated_revenue: float | None = None
    traffic_sources: list[dict] = field(default_factory=list)
    retention: list[dict] = field(default_factory=list)


CHANNEL_METRICS = [
    "estimatedMinutesWatched",
    "averageViewDuration",
    "subscribersGained",
    "subscribersLost",
]

VIDEO_METRICS = [
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "shares",
    "subscribersGained",
    "subscribersLost",
]

# Impressions and CTR live in a different metric group and are requested
# separately, because asking for them alongside the rest returns a 400 for
# channels that have no impression data at all.
IMPRESSION_METRICS = ["impressions", "impressionsClickThroughRate"]

REVENUE_METRICS = ["estimatedRevenue"]


def _rows_to_dict(payload: dict) -> list[dict]:
    headers = [column.get("name") for column in payload.get("columnHeaders") or []]
    return [dict(zip(headers, row)) for row in payload.get("rows") or []]


def _report(access_token: str, params: dict) -> list[dict]:
    payload = _get(ANALYTICS_API_ROOT, "reports", params, access_token=access_token)
    return _rows_to_dict(payload)


def _query(channel_id: str, start: date, end: date, metrics: list[str], **extra) -> dict:
    return {
        "ids": f"channel=={channel_id}",
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "metrics": ",".join(metrics),
        **extra,
    }


def _tolerant(fn, default):
    """
    Optional metric groups. Impressions are unavailable on some channels and
    revenue requires the monetary scope on a monetised channel; neither absence
    is an error, and neither should abort a sync that is otherwise succeeding.
    """
    try:
        return fn()
    except YoutubeApiError as exc:
        if exc.is_quota_exceeded or exc.is_auth_failure:
            raise
        logger.info("optional analytics metrics unavailable: %s", exc)
        return default


def fetch_channel_analytics(
    access_token: str, youtube_channel_id: str, start: date, end: date, *, include_revenue: bool = False
) -> ChannelAnalytics:
    rows = _report(access_token, _query(youtube_channel_id, start, end, CHANNEL_METRICS))
    row = rows[0] if rows else {}

    revenue = None
    if include_revenue:
        revenue_rows = _tolerant(
            lambda: _report(access_token, _query(youtube_channel_id, start, end, REVENUE_METRICS)), []
        )
        if revenue_rows:
            revenue = revenue_rows[0].get("estimatedRevenue")

    minutes = row.get("estimatedMinutesWatched")
    duration = row.get("averageViewDuration")
    return ChannelAnalytics(
        watch_time_minutes=int(minutes) if minutes is not None else None,
        average_view_duration=float(duration) if duration is not None else None,
        subscribers_gained=_int_or_none(row.get("subscribersGained")),
        subscribers_lost=_int_or_none(row.get("subscribersLost")),
        estimated_revenue=float(revenue) if revenue is not None else None,
    )


def fetch_video_analytics(
    access_token: str,
    youtube_channel_id: str,
    video_ids: list[str],
    start: date,
    end: date,
    *,
    include_revenue: bool = False,
) -> dict[str, VideoAnalytics]:
    """
    One report for the whole set, dimensioned by video. Cheaper and far fewer
    round trips than per-video calls.
    """
    if not video_ids:
        return {}

    filters = f"video=={','.join(video_ids[:200])}"
    out = {vid: VideoAnalytics(youtube_video_id=vid) for vid in video_ids}

    for row in _report(
        access_token,
        _query(youtube_channel_id, start, end, VIDEO_METRICS, dimensions="video", filters=filters),
    ):
        record = out.get(row.get("video"))
        if not record:
            continue
        record.watch_time_minutes = _int_or_none(row.get("estimatedMinutesWatched"))
        duration = row.get("averageViewDuration")
        record.average_view_duration = float(duration) if duration is not None else None
        percentage = row.get("averageViewPercentage")
        record.average_percentage_viewed = float(percentage) if percentage is not None else None
        record.shares = _int_or_none(row.get("shares"))
        record.subscribers_gained = _int_or_none(row.get("subscribersGained"))
        record.subscribers_lost = _int_or_none(row.get("subscribersLost"))

    for row in _tolerant(
        lambda: _report(
            access_token,
            _query(youtube_channel_id, start, end, IMPRESSION_METRICS, dimensions="video", filters=filters),
        ),
        [],
    ):
        record = out.get(row.get("video"))
        if not record:
            continue
        record.impressions = _int_or_none(row.get("impressions"))
        ctr = row.get("impressionsClickThroughRate")
        # YouTube returns a ratio; the column stores a percentage.
        record.impressions_ctr_percent = round(float(ctr) * 100, 3) if ctr is not None else None

    for row in _tolerant(
        lambda: _report(
            access_token,
            _query(
                youtube_channel_id,
                start,
                end,
                ["views", "estimatedMinutesWatched"],
                dimensions="video,insightTrafficSourceType",
                filters=filters,
            ),
        ),
        [],
    ):
        record = out.get(row.get("video"))
        if not record:
            continue
        record.traffic_sources.append(
            {
                "source_type": row.get("insightTrafficSourceType", "UNKNOWN"),
                "views": _int_or_none(row.get("views")) or 0,
                "watch_time_minutes": _int_or_none(row.get("estimatedMinutesWatched")),
            }
        )

    if include_revenue:
        for row in _tolerant(
            lambda: _report(
                access_token,
                _query(youtube_channel_id, start, end, REVENUE_METRICS, dimensions="video", filters=filters),
            ),
            [],
        ):
            record = out.get(row.get("video"))
            if record and row.get("estimatedRevenue") is not None:
                record.estimated_revenue = float(row["estimatedRevenue"])

    return out


def fetch_retention(access_token: str, youtube_channel_id: str, video_id: str, start: date, end: date) -> list[dict]:
    """
    The audience retention curve for one video. Requested only on demand — it
    is one report per video, so syncing it for every upload is wasteful.
    """
    rows = _tolerant(
        lambda: _report(
            access_token,
            _query(
                youtube_channel_id,
                start,
                end,
                ["audienceWatchRatio", "relativeRetentionPerformance"],
                dimensions="elapsedVideoTimeRatio",
                filters=f"video=={video_id}",
            ),
        ),
        [],
    )
    return [
        {
            "elapsed_ratio": row.get("elapsedVideoTimeRatio"),
            "audience_watch_ratio": row.get("audienceWatchRatio"),
            "relative_retention_performance": row.get("relativeRetentionPerformance"),
        }
        for row in rows
        if row.get("elapsedVideoTimeRatio") is not None
    ]
