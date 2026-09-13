"""
Getting the numbers the niche-validation rules need onto a competitor row.

Two routes, and the row records which one it came from:

* **Fetched** — paste a channel URL and the public YouTube Data API supplies
  video count, views, subscribers and the per-video view series. Costs 3 quota
  units per channel and needs only ``YOUTUBE_API_KEY``; no student consent is
  involved, because every figure here is public.
* **Typed** — the student enters what they found by hand. Recorded as MANUAL so
  an instructor can see at a glance which channels were machine-checked and
  which were taken on trust.

The rules themselves live in ``competitor_rules`` and never call this module,
so they stay pure and testable.
"""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.utils import timezone

from apps.core.enums import MetricSource
from apps.core.settings_service import competitor_thresholds
from apps.research.competitor_rules import ChannelVerdict, SubmissionVerdict, evaluate_channel
from apps.youtube import api

logger = logging.getLogger(__name__)

# How many recent uploads to read for the growth series. Enough to see a trend
# without paying for a large channel's entire back catalogue.
GROWTH_SAMPLE_SIZE = 30


class CompetitorLookupError(RuntimeError):
    """Raised with a sentence the student can act on, not an API error code."""


def _extract_channel_id(value: str) -> str | None:
    """A bare UC… id, or one embedded in a /channel/ URL."""
    value = (value or "").strip()
    if re.fullmatch(r"UC[\w-]{20,}", value):
        return value
    match = re.search(r"/channel/(UC[\w-]{20,})", value)
    return match.group(1) if match else None


def _extract_handle(value: str) -> str | None:
    """An @handle, either bare or inside a youtube.com/@handle URL."""
    value = (value or "").strip()
    if value.startswith("@") and len(value) > 1:
        return value[1:]
    match = re.search(r"youtube\.com/@([\w.-]+)", value)
    return match.group(1) if match else None


def resolve_channel_id(url_or_id: str) -> str:
    """
    Turns whatever the student pasted into a UC… channel id.

    A handle needs a search call, which costs 100 quota units against the 1 a
    direct lookup costs — so a pasted /channel/ URL is very much the cheaper
    thing to ask for, and the error message says so.
    """
    channel_id = _extract_channel_id(url_or_id)
    if channel_id:
        return channel_id

    handle = _extract_handle(url_or_id)
    if not handle:
        raise CompetitorLookupError(
            "That does not look like a YouTube channel address. Paste the channel URL "
            "(youtube.com/@name or youtube.com/channel/UC…) or the UC… channel ID."
        )

    payload = api._get(  # noqa: SLF001 — same package, one shared request path
        api.DATA_API_ROOT,
        "channels",
        {"part": "id", "forHandle": f"@{handle}", "key": settings.YOUTUBE_API_KEY},
    )
    items = payload.get("items") or []
    if not items:
        raise CompetitorLookupError(
            f"No channel found for @{handle}. Check the handle, or paste the youtube.com/channel/UC… address instead."
        )
    return items[0]["id"]


def fetch_metrics(url_or_id: str) -> dict:
    """
    Public figures for one competitor channel.

    Returns a dict ready to assign onto a ResearchCompetitor. Raises
    CompetitorLookupError with a readable sentence when the channel cannot be
    reached — the student sees the reason, not a 404.
    """
    if not settings.YOUTUBE_API_KEY:
        raise CompetitorLookupError(
            "YOUTUBE_API_KEY is not configured on this deployment, so competitor figures "
            "cannot be fetched automatically. Enter them by hand instead."
        )

    channel_id = resolve_channel_id(url_or_id)

    try:
        stats = api.fetch_channel(settings.YOUTUBE_API_KEY, channel_id)
    except api.YoutubeApiError as exc:
        raise CompetitorLookupError(f"YouTube refused the request: {exc}") from exc

    if stats is None:
        raise CompetitorLookupError(
            "That channel could not be found. It may have been deleted, renamed or made private."
        )

    videos = []
    if stats.uploads_playlist_id:
        try:
            video_ids, _ = api.fetch_upload_ids(
                settings.YOUTUBE_API_KEY, stats.uploads_playlist_id, limit=GROWTH_SAMPLE_SIZE
            )
            if video_ids:
                videos, _ = api.fetch_videos(settings.YOUTUBE_API_KEY, video_ids)
        except api.YoutubeApiError as exc:
            # The channel-level figures are still worth keeping; only the growth
            # rule goes unchecked.
            logger.info("could not read uploads for %s: %s", channel_id, exc)

    dated = sorted(
        [v for v in videos if v.published_at is not None], key=lambda v: v.published_at
    )
    series = [v.views for v in dated if v.views is not None]

    return {
        "youtube_channel_id": stats.youtube_channel_id,
        "channel_name": stats.title or "",
        "subscriber_count": stats.subscriber_count,
        "view_count": stats.view_count,
        "video_count": stats.video_count,
        "oldest_video_at": dated[0].published_at if dated else None,
        "newest_video_at": dated[-1].published_at if dated else None,
        "video_views_series": series,
        "metrics_source": MetricSource.PUBLIC_API,
        "metrics_fetched_at": timezone.now(),
        # True when the sample hit its ceiling: the oldest video we saw may not
        # be the channel's oldest, so the age rule is reading a floor.
        "series_truncated": bool(stats.video_count and stats.video_count > len(videos) > 0),
    }


def refresh_competitor(competitor) -> dict:
    """Fetches and saves the figures for one stored competitor row."""
    metrics = fetch_metrics(competitor.youtube_channel_id or competitor.channel_url)
    truncated = metrics.pop("series_truncated", False)
    # The student's own label for the channel wins; only fill a blank one.
    if competitor.channel_name:
        metrics.pop("channel_name", None)

    for field, value in metrics.items():
        setattr(competitor, field, value)
    competitor.save()
    return {"truncated": truncated, "channel_id": competitor.youtube_channel_id}


# --- Evaluation -------------------------------------------------------------


def evaluate_competitor(competitor, *, thresholds: dict | None = None) -> ChannelVerdict:
    return evaluate_channel(
        channel_name=competitor.channel_name,
        video_count=competitor.video_count,
        subscriber_count=competitor.subscriber_count,
        view_count=competitor.view_count,
        oldest_video_date=competitor.oldest_video_date,
        views_oldest_first=competitor.video_views_series or [],
        today=timezone.localdate(),
        thresholds=thresholds if thresholds is not None else competitor_thresholds(),
    )


def evaluate_submission(submission) -> SubmissionVerdict:
    """
    Every competitor channel on a submission, against the thresholds currently
    in force. Recomputed on each view rather than stored, so retuning a
    threshold in Settings immediately changes what evaluators see instead of
    leaving stale verdicts behind.
    """
    thresholds = competitor_thresholds()
    return SubmissionVerdict(
        channels=[
            evaluate_competitor(competitor, thresholds=thresholds)
            for competitor in submission.competitors.all()
        ]
    )
