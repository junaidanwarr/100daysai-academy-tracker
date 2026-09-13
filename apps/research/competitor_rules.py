"""
Niche-validation rules for the competitor channels in a research submission.

The subjective criteria (niche clarity, audience definition) ask an instructor
for a judgement. These four ask arithmetic, and arithmetic can be checked:

1. **Video count** — a proven-format channel has 20-30 uploads. Fewer is too
   early to call; many more suggests an established channel whose results a new
   student cannot expect to reproduce.
2. **Subscribers as a share of views** — at least 0.5% (1,000 views should have
   produced about 5 subscribers). Views that convert nobody are views the format
   did not earn.
3. **Channel age** — the oldest video is 2-3 months old. This is the whole point
   of the exercise: a channel that reached these numbers *recently* proves the
   niche works now, not that it worked in 2021.
4. **Consistent growth** — views climb or hold steady. One viral video carrying
   a channel is the strongest red flag in the set, because a student copying
   that format is copying a lottery ticket.

Every function here is pure: it takes numbers, returns a verdict and the reason
in plain English. No database, no API, no settings lookups — the thresholds are
passed in, so the academy can retune them without touching this file, and every
rule is unit-testable on its own.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

# Defaults, overridden by the SystemSetting values the caller passes in.
DEFAULTS = {
    "min_videos": 20,
    "max_videos": 30,
    "min_subs_view_ratio_percent": 0.5,
    "min_age_days": 60,
    "max_age_days": 90,
    # A single video pulling more than this multiple of the median is a spike,
    # not growth.
    "spike_multiple": 5.0,
    # ...and if that one video is more than this share of every view the channel
    # has, the channel is one hit rather than a working format.
    "max_top_video_share_percent": 50.0,
    # Newer uploads averaging below this share of the older ones is a decline.
    "min_recent_share_percent": 50.0,
}

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"


@dataclass
class RuleResult:
    """One rule, one verdict, and the sentence explaining it."""

    key: str
    label: str
    status: str
    detail: str
    # True for rule 4, which the academy treats as the strongest signal.
    is_red_flag: bool = False

    @property
    def passed(self) -> bool:
        return self.status == PASS

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    @property
    def unknown(self) -> bool:
        return self.status == UNKNOWN


@dataclass
class ChannelVerdict:
    channel_name: str
    results: list[RuleResult] = field(default_factory=list)

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.results if r.failed]

    @property
    def unknown(self) -> list[RuleResult]:
        return [r for r in self.results if r.unknown]

    @property
    def passes_all(self) -> bool:
        """Every rule checked, and every one passed. Unknown is not a pass."""
        return bool(self.results) and all(r.passed for r in self.results)

    @property
    def has_red_flag(self) -> bool:
        return any(r.failed and r.is_red_flag for r in self.results)

    @property
    def summary(self) -> str:
        if not self.results:
            return "No data recorded for this channel, so none of the four rules could be checked."
        if self.passes_all:
            return "Meets all four niche-validation rules."
        failed = len(self.failed)
        unknown = len(self.unknown)
        parts = []
        if failed:
            parts.append(f"{failed} rule(s) failed")
        if unknown:
            parts.append(f"{unknown} could not be checked")
        return " and ".join(parts) + "."


def _threshold(overrides: dict | None, key: str):
    if overrides and overrides.get(key) is not None:
        return overrides[key]
    return DEFAULTS[key]


# --- Rule 1: video count ----------------------------------------------------


def check_video_count(video_count: int | None, thresholds: dict | None = None) -> RuleResult:
    low = _threshold(thresholds, "min_videos")
    high = _threshold(thresholds, "max_videos")
    label = f"Video count is {low}-{high}"

    if video_count is None:
        return RuleResult("video_count", label, UNKNOWN, "Video count is not recorded for this channel.")
    if video_count < low:
        return RuleResult(
            "video_count", label, FAIL,
            f"{video_count} videos — below {low}. Too early to judge whether the format works.",
        )
    if video_count > high:
        return RuleResult(
            "video_count", label, FAIL,
            f"{video_count} videos — above {high}. An established channel, not a fresh proof of the niche.",
        )
    return RuleResult("video_count", label, PASS, f"{video_count} videos, within {low}-{high}.")


# --- Rule 2: subscribers as a share of views --------------------------------


def check_subscriber_ratio(
    subscriber_count: int | None, view_count: int | None, thresholds: dict | None = None
) -> RuleResult:
    minimum = float(_threshold(thresholds, "min_subs_view_ratio_percent"))
    label = f"Subscribers are at least {minimum}% of views"

    if subscriber_count is None or view_count is None:
        return RuleResult(
            "subscriber_ratio", label, UNKNOWN,
            "Subscriber or view count is not recorded, so the ratio cannot be worked out.",
        )
    if view_count <= 0:
        return RuleResult("subscriber_ratio", label, UNKNOWN, "The channel reports no views to measure against.")

    ratio = (subscriber_count / view_count) * 100
    expected = int(round(view_count * minimum / 100))
    shown = f"{ratio:.3f}%".rstrip("0").rstrip(".")

    if ratio < minimum:
        return RuleResult(
            "subscriber_ratio", label, FAIL,
            f"{subscriber_count:,} subscribers on {view_count:,} views is {shown} — "
            f"below {minimum}%. Around {expected:,} would be expected.",
        )
    return RuleResult(
        "subscriber_ratio", label, PASS,
        f"{subscriber_count:,} subscribers on {view_count:,} views is {shown}, at or above {minimum}%.",
    )


# --- Rule 3: channel age ----------------------------------------------------


def check_channel_age(
    oldest_video_date: date | None, today: date | None = None, thresholds: dict | None = None
) -> RuleResult:
    low = int(_threshold(thresholds, "min_age_days"))
    high = int(_threshold(thresholds, "max_age_days"))
    label = f"Oldest video is {low}-{high} days old"

    if oldest_video_date is None:
        return RuleResult("channel_age", label, UNKNOWN, "The date of the oldest video is not recorded.")

    today = today or date.today()
    age = (today - oldest_video_date).days

    if age < 0:
        return RuleResult(
            "channel_age", label, UNKNOWN,
            "The oldest video is dated in the future, so the age cannot be trusted.",
        )
    if age < low:
        return RuleResult(
            "channel_age", label, FAIL,
            f"Oldest video is {age} days old — younger than {low} days. Not yet enough history to judge.",
        )
    if age > high:
        return RuleResult(
            "channel_age", label, FAIL,
            f"Oldest video is {age} days old — older than {high} days. This does not prove the niche works now.",
        )
    return RuleResult("channel_age", label, PASS, f"Oldest video is {age} days old, within {low}-{high}.")


# --- Rule 4: consistent growth (the red flag) -------------------------------


def check_growth_consistency(views_oldest_first: list[int] | None, thresholds: dict | None = None) -> RuleResult:
    """
    Three separate ways a channel fails this, reported by name so an instructor
    knows which one it was:

    * **spike** — one video pulled a multiple of the typical video's views
    * **one-hit** — a single video accounts for most of the channel's views
    * **decline** — the newer half performs materially worse than the older half

    ``5k, 8k, 10k`` passes. ``10k, 100k, 5k`` fails on the first two.
    """
    spike_multiple = float(_threshold(thresholds, "spike_multiple"))
    top_share_limit = float(_threshold(thresholds, "max_top_video_share_percent"))
    recent_floor = float(_threshold(thresholds, "min_recent_share_percent"))
    label = "Views grow consistently, with no single spike carrying the channel"

    views = [int(v) for v in (views_oldest_first or []) if v is not None and int(v) >= 0]
    if len(views) < 3:
        return RuleResult(
            "growth_consistency", label, UNKNOWN,
            "Fewer than three videos with view figures recorded, so a trend cannot be read.",
            is_red_flag=True,
        )

    total = sum(views)
    if total <= 0:
        return RuleResult(
            "growth_consistency", label, UNKNOWN,
            "Every recorded video reports zero views.",
            is_red_flag=True,
        )

    peak = max(views)
    median = statistics.median(views)
    top_share = (peak / total) * 100

    problems = []
    if median > 0 and peak >= median * spike_multiple:
        problems.append(
            f"one video pulled {peak:,} views against a typical {int(median):,} "
            f"({peak / median:.1f}x) — a spike, not growth"
        )
    if top_share > top_share_limit:
        problems.append(
            f"a single video is {top_share:.0f}% of all views — the channel is one hit, not a working format"
        )

    half = len(views) // 2
    older, newer = views[:half], views[len(views) - half :]
    if older and newer:
        older_avg = sum(older) / len(older)
        newer_avg = sum(newer) / len(newer)
        if older_avg > 0 and (newer_avg / older_avg) * 100 < recent_floor:
            problems.append(
                f"recent uploads average {int(newer_avg):,} views against {int(older_avg):,} earlier "
                f"— performance is falling away"
            )

    if problems:
        return RuleResult("growth_consistency", label, FAIL, "; ".join(problems).capitalize() + ".", is_red_flag=True)

    # A 0.5% difference is not a trend. Anything inside ±10% reads as steady,
    # because calling it "rising" would overstate what the numbers show.
    first, last = views[0], views[-1]
    if last > first * 1.1:
        direction = "rising"
    elif last < first * 0.9:
        direction = "easing, but within tolerance"
    else:
        direction = "holding steady"
    return RuleResult(
        "growth_consistency", label, PASS,
        f"Views are {direction} across {len(views)} videos "
        f"({first:,} → {last:,}), with no single video dominating.",
        is_red_flag=True,
    )


# --- All four together ------------------------------------------------------


def evaluate_channel(
    *,
    channel_name: str,
    video_count: int | None = None,
    subscriber_count: int | None = None,
    view_count: int | None = None,
    oldest_video_date: date | None = None,
    views_oldest_first: list[int] | None = None,
    today: date | None = None,
    thresholds: dict | None = None,
) -> ChannelVerdict:
    return ChannelVerdict(
        channel_name=channel_name,
        results=[
            check_video_count(video_count, thresholds),
            check_subscriber_ratio(subscriber_count, view_count, thresholds),
            check_channel_age(oldest_video_date, today, thresholds),
            check_growth_consistency(views_oldest_first, thresholds),
        ],
    )


@dataclass
class SubmissionVerdict:
    """Every competitor channel in one submission, plus the headline count."""

    channels: list[ChannelVerdict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.channels)

    @property
    def passing(self) -> int:
        return sum(1 for c in self.channels if c.passes_all)

    @property
    def red_flagged(self) -> list[ChannelVerdict]:
        return [c for c in self.channels if c.has_red_flag]

    @property
    def all_pass(self) -> bool:
        return bool(self.channels) and all(c.passes_all for c in self.channels)

    @property
    def headline(self) -> str:
        if not self.channels:
            return "No competitor channels recorded, so the niche-validation rules could not be applied."
        line = f"{self.passing} of {self.total} competitor channel(s) meet all four rules."
        if self.red_flagged:
            line += f" {len(self.red_flagged)} show inconsistent growth, which is the strongest warning sign."
        return line
