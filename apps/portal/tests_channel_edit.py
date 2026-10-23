"""
A student correcting a channel they already submitted.

Everything is editable while it is under review; once staff confirm it the link
and channel ID lock; a frozen channel cannot be touched; nobody edits another
student's channel; and staff are told what changed.
"""

from datetime import date

from django.test import TestCase

from apps.academy.models import Batch, Student
from apps.accounts.models import User
from apps.core.crypto import encrypt
from apps.core.enums import AuditAction, ChannelStatus, StudentStatus, UserRole
from apps.core.models import AuditLog
from apps.monitoring.models import Notification
from apps.youtube import oauth
from apps.youtube.models import ChannelOauthGrant, YoutubeChannel

OLD_ID = "UC" + "a" * 22
NEW_ID = "UC" + "b" * 22


def post(**overrides):
    data = {
        "channel_name": "Money Made Simple",
        "channel_url": "https://www.youtube.com/@moneymadesimple",
        "youtube_channel_id": OLD_ID,
        "channel_creation_date": "2026-09-20",
        "content_type": "SHORTS",
        "primary_language": "English",
        "upload_schedule": "3 Shorts a week",
        "notes": "",
    }
    data.update(overrides)
    return data


class ChannelEditTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            email="tutor@example.test", password="tutor-pw-12345", role=UserRole.INSTRUCTOR, full_name="Tutor",
        )
        self.user = User.objects.create_user(
            email="asha@example.test", password="student-pw-12345", role=UserRole.STUDENT, full_name="Asha",
        )
        self.batch = Batch.objects.create(code="B-1", name="Batch 1", start_date=date(2026, 1, 1))
        self.student = Student.objects.create(
            enrollment_id="100DAI-2026-0001", full_name="Asha", email="asha@example.test",
            enrollment_date=date(2026, 1, 5), batch=self.batch, instructor=self.instructor, user=self.user,
            status=StudentStatus.RESEARCH_APPROVED,
        )
        self.channel = YoutubeChannel.objects.create(
            student=self.student, channel_name="Money Made Simple",
            channel_url="https://www.youtube.com/@moneymadesimple", youtube_channel_id=OLD_ID,
            content_type="SHORTS", primary_language="English", upload_schedule="3 Shorts a week",
            channel_creation_date=date(2026, 9, 20), status=ChannelStatus.UNDER_REVIEW,
        )
        self.url = f"/portal/channels/{self.channel.pk}/edit/"
        self.client.force_login(self.user)

    def test_offered_on_the_channel_page_and_the_list(self):
        self.assertContains(self.client.get(f"/portal/channels/{self.channel.pk}/"), self.url)
        self.assertContains(self.client.get("/portal/channels/"), self.url)

    def test_under_review_everything_can_change(self):
        response = self.client.post(self.url, post(
            channel_name="Money Made Easy", channel_url="https://www.youtube.com/@moneymadeeasy",
            youtube_channel_id=NEW_ID, upload_schedule="Daily Shorts",
        ))
        self.assertRedirects(response, f"/portal/channels/{self.channel.pk}/", fetch_redirect_response=False)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.channel_name, "Money Made Easy")
        self.assertEqual(self.channel.channel_url, "https://www.youtube.com/@moneymadeeasy")
        self.assertEqual(self.channel.youtube_channel_id, NEW_ID)
        self.assertEqual(self.channel.status, ChannelStatus.UNDER_REVIEW)

    def test_saving_its_own_link_and_id_is_not_a_duplicate(self):
        response = self.client.post(self.url, post(upload_schedule="Daily Shorts"))
        self.assertEqual(response.status_code, 302)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.upload_schedule, "Daily Shorts")

    def test_cannot_take_another_channels_link(self):
        other = Student.objects.create(
            enrollment_id="100DAI-2026-0002", full_name="Other", email="o@example.test",
            enrollment_date=date(2026, 1, 5), batch=self.batch,
        )
        YoutubeChannel.objects.create(
            student=other, channel_name="Theirs", channel_url="https://www.youtube.com/@theirs",
            youtube_channel_id=NEW_ID,
        )
        response = self.client.post(self.url, post(channel_url="https://www.youtube.com/@theirs", youtube_channel_id=NEW_ID))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already registered")
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.youtube_channel_id, OLD_ID)

    def test_once_confirmed_the_link_and_id_are_locked(self):
        self.channel.status = ChannelStatus.APPROVED
        self.channel.save(update_fields=["status"])
        self.assertContains(self.client.get(self.url), "locked")
        self.client.post(self.url, post(
            channel_name="Renamed", channel_url="https://www.youtube.com/@elsewhere", youtube_channel_id=NEW_ID,
        ))
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.channel_name, "Renamed")
        self.assertEqual(self.channel.channel_url, "https://www.youtube.com/@moneymadesimple")
        self.assertEqual(self.channel.youtube_channel_id, OLD_ID)
        self.assertEqual(self.channel.status, ChannelStatus.APPROVED)

    def test_status_and_staff_fields_cannot_be_set(self):
        self.client.post(self.url, post(status=ChannelStatus.ACTIVE, ownership_verified="on", monetization_status="MONETIZED"))
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.status, ChannelStatus.UNDER_REVIEW)
        self.assertFalse(self.channel.ownership_verified)

    def test_a_frozen_channel_cannot_be_edited(self):
        self.channel.status = ChannelStatus.SUSPENDED
        self.channel.save(update_fields=["status"])
        self.assertNotContains(self.client.get(f"/portal/channels/{self.channel.pk}/"), self.url)
        response = self.client.post(self.url, post(channel_name="Sneaky"))
        self.assertRedirects(response, f"/portal/channels/{self.channel.pk}/", fetch_redirect_response=False)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.channel_name, "Money Made Simple")

    def test_another_students_channel_is_a_404(self):
        other = Student.objects.create(
            enrollment_id="100DAI-2026-0002", full_name="Other", email="o@example.test",
            enrollment_date=date(2026, 1, 5), batch=self.batch,
        )
        theirs = YoutubeChannel.objects.create(student=other, channel_name="Theirs")
        self.assertEqual(self.client.get(f"/portal/channels/{theirs.pk}/edit/").status_code, 404)
        self.assertEqual(self.client.post(f"/portal/channels/{theirs.pk}/edit/", post()).status_code, 404)

    def test_staff_are_told_and_the_change_is_audited(self):
        self.client.post(self.url, post(upload_schedule="Daily Shorts"))
        note = Notification.objects.get(user=self.instructor)
        self.assertIn("Channel edited", note.title)
        self.assertIn("upload schedule", note.body)
        entry = AuditLog.objects.get(action=AuditAction.UPDATE, entity_type="YoutubeChannel")
        self.assertEqual(entry.after, {"upload_schedule": "Daily Shorts"})

    def test_saving_without_changes_tells_nobody(self):
        self.client.post(self.url, post())
        self.assertFalse(Notification.objects.exists())

    def test_changing_the_channel_id_disconnects_analytics(self):
        ChannelOauthGrant.objects.create(
            channel=self.channel, encrypted_refresh_token=encrypt("rt-1"),
            scopes=list(oauth.READONLY_SCOPES), consent_version=oauth.CONSENT_VERSION,
        )
        self.client.post(self.url, post(youtube_channel_id=NEW_ID))
        self.assertIsNotNone(ChannelOauthGrant.objects.get().revoked_at)
