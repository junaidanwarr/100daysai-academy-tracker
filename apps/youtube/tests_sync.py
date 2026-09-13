"""
Synchronisation engine tests.

Every YouTube call is served by ``FakeTransport``, so the full path — quota
accounting, snapshot writing, token refresh, revocation handling — is exercised
without credentials or network access.

These need a database because what they assert is what gets written:

    python manage.py test apps --settings=config.settings_test
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.academy.models import Batch, Student
from apps.accounts.models import User
from apps.core.crypto import encrypt
from apps.core.enums import MetricSource, SyncStatus, UserRole, VideoType
from apps.core.models import AuditLog
from apps.youtube import api, oauth, services
from apps.youtube.models import (
    ChannelOauthGrant,
    ChannelSnapshot,
    SyncJobRun,
    Video,
    VideoSnapshot,
    YoutubeChannel,
)

OAUTH_SETTINGS = {
    "GOOGLE_CLIENT_ID": "client-id",
    "GOOGLE_CLIENT_SECRET": "secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/api/oauth/youtube/callback/",
    "YOUTUBE_API_KEY": "test-api-key",
}


class FakeTransport:
    """
    Stands in for every HTTP call. Records the URLs it was asked for, so a test
    can assert that a call was *not* made — which is how quota ceilings and
    skipped optional metrics are verified.
    """

    def __init__(self, **overrides):
        self.calls: list[str] = []
        self.errors: dict[str, api.YoutubeApiError] = {}
        self.responses = {
            "channels": {
                "items": [
                    {
                        "id": "UC_TEST",
                        "snippet": {"title": "Test Channel", "publishedAt": "2025-06-01T00:00:00Z"},
                        "statistics": {"subscriberCount": "1500", "viewCount": "90000", "videoCount": "3"},
                        "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST"}},
                    }
                ]
            },
            "playlistItems": {
                "items": [
                    {"contentDetails": {"videoId": "vid1"}},
                    {"contentDetails": {"videoId": "vid2"}},
                ]
            },
            "videos": {
                "items": [
                    {
                        "id": "vid1",
                        "snippet": {
                            "title": "Long video",
                            "description": "d1",
                            "publishedAt": "2026-07-20T10:00:00Z",
                            "thumbnails": {"high": {"url": "https://img.test/1.jpg"}},
                        },
                        "contentDetails": {"duration": "PT10M30S"},
                        "statistics": {"viewCount": "1200", "likeCount": "80", "commentCount": "0"},
                    },
                    {
                        "id": "vid2",
                        "snippet": {
                            "title": "A Short",
                            "description": "d2",
                            "publishedAt": "2026-07-25T10:00:00Z",
                            "thumbnails": {},
                        },
                        "contentDetails": {"duration": "PT58S"},
                        "statistics": {"viewCount": "40000", "likeCount": "3000"},
                    },
                ]
            },
            "token": {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": " ".join(oauth.READONLY_SCOPES)},
            "userinfo": {"email": "student@example.test"},
            "mine": {"items": [{"id": "UC_TEST"}]},
            "channel_report": {
                "columnHeaders": [
                    {"name": "estimatedMinutesWatched"},
                    {"name": "averageViewDuration"},
                    {"name": "subscribersGained"},
                    {"name": "subscribersLost"},
                ],
                "rows": [[4200, 145.5, 60, 4]],
            },
            "video_report": {
                "columnHeaders": [
                    {"name": "video"},
                    {"name": "estimatedMinutesWatched"},
                    {"name": "averageViewDuration"},
                    {"name": "averageViewPercentage"},
                    {"name": "shares"},
                    {"name": "subscribersGained"},
                    {"name": "subscribersLost"},
                ],
                "rows": [["vid1", 3000, 210.0, 41.5, 12, 30, 1]],
            },
            "impressions_report": {
                "columnHeaders": [
                    {"name": "video"},
                    {"name": "impressions"},
                    {"name": "impressionsClickThroughRate"},
                ],
                "rows": [["vid1", 50000, 0.0842]],
            },
            "traffic_report": {
                "columnHeaders": [
                    {"name": "video"},
                    {"name": "insightTrafficSourceType"},
                    {"name": "views"},
                    {"name": "estimatedMinutesWatched"},
                ],
                "rows": [["vid1", "YT_SEARCH", 750, 1800], ["vid1", "SUGGESTED_VIDEO", 250, 600]],
            },
            "revoke": {},
        }
        self.responses.update(overrides)

    def fail(self, marker: str, error: api.YoutubeApiError) -> None:
        self.errors[marker] = error

    def __call__(self, url: str, *, headers=None, data=None) -> dict:
        self.calls.append(url)
        for marker, error in self.errors.items():
            if marker in url:
                raise error
        return self.responses[self._key(url, data)]

    def _key(self, url: str, data: bytes | None) -> str:
        if "oauth2.googleapis.com/revoke" in url:
            return "revoke"
        if "oauth2.googleapis.com/token" in url:
            return "token"
        if "userinfo" in url:
            return "userinfo"
        if "mine=true" in url:
            return "mine"
        if "youtubeanalytics" in url:
            if "impressions" in url:
                return "impressions_report"
            if "insightTrafficSourceType" in url:
                return "traffic_report"
            if "dimensions=video" in url:
                return "video_report"
            return "channel_report"
        if "/channels?" in url:
            return "channels"
        if "/playlistItems?" in url:
            return "playlistItems"
        if "/videos?" in url:
            return "videos"
        raise AssertionError(f"unexpected call: {url}")

    def called(self, fragment: str) -> bool:
        return any(fragment in call for call in self.calls)


class SyncTestCase(TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self._original = api.transport
        api.transport = self.transport

        self.admin = User.objects.create_user(
            email="admin@example.test", password="x", role=UserRole.SUPER_ADMIN, full_name="Admin"
        )
        batch = Batch.objects.create(code="B-1", name="Batch 1", start_date=date(2026, 1, 1))
        self.student = Student.objects.create(
            enrollment_id="100DAI-2026-0001",
            full_name="Test Student",
            email="student@example.test",
            enrollment_date=date(2026, 1, 5),
            batch=batch,
        )
        self.channel = YoutubeChannel.objects.create(
            student=self.student, channel_name="Test Channel", youtube_channel_id="UC_TEST"
        )

    def tearDown(self):
        api.transport = self._original

    def grant(self, **over) -> ChannelOauthGrant:
        defaults = {
            "encrypted_refresh_token": encrypt("rt-1"),
            "scopes": list(oauth.READONLY_SCOPES),
            "consent_version": oauth.CONSENT_VERSION,
        }
        return ChannelOauthGrant.objects.create(channel=self.channel, **{**defaults, **over})


@override_settings(**OAUTH_SETTINGS)
class PublicSyncTests(SyncTestCase):
    def test_writes_a_public_snapshot_and_videos(self):
        result = services.sync_channel(self.channel)

        self.assertEqual(result.status, SyncStatus.SUCCESS)
        self.assertEqual(result.videos_seen, 2)
        self.assertFalse(result.analytics)

        snapshot = ChannelSnapshot.objects.get(source=MetricSource.PUBLIC_API)
        self.assertEqual(snapshot.subscriber_count, 1500)
        self.assertEqual(snapshot.view_count, 90000)
        self.assertEqual(Video.objects.count(), 2)

    def test_classifies_a_short_by_duration(self):
        services.sync_channel(self.channel)
        self.assertEqual(Video.objects.get(youtube_video_id="vid2").video_type, VideoType.SHORT)
        self.assertEqual(Video.objects.get(youtube_video_id="vid1").video_type, VideoType.LONG_FORM)

    def test_leaves_analytics_columns_null_without_a_grant(self):
        services.sync_channel(self.channel)
        snapshot = ChannelSnapshot.objects.get()
        self.assertIsNone(snapshot.watch_time_minutes)
        self.assertIsNone(snapshot.average_view_duration)
        self.assertIsNone(snapshot.estimated_revenue)

    def test_distinguishes_a_measured_zero_from_a_missing_figure(self):
        # vid1 reports zero comments; vid2 reports none at all. They must not
        # end up looking the same.
        services.sync_channel(self.channel)
        vid1 = VideoSnapshot.objects.get(video__youtube_video_id="vid1")
        vid2 = VideoSnapshot.objects.get(video__youtube_video_id="vid2")
        self.assertEqual(vid1.comments, 0)
        self.assertIsNone(vid2.comments)

    def test_does_not_duplicate_videos_across_runs(self):
        services.sync_channel(self.channel)
        services.sync_channel(self.channel)
        self.assertEqual(Video.objects.count(), 2)
        # ...but each run leaves its own snapshot, because snapshots are history.
        self.assertEqual(VideoSnapshot.objects.filter(video__youtube_video_id="vid1").count(), 2)

    def test_records_last_synced_and_last_upload_on_the_channel(self):
        services.sync_channel(self.channel)
        self.channel.refresh_from_db()
        self.assertIsNotNone(self.channel.last_synced_at)
        self.assertEqual(self.channel.last_upload_at.date(), date(2026, 7, 25))
        self.assertEqual(self.channel.channel_creation_date, date(2025, 6, 1))

    def test_closes_the_run_with_the_quota_it_spent(self):
        result = services.sync_channel(self.channel)
        run = SyncJobRun.objects.get()
        self.assertEqual(run.status, SyncStatus.SUCCESS)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.quota_units_used, result.quota_used)
        self.assertEqual(run.quota_units_used, 3)  # channels + playlistItems + videos

    def test_refuses_to_sync_a_channel_with_no_youtube_id(self):
        self.channel.youtube_channel_id = None
        self.channel.save(update_fields=["youtube_channel_id"])
        with self.assertRaises(services.SyncNotConfigured):
            services.sync_channel(self.channel)

    @override_settings(YOUTUBE_API_KEY="")
    def test_refuses_to_sync_without_an_api_key_rather_than_reporting_success(self):
        with self.assertRaises(services.SyncNotConfigured):
            services.sync_channel(self.channel)


@override_settings(**OAUTH_SETTINGS)
class SyncFailureTests(SyncTestCase):
    def test_a_deleted_channel_fails_the_run_with_a_readable_reason(self):
        self.transport.responses["channels"] = {"items": []}
        result = services.sync_channel(self.channel)

        self.assertEqual(result.status, SyncStatus.FAILED)
        run = SyncJobRun.objects.get()
        self.assertIn("could not be found", run.error_message)
        self.assertEqual(ChannelSnapshot.objects.count(), 0)

    def test_quota_exhaustion_marks_the_run_partial_not_failed(self):
        self.transport.fail("/channels?", api.YoutubeApiError("quota", status=403, reason="quotaExceeded"))
        result = services.sync_channel(self.channel)

        self.assertEqual(result.status, SyncStatus.PARTIAL)
        self.assertEqual(SyncJobRun.objects.get().status, SyncStatus.PARTIAL)

    def test_an_unexpected_error_still_closes_the_run(self):
        self.transport.responses["playlistItems"] = None  # provokes a TypeError downstream
        result = services.sync_channel(self.channel)

        self.assertEqual(result.status, SyncStatus.FAILED)
        run = SyncJobRun.objects.get()
        self.assertIsNotNone(run.finished_at)
        self.assertTrue(run.error_message)

    def test_skips_uploads_and_reports_partial_when_the_budget_runs_out(self):
        result = services.sync_channel(self.channel, quota_budget=1)

        self.assertEqual(result.status, SyncStatus.PARTIAL)
        self.assertFalse(self.transport.called("/playlistItems?"))
        self.assertIn("uploads_skipped", SyncJobRun.objects.get().details)
        # The channel figures that were affordable are still written.
        self.assertEqual(ChannelSnapshot.objects.count(), 1)


@override_settings(**OAUTH_SETTINGS)
class AnalyticsSyncTests(SyncTestCase):
    def test_writes_private_metrics_as_analytics_rows(self):
        self.grant()
        result = services.sync_channel(self.channel)

        self.assertTrue(result.analytics)
        channel_row = ChannelSnapshot.objects.get(source=MetricSource.ANALYTICS_API)
        self.assertEqual(channel_row.watch_time_minutes, 4200)
        self.assertEqual(channel_row.average_view_duration, Decimal("145.5"))

        video_row = VideoSnapshot.objects.get(
            source=MetricSource.ANALYTICS_API, video__youtube_video_id="vid1"
        )
        self.assertEqual(video_row.impressions, 50000)
        # YouTube reports a ratio; the column stores a percentage.
        self.assertEqual(video_row.impressions_ctr_percent, Decimal("8.420"))
        self.assertEqual(video_row.average_percentage_viewed, Decimal("41.5"))

    def test_public_and_analytics_rows_stay_separate(self):
        self.grant()
        services.sync_channel(self.channel)

        public = ChannelSnapshot.objects.get(source=MetricSource.PUBLIC_API)
        private = ChannelSnapshot.objects.get(source=MetricSource.ANALYTICS_API)
        # An analytics row must never overwrite or dilute the public figures.
        self.assertEqual(public.subscriber_count, 1500)
        self.assertIsNone(private.subscriber_count)
        self.assertIsNone(public.watch_time_minutes)

    def test_records_traffic_sources_with_shares_summing_to_one_hundred(self):
        self.grant()
        services.sync_channel(self.channel)

        snapshot = VideoSnapshot.objects.get(source=MetricSource.ANALYTICS_API, video__youtube_video_id="vid1")
        sources = {s.source_type: s for s in snapshot.traffic_sources.all()}
        self.assertEqual(sources["YT_SEARCH"].views, 750)
        self.assertEqual(sources["YT_SEARCH"].share_percent, Decimal("75.000"))
        self.assertEqual(sources["SUGGESTED_VIDEO"].share_percent, Decimal("25.000"))

    def test_a_video_with_no_analytics_row_gets_nulls_not_zeros(self):
        # The fake report covers vid1 only; vid2 has no private figures at all.
        self.grant()
        services.sync_channel(self.channel)

        vid2 = VideoSnapshot.objects.get(source=MetricSource.ANALYTICS_API, video__youtube_video_id="vid2")
        self.assertIsNone(vid2.impressions)
        self.assertIsNone(vid2.watch_time_minutes)

    def test_omits_revenue_unless_the_monetary_scope_was_granted(self):
        self.grant()
        services.sync_channel(self.channel)
        self.assertFalse(self.transport.called("estimatedRevenue"))
        self.assertIsNone(ChannelSnapshot.objects.get(source=MetricSource.ANALYTICS_API).estimated_revenue)

    def test_missing_impressions_do_not_abort_the_rest_of_the_sync(self):
        # Some channels have no impression data. That is an absence, not a failure.
        self.grant()
        self.transport.fail("impressions", api.YoutubeApiError("no data", status=400, reason="badRequest"))
        result = services.sync_channel(self.channel)

        self.assertTrue(result.analytics)
        row = VideoSnapshot.objects.get(source=MetricSource.ANALYTICS_API, video__youtube_video_id="vid1")
        self.assertIsNone(row.impressions)
        self.assertEqual(row.watch_time_minutes, 3000)

    def test_a_revoked_token_marks_the_grant_revoked_and_reports_partial(self):
        grant = self.grant()
        self.transport.fail(
            "oauth2.googleapis.com/token",
            api.YoutubeApiError("invalid_grant", status=401, reason="invalid_grant"),
        )
        result = services.sync_channel(self.channel)

        self.assertEqual(result.status, SyncStatus.PARTIAL)
        grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)
        self.assertEqual(grant.encrypted_refresh_token, "")
        # Public figures were still collected — losing analytics is not losing everything.
        self.assertEqual(ChannelSnapshot.objects.filter(source=MetricSource.PUBLIC_API).count(), 1)

    def test_a_revoked_grant_is_not_used_again(self):
        self.grant(revoked_at=timezone.now(), encrypted_refresh_token="")
        result = services.sync_channel(self.channel)

        self.assertFalse(result.analytics)
        self.assertFalse(self.transport.called("youtubeanalytics"))


@override_settings(**OAUTH_SETTINGS)
class ConsentLifecycleTests(SyncTestCase):
    def test_stores_the_refresh_token_encrypted_and_records_consent(self):
        grant = services.complete_connection(self.admin, self.channel, "auth-code", ip_address="10.0.0.1")

        self.assertNotIn("rt-1", grant.encrypted_refresh_token)
        self.assertEqual(grant.google_account_email, "student@example.test")
        self.assertEqual(grant.consent_version, oauth.CONSENT_VERSION)
        self.assertEqual(grant.consent_ip_address, "10.0.0.1")

    def test_refuses_a_google_account_that_does_not_own_the_channel(self):
        self.transport.responses["mine"] = {"items": [{"id": "UC_SOMEONE_ELSE"}]}
        with self.assertRaises(services.SyncNotConfigured):
            services.complete_connection(self.admin, self.channel, "auth-code")
        self.assertFalse(ChannelOauthGrant.objects.exists())

    def test_refuses_when_google_returns_no_refresh_token(self):
        self.transport.responses["token"] = {"access_token": "at-1", "expires_in": 3600}
        with self.assertRaises(services.SyncNotConfigured):
            services.complete_connection(self.admin, self.channel, "auth-code")

    def test_the_refresh_token_never_reaches_the_audit_log(self):
        services.complete_connection(self.admin, self.channel, "auth-code")
        entries = json.dumps([
            {"summary": e.summary, "before": e.before, "after": e.after}
            for e in AuditLog.objects.all()
        ])
        self.assertNotIn("rt-1", entries)

    def test_disconnecting_keeps_the_snapshots_already_collected(self):
        self.grant()
        services.sync_channel(self.channel)
        before = ChannelSnapshot.objects.filter(source=MetricSource.ANALYTICS_API).count()

        self.assertTrue(services.disconnect(self.admin, self.channel))

        self.assertEqual(ChannelSnapshot.objects.filter(source=MetricSource.ANALYTICS_API).count(), before)
        grant = ChannelOauthGrant.objects.get()
        self.assertIsNotNone(grant.revoked_at)
        self.assertEqual(grant.encrypted_refresh_token, "")

    def test_disconnecting_twice_is_a_no_op(self):
        self.grant()
        self.assertTrue(services.disconnect(self.admin, self.channel))
        self.channel.refresh_from_db()
        self.assertFalse(services.disconnect(self.admin, self.channel))

    def test_disconnect_succeeds_even_if_google_refuses_the_revocation(self):
        self.grant()
        self.transport.fail("revoke", api.YoutubeApiError("nope", status=400))
        self.assertTrue(services.disconnect(self.admin, self.channel))
        self.assertIsNotNone(ChannelOauthGrant.objects.get().revoked_at)

    def test_reconnecting_clears_a_previous_revocation(self):
        self.grant(revoked_at=timezone.now(), revoked_reason="student disconnected")
        grant = services.complete_connection(self.admin, self.channel, "auth-code")
        self.assertIsNone(grant.revoked_at)
        self.assertIsNone(grant.revoked_reason)
        self.assertEqual(ChannelOauthGrant.objects.count(), 1)


@override_settings(**OAUTH_SETTINGS)
class QuotaTests(SyncTestCase):
    def test_counts_only_todays_runs(self):
        SyncJobRun.objects.create(job_type="channel-sync", quota_units_used=40)
        old = SyncJobRun.objects.create(job_type="channel-sync", quota_units_used=9000)
        SyncJobRun.objects.filter(pk=old.pk).update(started_at=timezone.now() - timedelta(days=2))

        self.assertEqual(services.quota_used_today(), 40)

    @override_settings(YOUTUBE_DAILY_QUOTA_UNITS=100)
    def test_remaining_never_goes_negative(self):
        SyncJobRun.objects.create(job_type="channel-sync", quota_units_used=250)
        self.assertEqual(services.quota_remaining_today(), 0)

    @override_settings(YOUTUBE_DAILY_QUOTA_UNITS=0)
    def test_the_sweep_defers_channels_instead_of_hammering_the_api(self):
        result = services.sync_all(force=True)

        self.assertTrue(result.quota_ceiling_reached)
        self.assertEqual(result.synced, 0)
        self.assertEqual(result.skipped, 1)
        self.assertFalse(self.transport.calls)

    def test_a_manual_sync_is_refused_once_the_ceiling_is_reached(self):
        SyncJobRun.objects.create(job_type="channel-sync", quota_units_used=999999)
        with self.assertRaises(services.SyncNotConfigured):
            services.sync_now(self.admin, self.channel)


@override_settings(**OAUTH_SETTINGS)
class SweepTests(SyncTestCase):
    def test_skips_channels_without_a_youtube_id(self):
        YoutubeChannel.objects.create(student=self.student, channel_name="Unlinked")
        result = services.sync_all(force=True)

        self.assertEqual(result.considered, 1)
        self.assertEqual(result.synced, 1)

    def test_a_manual_cadence_leaves_the_scheduled_sweep_with_nothing_to_do(self):
        from apps.core.models import SystemSetting

        SystemSetting.objects.update_or_create(
            key="youtube_sync_cadence",
            defaults={"value": "MANUAL", "value_type": "string", "label": "YouTube sync cadence"},
        )
        result = services.sync_all()

        self.assertEqual(result.considered, 0)
        self.assertFalse(self.transport.calls)

    def test_a_recently_synced_channel_is_not_synced_again_within_the_cadence(self):
        self.channel.last_synced_at = timezone.now()
        self.channel.save(update_fields=["last_synced_at"])
        self.assertEqual(services.sync_all().considered, 0)

    def test_never_synced_channels_come_first(self):
        fresh = YoutubeChannel.objects.create(
            student=self.student, channel_name="Never synced", youtube_channel_id="UC_NEW"
        )
        self.channel.last_synced_at = timezone.now() - timedelta(days=5)
        self.channel.save(update_fields=["last_synced_at"])

        self.assertEqual(list(services.due_channels())[0].pk, fresh.pk)

    def test_a_manual_sync_writes_an_audit_entry(self):
        services.sync_now(self.admin, self.channel)
        self.assertTrue(AuditLog.objects.filter(entity_type="YoutubeChannel").exists())


@override_settings(**OAUTH_SETTINGS)
class GrowthTests(SyncTestCase):
    def _snapshot(self, days_ago: int, subscribers: int | None, views: int | None = None) -> ChannelSnapshot:
        row = ChannelSnapshot.objects.create(
            channel=self.channel,
            source=MetricSource.PUBLIC_API,
            subscriber_count=subscribers,
            view_count=views,
        )
        ChannelSnapshot.objects.filter(pk=row.pk).update(
            captured_at=timezone.now() - timedelta(days=days_ago)
        )
        row.refresh_from_db()
        return row

    def test_reports_no_history_rather_than_a_zero(self):
        summary = services.growth_summary(self.channel)
        self.assertFalse(summary["available"])
        self.assertIn("No public snapshot", summary["reason"])

    def test_computes_deltas_against_the_closest_earlier_snapshot(self):
        self._snapshot(31, 1000, 50000)
        self._snapshot(8, 1200, 60000)
        self._snapshot(0, 1500, 90000)

        periods = {p["label"]: p for p in services.growth_summary(self.channel)["periods"]}
        self.assertEqual(periods["Last 7 days"]["subscribers"], 300)
        self.assertEqual(periods["Last 30 days"]["subscribers"], 500)
        self.assertEqual(periods["Last 30 days"]["views"], 40000)

    def test_says_so_when_there_is_no_baseline_for_a_period(self):
        self._snapshot(0, 1500)
        periods = {p["label"]: p for p in services.growth_summary(self.channel)["periods"]}
        self.assertFalse(periods["Last 30 days"]["available"])
        self.assertIn("compare against", periods["Last 30 days"]["reason"])

    def test_a_hidden_subscriber_count_yields_no_delta_rather_than_a_fake_one(self):
        self._snapshot(8, None)
        self._snapshot(0, None)
        periods = {p["label"]: p for p in services.growth_summary(self.channel)["periods"]}
        self.assertIsNone(periods["Last 7 days"]["subscribers"])

    def test_the_series_only_includes_public_rows(self):
        self._snapshot(1, 1000)
        ChannelSnapshot.objects.create(
            channel=self.channel, source=MetricSource.ANALYTICS_API, watch_time_minutes=99
        )
        self.assertEqual(len(services.growth_series(self.channel)), 1)


@override_settings(**OAUTH_SETTINGS)
class AvailabilityTests(SyncTestCase):
    def test_explains_a_missing_channel_id(self):
        self.channel.youtube_channel_id = None
        self.channel.save(update_fields=["youtube_channel_id"])

        state = services.availability(self.channel)
        self.assertFalse(state["public_available"])
        self.assertIn("No YouTube channel ID", state["public_reason"])

    def test_explains_that_analytics_needs_the_students_authorization(self):
        state = services.availability(self.channel)
        self.assertTrue(state["public_available"])
        self.assertFalse(state["private_available"])
        self.assertIn("has not authorized", state["private_reason"])

    def test_explains_a_disconnection_with_its_date(self):
        self.grant(revoked_at=timezone.now(), encrypted_refresh_token="")
        state = services.availability(self.channel)
        self.assertIn("disconnected on", state["private_reason"])

    def test_reports_everything_available_once_connected(self):
        self.grant()
        state = services.availability(self.channel)
        self.assertTrue(state["private_available"])
        self.assertIsNone(state["private_reason"])
        self.assertFalse(state["revenue_authorized"])

    @override_settings(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="")
    def test_says_when_oauth_is_not_configured_at_all(self):
        state = services.availability(self.channel)
        self.assertIn("not configured", state["private_reason"])


@override_settings(**OAUTH_SETTINGS)
class ScreenTests(SyncTestCase):
    """
    The Phase 3 screens render, scope their rows, and refuse the roles that may
    not reach them. Signed in as an instructor, because a Super Admin would be
    held at the TOTP challenge by the MFA middleware.
    """

    def setUp(self):
        super().setUp()
        self.instructor = User.objects.create_user(
            email="tutor@example.test", password="instructor-pw-12345",
            role=UserRole.INSTRUCTOR, full_name="Tutor",
        )
        self.student.instructor = self.instructor
        self.student.save(update_fields=["instructor"])
        self.client.force_login(self.instructor)

    def test_the_channel_page_renders_with_no_data_at_all(self):
        response = self.client.get(self.channel.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "has not authorized")
        self.assertContains(response, "Never")

    def test_the_channel_page_renders_after_a_sync(self):
        self.grant()
        services.sync_channel(self.channel)
        response = self.client.get(self.channel.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1500")

    def test_the_video_list_and_detail_render(self):
        services.sync_channel(self.channel)
        video = Video.objects.get(youtube_video_id="vid1")

        self.assertEqual(self.client.get("/videos/").status_code, 200)
        self.assertEqual(self.client.get(f"/videos/{video.pk}/").status_code, 200)

    def test_the_analytics_overview_renders_and_states_the_quota(self):
        response = self.client.get("/analytics/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quota used today")

    def test_the_consent_screen_states_what_will_be_read(self):
        response = self.client.get(f"/channels/{self.channel.pk}/analytics/connect/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Read-only")
        self.assertContains(response, oauth.CONSENT_VERSION)

    def test_a_student_cannot_see_another_students_channel(self):
        other = User.objects.create_user(
            email="other@example.test", password="student-pw-12345",
            role=UserRole.STUDENT, full_name="Other Student",
        )
        self.client.force_login(other)

        # The console URL now bounces students to their own portal before the
        # lookup is even attempted — students have no business on this screen
        # regardless of whose channel it is.
        console = self.client.get(self.channel.get_absolute_url())
        self.assertEqual(console.status_code, 302)
        self.assertEqual(console.headers["Location"], "/portal/")

        # This account has no enrolment linked to it at all, so the portal
        # explains that rather than showing a channel. The 404 case for a
        # *linked* student asking after someone else's channel is covered by
        # apps.portal.tests.RowScopingTests.
        portal = self.client.get(f"/portal/channels/{self.channel.pk}/")
        self.assertEqual(portal.status_code, 200)
        self.assertContains(portal, "No student record is linked")
        self.assertNotContains(portal, "Test Channel")

    def test_the_callback_refuses_an_unsigned_state(self):
        response = self.client.get("/api/oauth/youtube/callback/?code=abc&state=forged")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ChannelOauthGrant.objects.exists())

    def test_the_callback_stores_the_grant_for_the_channel_named_in_the_state(self):
        state = oauth.build_state(self.channel.pk, self.instructor.pk)
        response = self.client.get(f"/api/oauth/youtube/callback/?code=abc&state={state}")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ChannelOauthGrant.objects.count(), 1)

    def test_a_declined_consent_is_recorded_as_declined_not_as_an_error(self):
        state = oauth.build_state(self.channel.pk, self.instructor.pk)
        response = self.client.get(f"/api/oauth/youtube/callback/?error=access_denied&state={state}")

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ChannelOauthGrant.objects.exists())

    def test_sync_now_requires_a_post(self):
        self.assertEqual(self.client.get(f"/channels/{self.channel.pk}/sync/").status_code, 405)

    def test_sync_now_runs_and_redirects(self):
        response = self.client.post(f"/channels/{self.channel.pk}/sync/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ChannelSnapshot.objects.count(), 1)

    def test_read_only_management_may_view_but_not_sync(self):
        viewer = User.objects.create_user(
            email="board@example.test", password="viewer-pw-12345",
            role=UserRole.MANAGEMENT_READONLY, full_name="Board Member",
        )
        self.client.force_login(viewer)

        self.assertEqual(self.client.get("/analytics/").status_code, 200)
        self.assertEqual(self.client.post(f"/channels/{self.channel.pk}/sync/").status_code, 403)
        self.assertEqual(
            self.client.post(f"/channels/{self.channel.pk}/analytics/disconnect/").status_code, 403
        )


@override_settings(**OAUTH_SETTINGS)
class JobTests(SyncTestCase):
    def test_the_scheduled_job_reports_what_it_did(self):
        from apps.monitoring.jobs import youtube_sync

        self.channel.last_synced_at = None
        self.channel.save(update_fields=["last_synced_at"])
        payload = youtube_sync()

        self.assertEqual(payload["synced"], 1)
        self.assertEqual(payload["cadence"], "DAILY")

    @override_settings(YOUTUBE_API_KEY="")
    def test_the_job_skips_with_a_reason_rather_than_claiming_success(self):
        from apps.monitoring.jobs import youtube_sync

        payload = youtube_sync()
        self.assertTrue(payload["skipped"])
        self.assertIn("YOUTUBE_API_KEY", payload["reason"])
