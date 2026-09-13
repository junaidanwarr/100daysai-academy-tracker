"""
Google OAuth 2.0 for YouTube Analytics access (specification sections 9 and 21).

The only lawful route to a student's impressions, click-through rate, average
view duration, retention, traffic sources and revenue is their own explicit
consent. This module is what obtains it and what gives it back.

Three properties the rest of the system depends on:

* **Only the refresh token is stored, and only encrypted.** Access tokens are
  exchanged per sync and held in memory for the duration of that sync.
* **The state parameter is signed and expiring.** It carries the channel ID
  through the redirect; an unsigned one would let anyone bind their Google
  account to someone else's channel record.
* **Disconnecting is real.** The token is revoked at Google, not merely
  forgotten locally.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.utils import timezone

# Imported as a module, never `from ... import transport`: the transport is
# swapped at runtime by the tests, and a by-value import would keep the
# original bound here.
from apps.youtube import api
from apps.youtube.api import YoutubeApiError

logger = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"

READONLY_SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
    "openid",
    "email",
]

# Requested only when the academy has enabled revenue tracking; asking for the
# monetary scope by default puts an alarming line on every student's consent
# screen for data most channels do not have.
MONETARY_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"

# Bumped whenever the consent text below changes. Stored on the grant so it is
# always possible to say exactly what a student agreed to, and when.
CONSENT_VERSION = "2026-08-v1"

CONSENT_SUMMARY = [
    "Your channel's view, watch time and subscriber figures",
    "Impressions and click-through rate for your videos",
    "Average view duration, percentage viewed and audience retention",
    "Where your views come from (search, suggested, browse and so on)",
]

CONSENT_LIMITS = [
    "Read-only. Nothing can be uploaded, edited or deleted on your channel.",
    "Only this channel's analytics. No access to your email, Drive or other Google data.",
    "You can disconnect at any time, from this page, without asking anyone.",
    "Figures already collected are kept as a record of what was true at the time.",
]

STATE_SALT = "youtube.oauth.state"
STATE_MAX_AGE_SECONDS = 900  # 15 minutes: long enough to read the consent screen


class OauthNotConfigured(RuntimeError):
    pass


class OauthStateError(ValueError):
    pass


def is_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.GOOGLE_OAUTH_REDIRECT_URI)


def _require_configured() -> None:
    if not is_configured():
        raise OauthNotConfigured(
            "Google OAuth is not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and "
            "GOOGLE_OAUTH_REDIRECT_URI, and enable the YouTube Data and Analytics APIs on the project."
        )


def scopes_for(*, include_revenue: bool = False) -> list[str]:
    return [*READONLY_SCOPES, MONETARY_SCOPE] if include_revenue else list(READONLY_SCOPES)


def build_state(channel_id, user_id) -> str:
    """Signed, so the callback cannot be pointed at a channel the user never chose."""
    return signing.dumps({"channel": str(channel_id), "user": str(user_id)}, salt=STATE_SALT)


def read_state(state: str) -> dict:
    try:
        return signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE_SECONDS)
    except signing.SignatureExpired as exc:
        raise OauthStateError("The authorization link expired. Start the connection again.") from exc
    except signing.BadSignature as exc:
        raise OauthStateError("The authorization response could not be verified.") from exc


def consent_url(channel_id, user_id, *, include_revenue: bool = False) -> str:
    _require_configured()
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes_for(include_revenue=include_revenue)),
        # offline + consent is what returns a refresh token. Without both,
        # Google returns one only on the very first authorization ever, and a
        # student who reconnects gets no usable grant.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": build_state(channel_id, user_id),
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


@dataclass
class TokenResponse:
    access_token: str
    expires_at: object
    refresh_token: str | None = None
    scopes: list[str] | None = None


def _post_form(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    return api.transport(
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data=data,
    )


def _to_token(payload: dict) -> TokenResponse:
    expires_in = int(payload.get("expires_in", 3600) or 3600)
    return TokenResponse(
        access_token=payload["access_token"],
        expires_at=timezone.now() + timedelta(seconds=expires_in),
        refresh_token=payload.get("refresh_token"),
        scopes=(payload.get("scope") or "").split() or None,
    )


def exchange_code(code: str) -> TokenResponse:
    _require_configured()
    payload = _post_form(
        TOKEN_ENDPOINT,
        {
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    if "access_token" not in payload:
        raise YoutubeApiError("Google did not return an access token.", status=400, reason="invalid_grant")
    return _to_token(payload)


def refresh_access_token(refresh_token: str) -> TokenResponse:
    """
    Exchanges the stored refresh token for a short-lived access token.

    A revoked or expired grant surfaces here as ``invalid_grant``; the caller
    marks the grant revoked and raises an alert rather than retrying forever.
    """
    _require_configured()
    payload = _post_form(
        TOKEN_ENDPOINT,
        {
            "refresh_token": refresh_token,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
    )
    if "access_token" not in payload:
        raise YoutubeApiError(
            payload.get("error_description") or "Refresh failed.",
            status=400,
            reason=payload.get("error") or "invalid_grant",
        )
    return _to_token(payload)


def fetch_account_email(access_token: str) -> str | None:
    """Recorded on the grant so a student can see which Google account is linked."""
    try:
        payload = api.transport(
            USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
    except YoutubeApiError as exc:
        logger.info("could not read Google account email: %s", exc)
        return None
    return payload.get("email")


def revoke(token: str) -> bool:
    """
    Tells Google to invalidate the grant. Returns False if Google refused —
    which is not fatal: the local grant is still marked revoked, and a token
    that Google no longer honours is exactly what we wanted anyway.
    """
    try:
        _post_form(REVOKE_ENDPOINT, {"token": token})
        return True
    except YoutubeApiError as exc:
        logger.warning("Google revoke call failed: %s", exc)
        return False
    except Exception:  # noqa: BLE001 — disconnection must never fail on us
        logger.exception("unexpected error revoking Google token")
        return False


def owned_channel_ids(access_token: str) -> list[str]:
    """
    The channels this Google account actually owns, from ``channels.list?mine=true``.

    Used to verify at connection time that the student authorized the account
    that owns the channel on record, instead of discovering the mismatch later
    as a stream of empty analytics reports.
    """
    try:
        payload = api.transport(
            f"{api.DATA_API_ROOT}/channels?part=id&mine=true",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
    except YoutubeApiError as exc:
        logger.info("could not list owned channels: %s", exc)
        return []
    return [item.get("id") for item in (payload.get("items") or []) if item.get("id")]


def consent_context(*, include_revenue: bool = False) -> dict:
    """Everything the consent screen needs, so the text lives with the version it stamps."""
    return {
        "consent_version": CONSENT_VERSION,
        "will_read": CONSENT_SUMMARY + (["Estimated revenue"] if include_revenue else []),
        "limits": CONSENT_LIMITS,
        "scopes": scopes_for(include_revenue=include_revenue),
    }


def parse_scopes(raw: str | list | None) -> list[str]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else raw.split()
        except ValueError:
            return raw.split()
    return []
