"""
A student submitting their own YouTube channel from the portal.

Only after research is approved; it always arrives UNDER_REVIEW with the approved
niche attached; staff are told; and nothing a student types can approve, verify
or monetize a channel, or claim one that is already registered.
"""

from datetime import date

from django.test import TestCase

from apps.academy.models import Batch, Student, StudentActivity
from apps.accounts.models import User
from apps.core.enums import ActivityKind, AuditAction, ChannelStatus, StudentStatus, SubmissionStatus, UserRole
from apps.core.models import AuditLog
from apps.monitoring.models import Notification
from apps.research.models import ResearchSubmission
from apps.youtube.forms import StudentChannelForm
from apps.youtube.models import YoutubeChannel
from apps.youtube.submissions import ChannelSubmissionError, submit_student_channel

CHANNEL_ID = "UC" + "a" * 22


def valid_post(**overrides):
    data = {
        "channel_name": "Money Made Simple",
        "channel_url": "https://www.youtube.com/@moneymadesimple",
        "youtube_channel_id": CHANNEL_ID,
        "channel_creation_date": "2026-09-20",
        "content_type": "SHORTS",
        "primary_language": "English",
        "upload_schedule": "3 Shorts a week",
        "notes": "",
    }
    data.update(overrides)
    return data


class ChannelSubmitTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            email="tutor@example.test", password="tutor-pw-12345", role=UserRole.INSTRUCTOR, full_name="Tutor",
        )
        self.user = User.objects.create_user(
            email="asha@example.test", password="student-pw-12345", role=UserRole.STUDENT, full_name="Asha",
        )
        batch = Batch.objects.create(code="B-1", name="Batch 1", start_date=date(2026, 1, 1))
        self.student = Student.objects.create(
            enrollment_id="100DAI-2026-0001", full_name="Asha", email="asha@example.test",
            enrollment_date=date(2026, 1, 5), batch=batch, instructor=self.instructor, user=self.user,
            status=StudentStatus.RESEARCH_APPROVED,
        )
        self.client.force_login(self.user)

    def approve_research(self, student=None):
        return ResearchSubmission.objects.create(
            student=student or self.student, version=1, status=SubmissionStatus.APPROVED,
            niche="Personal finance", sub_niche="Budgeting for students",
            target_audience="University students", target_country="Pakistan", content_format="Faceless Shorts",
        )

    # --- Gate ----------------------------------------------------------------

    def test_not_offered_before_research_is_approved(self):
        self.assertNotContains(self.client.get("/portal/channels/"), "/portal/channels/new/")
        response = self.client.get("/portal/channels/new/")
        self.assertRedirects(response, "/portal/channels/", fetch_redirect_response=False)
        self.client.post("/portal/channels/new/", valid_post())
        self.assertFalse(YoutubeChannel.objects.exists())

    def test_offered_once_research_is_approved(self):
        self.approve_research()
        self.assertContains(self.client.get("/portal/channels/"), "Submit my channel")
        self.assertEqual(self.client.get("/portal/channels/new/").status_code, 200)

    # --- Submitting ------------------------------------------------------------

    def test_a_submission_arrives_under_review_with_the_approved_niche(self):
        self.approve_research()
        response = self.client.post("/portal/channels/new/", valid_post())
        channel = YoutubeChannel.objects.get()
        self.assertRedirects(response, f"/portal/channels/{channel.pk}/", fetch_redirect_response=False)

        self.assertEqual(channel.student, self.student)
        self.assertEqual(channel.status, ChannelStatus.UNDER_REVIEW)
        self.assertFalse(channel.ownership_verified)
        self.assertEqual(channel.youtube_channel_id, CHANNEL_ID)
        self.assertEqual(channel.niche, "Personal finance")
        self.assertEqual(channel.sub_niche, "Budgeting for students")
        self.assertContains(self.client.get(f"/portal/channels/{channel.pk}/"), "Your instructor is checking")

    def test_staff_only_fields_cannot_be_set_by_the_student(self):
        self.approve_research()
        self.client.post("/portal/channels/new/", valid_post(
            status=ChannelStatus.MONETIZED, ownership_verified="on", monetization_status="MONETIZED",
            student=self.student.pk,
        ))
        channel = YoutubeChannel.objects.get()
        self.assertEqual(channel.status, ChannelStatus.UNDER_REVIEW)
        self.assertFalse(channel.ownership_verified)

    def test_the_channel_id_is_optional(self):
        self.approve_research()
        self.client.post("/portal/channels/new/", valid_post(youtube_channel_id=""))
        self.assertIsNone(YoutubeChannel.objects.get().youtube_channel_id)

    def test_the_instructor_is_notified_and_it_is_recorded(self):
        self.approve_research()
        self.client.post("/portal/channels/new/", valid_post())
        channel = YoutubeChannel.objects.get()

        note = Notification.objects.get(user=self.instructor)
        self.assertIn("Money Made Simple", note.title)
        self.assertEqual(note.link_url, f"/channels/{channel.pk}/")

        activity = StudentActivity.objects.get(student=self.student, kind=ActivityKind.CHANNEL_SUBMITTED)
        self.assertTrue(activity.is_student_action)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditAction.CREATE, entity_type="YoutubeChannel", entity_id=channel.pk,
        ).exists())

    def test_admins_are_notified_when_no_instructor_is_assigned(self):
        admin = User.objects.create_user(
            email="admin@example.test", password="admin-pw-12345", role=UserRole.SUPER_ADMIN, full_name="Admin",
        )
        self.student.instructor = None
        self.student.save()
        self.approve_research()
        self.client.post("/portal/channels/new/", valid_post())
        self.assertTrue(Notification.objects.filter(user=admin).exists())

    # --- Validation ------------------------------------------------------------

    def test_only_youtube_links_are_accepted(self):
        self.approve_research()
        response = self.client.post("/portal/channels/new/", valid_post(channel_url="https://example.com/me"))
        self.assertContains(response, "link to your channel on youtube.com")
        response = self.client.post("/portal/channels/new/", valid_post(channel_url=""))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(YoutubeChannel.objects.exists())

    def test_a_malformed_channel_id_is_refused(self):
        self.approve_research()
        response = self.client.post("/portal/channels/new/", valid_post(youtube_channel_id="not-an-id"))
        self.assertContains(response, "starts with UC")
        self.assertFalse(YoutubeChannel.objects.exists())

    def test_a_channel_registered_to_someone_else_cannot_be_claimed(self):
        other = Student.objects.create(
            enrollment_id="100DAI-2026-0002", full_name="Other", email="other@example.test",
            enrollment_date=date(2026, 1, 5), batch=self.student.batch,
        )
        YoutubeChannel.objects.create(
            student=other, channel_name="Theirs", youtube_channel_id=CHANNEL_ID,
            channel_url="https://www.youtube.com/@theirs",
        )
        self.approve_research()

        by_id = self.client.post("/portal/channels/new/", valid_post())
        self.assertContains(by_id, "already registered in the academy")
        by_url = self.client.post("/portal/channels/new/", valid_post(
            youtube_channel_id="", channel_url="https://www.youtube.com/@theirs",
        ))
        self.assertContains(by_url, "already registered in the academy")
        self.assertEqual(YoutubeChannel.objects.filter(student=self.student).count(), 0)

    # --- Boundaries ------------------------------------------------------------

    def test_the_console_form_stays_closed_to_students(self):
        self.approve_research()
        self.assertRedirects(self.client.get("/channels/new/"), "/portal/", fetch_redirect_response=False)

    def test_staff_cannot_use_the_portal_form(self):
        self.approve_research()
        self.client.force_login(self.instructor)
        self.assertNotEqual(self.client.get("/portal/channels/new/").status_code, 200)
        self.client.post("/portal/channels/new/", valid_post())
        self.assertFalse(YoutubeChannel.objects.exists())

    def test_the_service_refuses_anyone_but_the_student(self):
        self.approve_research()
        form = StudentChannelForm(data=valid_post())
        self.assertTrue(form.is_valid(), form.errors)
        intruder = User.objects.create_user(
            email="intruder@example.test", password="pw-12345-intruder", role=UserRole.STUDENT, full_name="X",
        )
        with self.assertRaises(ChannelSubmissionError):
            submit_student_channel(intruder, self.student, form)

    def test_staff_see_the_submission_awaiting_review(self):
        self.approve_research()
        self.client.post("/portal/channels/new/", valid_post())
        channel = YoutubeChannel.objects.get()
        self.client.force_login(self.instructor)
        response = self.client.get(f"/channels/{channel.pk}/")
        self.assertContains(response, "Awaiting review")
        self.assertContains(response, f"/channels/{channel.pk}/edit/")
