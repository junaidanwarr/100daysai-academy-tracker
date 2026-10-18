"""
A student connecting YouTube Analytics for their own channel, from the portal.

Consent belongs to the channel owner, so the student grants and withdraws it
themselves; nobody else's channel can be connected; and the console no longer
offers staff a button they cannot meaningfully use.
"""

from __future__ import annotations

import urllib.parse

from django.test import override_settings

from apps.accounts.models import User
from apps.accounts.services import MFA_SESSION_KEY
from apps.academy.models import Student
from apps.core.enums import UserRole
from apps.core.permissions import PermissionDenied
from apps.youtube import oauth, services
from apps.youtube.models import ChannelOauthGrant, YoutubeChannel
from apps.youtube.tests_sync import OAUTH_SETTINGS, SyncTestCase


@override_settings(**OAUTH_SETTINGS)
class StudentConsentTests(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email="student@example.test", password="student-pw-12345", role=UserRole.STUDENT, full_name="Test Student"
        )
        self.student.user = self.user
        self.student.save(update_fields=["user"])
        self.client.force_login(self.user)

    def other_channel(self):
        other = Student.objects.create(
            enrollment_id="100DAI-2026-0002", full_name="Other", email="other@example.test",
            enrollment_date=self.student.enrollment_date, batch=self.student.batch,
        )
        return YoutubeChannel.objects.create(student=other, channel_name="Other", youtube_channel_id="UC_OTHER")

    def callback(self, channel, user=None, **params):
        state = oauth.build_state(channel.pk, (user or self.user).pk)
        return self.client.get("/api/oauth/youtube/callback/", {"state": state, "code": "auth-code", **params})

    # --- Where the button is ---------------------------------------------------

    def test_the_portal_offers_connect_analytics(self):
        url = f"/portal/channels/{self.channel.pk}/analytics/connect/"
        self.assertContains(self.client.get("/portal/analytics/"), url)
        self.assertContains(self.client.get(f"/portal/channels/{self.channel.pk}/"), url)
        self.assertContains(self.client.get(url), "Continue to Google")

    @override_settings(GOOGLE_CLIENT_ID="")
    def test_no_button_when_oauth_is_not_configured(self):
        self.assertNotContains(self.client.get("/portal/analytics/"), "/analytics/connect/")

    def test_the_console_tells_staff_the_student_connects(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session[MFA_SESSION_KEY] = True
        session.save()
        response = self.client.get(f"/channels/{self.channel.pk}/")
        self.assertNotContains(response, f"/channels/{self.channel.pk}/analytics/connect/")
        self.assertContains(response, "The student connects this themselves")

    # --- Starting -------------------------------------------------------------

    def test_continuing_sends_the_student_to_google_with_their_own_state(self):
        response = self.client.post(f"/portal/channels/{self.channel.pk}/analytics/connect/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(oauth.AUTH_ENDPOINT))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(response["Location"]).query)
        state = oauth.read_state(query["state"][0])
        self.assertEqual(state, {"channel": str(self.channel.pk), "user": str(self.user.pk)})

    def test_another_students_channel_is_a_404(self):
        other = self.other_channel()
        self.assertEqual(self.client.get(f"/portal/channels/{other.pk}/analytics/connect/").status_code, 404)
        self.assertEqual(self.client.post(f"/portal/channels/{other.pk}/analytics/connect/").status_code, 404)

    def test_the_service_refuses_a_student_who_does_not_own_the_channel(self):
        with self.assertRaises(PermissionDenied):
            services.start_connection(self.user, self.other_channel())

    # --- Google's callback ----------------------------------------------------

    def test_the_callback_stores_the_grant_and_returns_to_the_portal(self):
        response = self.callback(self.channel)
        self.assertRedirects(response, f"/portal/channels/{self.channel.pk}/", fetch_redirect_response=False)
        grant = ChannelOauthGrant.objects.get(channel=self.channel)
        self.assertIsNone(grant.revoked_at)
        self.assertContains(self.client.get("/portal/analytics/"), "Analytics connected")

    def test_a_state_issued_to_someone_else_is_refused(self):
        response = self.callback(self.channel, user=self.admin)
        self.assertRedirects(response, "/portal/analytics/", fetch_redirect_response=False)
        self.assertFalse(ChannelOauthGrant.objects.exists())

    def test_a_signed_state_for_another_students_channel_is_a_404(self):
        response = self.callback(self.other_channel())
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ChannelOauthGrant.objects.exists())

    def test_declining_at_google_stores_nothing(self):
        response = self.callback(self.channel, error="access_denied")
        self.assertRedirects(response, f"/portal/channels/{self.channel.pk}/", fetch_redirect_response=False)
        self.assertFalse(ChannelOauthGrant.objects.exists())

    # --- Withdrawing ----------------------------------------------------------

    def test_the_student_can_disconnect(self):
        self.grant()
        response = self.client.post(f"/portal/channels/{self.channel.pk}/analytics/disconnect/")
        self.assertRedirects(response, f"/portal/channels/{self.channel.pk}/", fetch_redirect_response=False)
        self.assertIsNotNone(ChannelOauthGrant.objects.get().revoked_at)
