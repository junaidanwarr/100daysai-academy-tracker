"""
Operational configuration, read from the database.

Callers must never hardcode 15 days — the research window is a setting with a
per-batch override (specification section 7).
"""

from __future__ import annotations

from typing import Any

from apps.core.audit import write_audit
from apps.core.enums import AuditAction
from apps.core.middleware import current_request_meta
from apps.core.models import SystemSetting
from apps.core.permissions import assert_can

RESEARCH_DAYS = "research_days"
RESEARCH_WARNING_DAYS = "research_warning_days"
INACTIVITY_DAYS = "inactivity_days"
MAX_REJECTIONS_BEFORE_ALERT = "max_rejections_before_alert"
CHANNEL_CREATION_DAYS = "channel_creation_days"
FIRST_VIDEO_DAYS = "first_video_days"
NO_UPLOAD_DAYS = "no_upload_days"
MIN_VIDEOS_PER_WEEK = "min_videos_per_week"
YOUTUBE_SYNC_CADENCE = "youtube_sync_cadence"
ENROLLMENT_ID_PREFIX = "enrollment_id_prefix"
ACADEMY_NAME = "academy_name"

# Niche-validation thresholds applied to the competitor channels in a research
# submission. Tuned from the Settings screen, never hardcoded in the rules.
COMPETITOR_MIN_VIDEOS = "competitor_min_videos"
COMPETITOR_MAX_VIDEOS = "competitor_max_videos"
COMPETITOR_MIN_SUBS_VIEW_RATIO = "competitor_min_subs_view_ratio"
COMPETITOR_MIN_AGE_DAYS = "competitor_min_age_days"
COMPETITOR_MAX_AGE_DAYS = "competitor_max_age_days"
COMPETITOR_SPIKE_MULTIPLE = "competitor_spike_multiple"
COMPETITOR_MAX_TOP_VIDEO_SHARE = "competitor_max_top_video_share"
COMPETITOR_MIN_RECENT_SHARE = "competitor_min_recent_share"

# Fallbacks for a fresh database, or a key added by a later release. Never the
# source of truth once seeded.
DEFAULTS: dict[str, Any] = {
    RESEARCH_DAYS: 15,
    RESEARCH_WARNING_DAYS: 3,
    INACTIVITY_DAYS: 7,
    MAX_REJECTIONS_BEFORE_ALERT: 2,
    CHANNEL_CREATION_DAYS: 20,
    FIRST_VIDEO_DAYS: 3,
    NO_UPLOAD_DAYS: 10,
    MIN_VIDEOS_PER_WEEK: 3,
    YOUTUBE_SYNC_CADENCE: "DAILY",
    ENROLLMENT_ID_PREFIX: "100DAI",
    ACADEMY_NAME: "100DaysAI Academy",
    COMPETITOR_MIN_VIDEOS: 20,
    COMPETITOR_MAX_VIDEOS: 30,
    COMPETITOR_MIN_SUBS_VIEW_RATIO: "0.5",
    COMPETITOR_MIN_AGE_DAYS: 60,
    COMPETITOR_MAX_AGE_DAYS: 90,
    COMPETITOR_SPIKE_MULTIPLE: "5",
    COMPETITOR_MAX_TOP_VIDEO_SHARE: "50",
    COMPETITOR_MIN_RECENT_SHARE: "50",
}


def competitor_thresholds() -> dict:
    """
    The niche-validation thresholds in force, in the shape
    ``apps.research.competitor_rules`` expects. One place to change them, and
    the rules module stays free of database access.
    """

    def number(key, fallback):
        try:
            return float(get_setting(key))
        except (TypeError, ValueError):
            return fallback

    return {
        "min_videos": int(number(COMPETITOR_MIN_VIDEOS, 20)),
        "max_videos": int(number(COMPETITOR_MAX_VIDEOS, 30)),
        "min_subs_view_ratio_percent": number(COMPETITOR_MIN_SUBS_VIEW_RATIO, 0.5),
        "min_age_days": int(number(COMPETITOR_MIN_AGE_DAYS, 60)),
        "max_age_days": int(number(COMPETITOR_MAX_AGE_DAYS, 90)),
        "spike_multiple": number(COMPETITOR_SPIKE_MULTIPLE, 5.0),
        "max_top_video_share_percent": number(COMPETITOR_MAX_TOP_VIDEO_SHARE, 50.0),
        "min_recent_share_percent": number(COMPETITOR_MIN_RECENT_SHARE, 50.0),
    }


def get_setting(key: str) -> Any:
    row = SystemSetting.objects.filter(key=key).first()
    return row.value if row else DEFAULTS.get(key)


def get_int(key: str) -> int:
    try:
        return int(get_setting(key))
    except (TypeError, ValueError):
        return int(DEFAULTS.get(key, 0))


def effective_research_days(batch_research_days: int | None) -> int:
    """
    The window that actually applies: the batch override if set, otherwise the
    academy-wide default.
    """
    if batch_research_days and batch_research_days > 0:
        return batch_research_days
    return get_int(RESEARCH_DAYS)


def update_setting(actor, key: str, raw_value: str) -> SystemSetting:
    assert_can(actor.role, "setting", "configure")

    setting = SystemSetting.objects.get(key=key)
    previous = setting.value
    setting.value = _coerce(raw_value, setting.value_type)
    setting.updated_by = actor
    setting.save(update_fields=["value", "updated_by", "updated_at"])

    write_audit(
        actor=actor,
        action=AuditAction.SETTING_CHANGE,
        entity_type="SystemSetting",
        entity_id=setting.id,
        summary=f'Changed setting "{setting.label}"',
        before={"value": previous},
        after={"value": setting.value},
        **current_request_meta(),
    )
    return setting


def _coerce(raw: str, value_type: str) -> Any:
    if value_type == "int":
        number = int(raw)
        if number < 1:
            raise ValueError("Enter a whole number of 1 or more.")
        return number
    if value_type == "bool":
        return str(raw).lower() in {"1", "true", "yes", "on"}
    if value_type == "json":
        import json

        return json.loads(raw)
    return raw


def feature_flags() -> dict[str, bool]:
    """
    What is actually configured on this deployment. The UI reads this to say
    "not configured" rather than failing silently, or worse, implying data
    exists when it cannot.
    """
    from django.conf import settings as dj

    return {
        "youtube_public_api": bool(dj.YOUTUBE_API_KEY),
        "youtube_oauth": bool(dj.GOOGLE_CLIENT_ID and dj.GOOGLE_CLIENT_SECRET),
        "email": bool(dj.RESEND_API_KEY),
        "sms": bool(dj.TWILIO_ACCOUNT_SID and dj.TWILIO_AUTH_TOKEN),
        "whatsapp": bool(dj.WHATSAPP_PHONE_NUMBER_ID and dj.WHATSAPP_ACCESS_TOKEN),
        "lms": dj.LMS_ADAPTER != "MANUAL",
        "lms_webhook": bool(dj.LMS_WEBHOOK_SECRET),
    }
