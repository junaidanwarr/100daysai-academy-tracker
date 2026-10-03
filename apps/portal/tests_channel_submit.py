"""
A student submitting their own YouTube channel from the portal.

Only after research is approved; it always arrives UNDER_REVIEW with the approved
niche attached; staff are told; and nothing a student types can approve, verify
or monetize a channel, or claim one that is already registered.
"""

from datetime import date

from django.test import TestCase

from apps.academy.models import Batch, Student, StudentActivity, StudentStatusHistory
from apps.accounts.models import User
from apps.accounts.services import MFA_SESSION_KEY
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
        page = self.client.get(f"/portal/channels/{channel.pk}/")
        self.assertContains(page, "Your instructor is checking")
        # With an ID, sync does not wait for the review, so the page must not claim it does.
        self.assertContains(page, "does not wait for the review")

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
        channel = YoutubeChannel.objects.get()
        self.assertIsNone(channel.youtube_channel_id)
        self.assertContains(self.client.get(f"/portal/channels/{channel.pk}/"), "once its channel ID is recorded")

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


class ChannelConfirmedMovesStudentTests(TestCase):
    """Staff confirming a channel moves the student to Channel Created — forward only."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@example.test", password="admin-pw-12345", role=UserRole.SUPER_ADMIN, full_name="Admin",
        )
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
        ResearchSubmission.objects.create(
            student=self.student, version=1, status=SubmissionStatus.APPROVED, niche="Personal finance",
        )
        self.channel = YoutubeChannel.objects.create(
            student=self.student, channel_name="Money Made Simple", status=ChannelStatus.UNDER_REVIEW,
            channel_url="https://www.youtube.com/@moneymadesimple",
        )
        self.client.force_login(self.instructor)

    def edit(self, status, channel=None):
        channel = channel or self.channel
        return self.client.post(f"/channels/{channel.pk}/edit/", {
            "student": channel.student.pk, "channel_name": channel.channel_name,
            "channel_url": channel.channel_url or "", "content_type": "MIXED",
            "status": status, "monetization_status": "UNKNOWN",
        }, follow=True)

    def history(self):
        return StudentStatusHistory.objects.filter(student=self.student, to_status=StudentStatus.CHANNEL_CREATED)

    def test_approving_the_channel_moves_the_student_to_channel_created(self):
        response = self.edit(ChannelStatus.APPROVED)
        self.assertContains(response, "moved to Channel Created")
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.CHANNEL_CREATED)
        self.assertEqual(self.student.roadmap_stage, "CHANNEL_CREATION")
        entry = self.history().get()
        self.assertEqual(entry.changed_by, self.instructor)
        self.assertIn("Money Made Simple", entry.reason)

    def test_the_student_is_told(self):
        self.edit(ChannelStatus.ACTIVE)
        note = Notification.objects.get(user=self.user)
        self.assertIn("Channel confirmed", note.title)
        self.assertEqual(note.link_url, f"/portal/channels/{self.channel.pk}/")

    def test_from_channel_creation_pending_too(self):
        self.student.status = StudentStatus.CHANNEL_CREATION_PENDING
        self.student.save()
        self.edit(ChannelStatus.APPROVED)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.CHANNEL_CREATED)

    def test_re_saving_a_confirmed_channel_moves_no_one_again(self):
        self.edit(ChannelStatus.APPROVED)
        self.edit(ChannelStatus.ACTIVE)
        self.edit(ChannelStatus.MONETIZED)
        self.assertEqual(self.history().count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)

    def test_states_short_of_confirmed_move_no_one(self):
        for status in (ChannelStatus.PENDING, ChannelStatus.UNDER_REVIEW, ChannelStatus.INACTIVE):
            self.edit(status)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.RESEARCH_APPROVED)

    def test_a_student_further_along_or_flagged_is_left_alone(self):
        for status in (StudentStatus.CONTENT_PRODUCTION_STARTED, StudentStatus.AT_RISK, StudentStatus.INACTIVE):
            with self.subTest(status=status):
                self.student.status = status
                self.student.save()
                self.channel.status = ChannelStatus.UNDER_REVIEW
                self.channel.save()
                self.edit(ChannelStatus.APPROVED)
                self.student.refresh_from_db()
                self.assertEqual(self.student.status, status)
        self.assertFalse(self.history().exists())

    def test_a_channel_cannot_be_confirmed_on_edit_without_approved_research(self):
        ResearchSubmission.objects.update(status=SubmissionStatus.REVISION_REQUESTED)
        response = self.edit(ChannelStatus.APPROVED)
        self.assertContains(response, "its status was kept")
        self.channel.refresh_from_db()
        self.student.refresh_from_db()
        self.assertEqual(self.channel.status, ChannelStatus.UNDER_REVIEW)
        self.assertEqual(self.student.status, StudentStatus.RESEARCH_APPROVED)

    def test_adding_a_channel_already_active_from_the_console_also_moves_the_student(self):
        self.channel.delete()
        # Only an administrator adds channels from the console.
        self.client.force_login(self.admin)
        session = self.client.session
        session[MFA_SESSION_KEY] = True
        session.save()
        response = self.client.post("/channels/new/", {
            "student": self.student.pk, "channel_name": "Second Go", "content_type": "MIXED",
            "status": ChannelStatus.ACTIVE, "monetization_status": "UNKNOWN",
        }, follow=True)
        self.assertContains(response, "moved to Channel Created")
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.CHANNEL_CREATED)
