"""
Parsing and consent-state tests — pure logic, no database required.

The synchronisation engine is covered separately in ``tests_sync.py``, which
needs a database because the whole point of it is what gets written.
"""

from datetime import datetime, timezone as dt_timezone

from django.test import SimpleTestCase, override_settings

from apps.youtube import api, oauth


class DurationParsingTests(SimpleTestCase):
    def test_parses_hours_minutes_and_seconds(self):
        self.assertEqual(api.parse_duration("PT1H2M3S"), 3723)

    def test_parses_a_bare_seconds_duration(self):
        self.assertEqual(api.parse_duration("PT45S"), 45)

    def test_parses_a_multi_day_duration(self):
        self.assertEqual(api.parse_duration("P1DT2H"), 93600)

    def test_returns_none_for_a_live_stream_placeholder(self):
        # YouTube reports P0D for an in-progress live broadcast. Zero seconds
        # would classify it as a Short, which it is not.
        self.assertIsNone(api.parse_duration("P0D"))

    def test_returns_none_for_missing_or_unparseable_input(self):
        self.assertIsNone(api.parse_duration(None))
        self.assertIsNone(api.parse_duration(""))
        self.assertIsNone(api.parse_duration("about a minute"))


class TimestampParsingTests(SimpleTestCase):
    def test_parses_a_zulu_timestamp_as_utc(self):
        parsed = api.parse_timestamp("2026-03-04T09:30:00Z")
        self.assertEqual(parsed, datetime(2026, 3, 4, 9, 30, tzinfo=dt_timezone.utc))

    def test_returns_none_rather_than_raising_on_rubbish(self):
        self.assertIsNone(api.parse_timestamp("not a date"))
        self.assertIsNone(api.parse_timestamp(None))


class HiddenCountTests(SimpleTestCase):
    """The distinction the whole module exists to protect."""

    def test_absent_becomes_none_not_zero(self):
        self.assertIsNone(api._int_or_none(None))
        self.assertIsNone(api._int_or_none(""))

    def test_a_measured_zero_stays_zero(self):
        self.assertEqual(api._int_or_none("0"), 0)

    def test_a_hidden_subscriber_count_is_stored_as_none(self):
        stats = self._channel({"hiddenSubscriberCount": True, "subscriberCount": "0", "viewCount": "500"})
        self.assertIsNone(stats.subscriber_count)
        self.assertTrue(stats.subscriber_count_hidden)
        self.assertEqual(stats.view_count, 500)

    def test_a_visible_subscriber_count_is_read(self):
        stats = self._channel({"hiddenSubscriberCount": False, "subscriberCount": "1240"})
        self.assertEqual(stats.subscriber_count, 1240)
        self.assertFalse(stats.subscriber_count_hidden)

    def _channel(self, statistics: dict) -> api.ChannelStats:
        payload = {
            "items": [
                {
                    "id": "UC123",
                    "snippet": {"title": "Test", "publishedAt": "2025-01-01T00:00:00Z"},
                    "statistics": statistics,
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
                }
            ]
        }
        original = api.transport
        api.transport = lambda url, **kwargs: payload
        try:
            return api.fetch_channel("key", "UC123")
        finally:
            api.transport = original


class ErrorClassificationTests(SimpleTestCase):
    def test_quota_exceeded_is_recognised(self):
        error = api.YoutubeApiError("over quota", status=403, reason="quotaExceeded")
        self.assertTrue(error.is_quota_exceeded)
        self.assertFalse(error.is_auth_failure)

    def test_a_revoked_grant_is_an_auth_failure(self):
        error = api.YoutubeApiError("bad grant", status=401, reason="authError")
        self.assertTrue(error.is_auth_failure)
        self.assertFalse(error.is_quota_exceeded)

    def test_a_server_error_is_retryable_but_a_client_error_is_not(self):
        self.assertTrue(api.YoutubeApiError("boom", status=503).is_retryable)
        self.assertFalse(api.YoutubeApiError("nope", status=404).is_retryable)


@override_settings(
    GOOGLE_CLIENT_ID="client-id",
    GOOGLE_CLIENT_SECRET="secret",
    GOOGLE_OAUTH_REDIRECT_URI="https://example.test/api/oauth/youtube/callback/",
)
class ConsentUrlTests(SimpleTestCase):
    def test_reports_configured_only_when_all_three_values_are_present(self):
        self.assertTrue(oauth.is_configured())
        with override_settings(GOOGLE_CLIENT_SECRET=""):
            self.assertFalse(oauth.is_configured())

    def test_requests_offline_access_so_a_refresh_token_is_returned(self):
        url = oauth.consent_url("channel-1", "user-1")
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)

    def test_omits_the_monetary_scope_unless_revenue_is_tracked(self):
        self.assertNotIn(oauth.MONETARY_SCOPE, oauth.scopes_for())
        self.assertIn(oauth.MONETARY_SCOPE, oauth.scopes_for(include_revenue=True))

    def test_raises_rather_than_building_a_broken_url_when_unconfigured(self):
        with override_settings(GOOGLE_CLIENT_ID=""):
            with self.assertRaises(oauth.OauthNotConfigured):
                oauth.consent_url("channel-1", "user-1")


class ConsentStateTests(SimpleTestCase):
    def test_round_trips_the_channel_and_user(self):
        state = oauth.build_state("channel-1", "user-9")
        self.assertEqual(oauth.read_state(state), {"channel": "channel-1", "user": "user-9"})

    def test_rejects_a_tampered_state(self):
        # Without this, anyone could bind their own Google account to another
        # student's channel record by editing the callback URL.
        state = oauth.build_state("channel-1", "user-9")
        with self.assertRaises(oauth.OauthStateError):
            oauth.read_state(state[:-4] + "aaaa")

    def test_rejects_an_empty_state(self):
        with self.assertRaises(oauth.OauthStateError):
            oauth.read_state("")


class ConsentTextTests(SimpleTestCase):
    def test_the_consent_screen_lists_revenue_only_when_it_is_requested(self):
        plain = oauth.consent_context()
        monetised = oauth.consent_context(include_revenue=True)
        self.assertNotIn("Estimated revenue", plain["will_read"])
        self.assertIn("Estimated revenue", monetised["will_read"])

    def test_the_consent_version_is_stamped_on_every_context(self):
        self.assertEqual(oauth.consent_context()["consent_version"], oauth.CONSENT_VERSION)

    def test_the_limits_state_that_access_is_read_only_and_revocable(self):
        limits = " ".join(oauth.consent_context()["limits"]).lower()
        self.assertIn("read-only", limits)
        self.assertIn("disconnect", limits)
