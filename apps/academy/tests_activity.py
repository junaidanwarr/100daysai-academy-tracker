"""
The activity log: written by the action, scoped like students, and honest
about who did what — only the student's own actions count as their activity.
"""

from datetime import date, timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.academy.activity import activity_summary, log_manual_activity, record_activity
from apps.academy.models import Batch, Student, StudentActivity
from apps.academy.services import change_status
from apps.accounts.models import User
from apps.core.enums import ActivityKind, StudentStatus, SubmissionStatus, UserRole
from apps.core.permissions import PermissionDenied
from apps.research.models import ResearchSubmission
from apps.research.services import submit_research


class ActivityTestCase(TestCase):
    def setUp(self):
        self.tutor = User.objects.create_user(
            email="tutor@example.test", password="tutor-pw-12345", role=UserRole.INSTRUCTOR, full_name="Tutor",
        )
        self.other_tutor = User.objects.create_user(
            email="other@example.test", password="other-pw-12345", role=UserRole.INSTRUCTOR, full_name="Other",
        )
        self.batch = Batch.objects.create(code="B-1", name="Batch 1", start_date=date(2026, 1, 1))
        self.user = User.objects.create_user(
            email="s@example.test", password="student-pw-12345", role=UserRole.STUDENT, full_name="Asha",
        )
        self.student = Student.objects.create(
            enrollment_id="100DAI-2026-0001", full_name="Asha", email="s@example.test",
            enrollment_date=date(2026, 1, 5), batch=self.batch, instructor=self.tutor, user=self.user,
            status=StudentStatus.RESEARCH_PENDING,
        )
        self.other = Student.objects.create(
            enrollment_id="100DAI-2026-0002", full_name="Bram", email="b@example.test",
            enrollment_date=date(2026, 1, 5), batch=self.batch, instructor=self.other_tutor,
            status=StudentStatus.ACTIVE,
        )


class RecordingTests(ActivityTestCase):
    def test_staff_actions_are_logged_but_do_not_count_as_the_students_activity(self):
        change_status(self.tutor, self.student, StudentStatus.RESEARCH_IN_PROGRESS, reason="Started")
        entry = StudentActivity.objects.get(kind=ActivityKind.STATUS_CHANGED)
        self.assertFalse(entry.is_student_action)
        self.assertEqual(entry.detail, "Started")
        self.student.refresh_from_db()
        self.assertIsNone(self.student.last_activity_at)

    def test_submitting_research_is_the_students_activity(self):
        submit_research(self.user, self.student, {"topic": "Finance"}, [])
        kinds = set(self.student.activities.values_list("kind", flat=True))
        self.assertIn(ActivityKind.RESEARCH_SUBMITTED, kinds)
        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.last_activity_at)

    def test_a_backdated_entry_never_moves_last_activity_backwards(self):
        now = timezone.now()
        record_activity(self.student, ActivityKind.LOGIN, "Signed in", occurred_at=now)
        record_activity(self.student, ActivityKind.VIDEO_PUBLISHED, "Old video", occurred_at=now - timedelta(days=30))
        self.student.refresh_from_db()
        self.assertEqual(self.student.last_activity_at, now)

    def test_signing_in_is_logged_and_lands_on_the_portal(self):
        response = self.client.post("/login/", {"email": "s@example.test", "password": "student-pw-12345"})
        self.assertRedirects(response, "/portal/")
        self.assertTrue(self.student.activities.filter(kind=ActivityKind.LOGIN).exists())

    def test_staff_log_attendance_but_not_automatic_kinds(self):
        entry = log_manual_activity(self.tutor, self.student, kind=ActivityKind.CLASS_ATTENDED, summary="Live class")
        self.assertTrue(entry.is_student_action)
        with self.assertRaises(ValueError):
            log_manual_activity(self.tutor, self.student, kind=ActivityKind.ASSIGNMENT_SUBMITTED, summary="Fake")

    def test_a_student_cannot_log_activity_for_themselves(self):
        with self.assertRaises(PermissionDenied):
            log_manual_activity(self.user, self.student, kind=ActivityKind.CLASS_ATTENDED, summary="Trust me")


class ScopingTests(ActivityTestCase):
    def setUp(self):
        super().setUp()
        record_activity(self.student, ActivityKind.LOGIN, "Asha signed in")
        record_activity(self.other, ActivityKind.LOGIN, "Bram signed in")
        record_activity(self.student, ActivityKind.NOTE, "Private staff note", actor=self.tutor)

    def test_an_instructor_sees_only_their_own_students_activity(self):
        self.client.force_login(self.tutor)
        body = self.client.get("/activity/").content.decode()
        self.assertIn("Asha signed in", body)
        self.assertNotIn("Bram signed in", body)
        self.assertEqual(self.client.get(f"/activity/?student={self.other.pk}").status_code, 403)

    def test_a_student_never_sees_staff_notes(self):
        self.client.force_login(self.user)
        body = self.client.get("/portal/activity/").content.decode()
        self.assertIn("Asha signed in", body)
        self.assertNotIn("Private staff note", body)
        self.assertNotIn("Bram", body)

    def test_the_summary_separates_active_from_quiet_students(self):
        admin = User.objects.create_user(
            email="a@example.test", password="admin-pw-12345", role=UserRole.SUPER_ADMIN, full_name="Admin",
        )
        summary = activity_summary(admin, days=7)
        self.assertEqual(summary.active_students, 2)
        self.assertEqual(summary.quiet_count, 0)
        self.assertEqual(summary.student_actions, 2)

    def test_logging_from_the_student_page(self):
        self.client.force_login(self.tutor)
        response = self.client.post(
            f"/students/{self.student.pk}/activity/",
            {"kind": ActivityKind.MENTOR_SESSION, "summary": "1:1 on hooks"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.student.activities.filter(kind=ActivityKind.MENTOR_SESSION).exists())
        self.assertContains(self.client.get(f"/students/{self.student.pk}/"), "1:1 on hooks")


class BackfillTests(ActivityTestCase):
    def test_backfill_writes_history_once_with_its_real_timestamp(self):
        when = timezone.now() - timedelta(days=10)
        ResearchSubmission.objects.create(
            student=self.student, version=1, status=SubmissionStatus.APPROVED, topic="Finance",
            submitted_at=when, reviewed_at=when + timedelta(days=1), evaluator=self.tutor,
        )
        call_command("backfill_activity", stdout=open("/dev/null", "w"))
        call_command("backfill_activity", stdout=open("/dev/null", "w"))

        submitted = StudentActivity.objects.filter(kind=ActivityKind.RESEARCH_SUBMITTED)
        self.assertEqual(submitted.count(), 1)
        self.assertEqual(submitted.get().occurred_at, when)
        self.assertEqual(StudentActivity.objects.filter(kind=ActivityKind.RESEARCH_REVIEWED).count(), 1)
