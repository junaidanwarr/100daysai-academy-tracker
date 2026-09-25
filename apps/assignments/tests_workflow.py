"""
The in-app assignment workflow: set, hand in, review, resubmit, and track.

The rules under test are the same ones research lives by — an attempt is never
edited after review, a resubmission is a new version — plus the one that makes
a roster worth having: a student who has handed in nothing still appears.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.academy.models import Batch, Student, StudentActivity
from apps.accounts.models import User
from apps.assignments.models import Assignment, AssignmentSubmission
from apps.assignments.services import (
    AssignmentStateError,
    assignment_roster,
    review_assignment,
    save_assignment,
    submit_assignment,
)
from apps.core.enums import ActivityKind, AssignmentType, StudentStatus, SubmissionStatus, UserRole
from apps.core.permissions import PermissionDenied


class WorkflowTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@example.test", password="admin-pw-12345", role=UserRole.SUPER_ADMIN, full_name="Admin",
        )
        self.tutor = User.objects.create_user(
            email="tutor@example.test", password="tutor-pw-12345", role=UserRole.INSTRUCTOR, full_name="Tutor",
        )
        self.other_tutor = User.objects.create_user(
            email="other@example.test", password="other-pw-12345", role=UserRole.INSTRUCTOR, full_name="Other",
        )
        self.batch = Batch.objects.create(code="B-1", name="Batch 1", start_date=date(2026, 1, 1), instructor=self.tutor)
        self.other_batch = Batch.objects.create(
            code="B-2", name="Batch 2", start_date=date(2026, 1, 1), instructor=self.other_tutor
        )
        self.student_user, self.student = self.make_student("Asha", "0001", self.batch, self.tutor)
        self.peer_user, self.peer = self.make_student("Bram", "0002", self.batch, self.tutor)
        self.outsider_user, self.outsider = self.make_student("Cleo", "0003", self.other_batch, self.other_tutor)

        self.assignment = Assignment.objects.create(
            title="Branding kit", type=AssignmentType.PROJECT, batch=self.batch,
            due_at=timezone.now() + timedelta(days=3), max_score=Decimal("20"),
        )

    def make_student(self, name, number, batch, instructor):
        user = User.objects.create_user(
            email=f"{name.lower()}@example.test", password="student-pw-12345",
            role=UserRole.STUDENT, full_name=name,
        )
        student = Student.objects.create(
            enrollment_id=f"100DAI-2026-{number}", full_name=name, email=user.email,
            enrollment_date=date(2026, 1, 5), batch=batch, instructor=instructor, user=user,
            status=StudentStatus.ACTIVE,
        )
        return user, student

    def hand_in(self, **kwargs):
        kwargs.setdefault("submission_url", "https://drive.example/folder")
        return submit_assignment(self.student_user, self.student, self.assignment, **kwargs)


class SubmitAndReviewTests(WorkflowTestCase):
    def test_a_student_hands_in_work_and_it_is_logged_as_their_activity(self):
        submission = self.hand_in(response_text="Here it is")
        self.assertEqual(submission.version, 1)
        self.assertEqual(submission.status, SubmissionStatus.SUBMITTED)

        entry = StudentActivity.objects.get(kind=ActivityKind.ASSIGNMENT_SUBMITTED)
        self.assertTrue(entry.is_student_action)
        self.assertEqual(entry.target_id, str(submission.pk))
        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.last_activity_at)

    def test_empty_work_is_refused(self):
        with self.assertRaises(AssignmentStateError):
            submit_assignment(self.student_user, self.student, self.assignment, response_text="  ")

    def test_a_second_attempt_waits_for_the_first_review(self):
        self.hand_in()
        with self.assertRaisesMessage(AssignmentStateError, "Wait for the review"):
            self.hand_in()

    def test_work_for_another_batch_cannot_be_handed_in(self):
        with self.assertRaisesMessage(AssignmentStateError, "not set for your batch"):
            submit_assignment(self.outsider_user, self.outsider, self.assignment, submission_url="https://x.example")

    def test_a_closed_assignment_accepts_nothing(self):
        self.assignment.is_active = False
        self.assignment.save()
        with self.assertRaises(AssignmentStateError):
            self.hand_in()

    def test_late_work_is_accepted_but_marked_late(self):
        self.assignment.due_at = timezone.now() - timedelta(days=1)
        self.assignment.save()
        submission = self.hand_in()
        self.assertTrue(submission.is_late)
        self.assertIn("after the due date", StudentActivity.objects.get(kind=ActivityKind.ASSIGNMENT_SUBMITTED).summary)

    def test_review_then_resubmit_creates_version_two_and_keeps_version_one_intact(self):
        first = self.hand_in()
        review_assignment(
            self.tutor, first, decision=SubmissionStatus.REVISION_REQUESTED, score=Decimal("8"),
            feedback="Good names", rejection_reason="Banner missing",
        )
        second = self.hand_in(response_text="Added the banner")

        first.refresh_from_db()
        self.assertEqual(second.version, 2)
        self.assertEqual(second.status, SubmissionStatus.RESUBMITTED)
        self.assertEqual(first.status, SubmissionStatus.REVISION_REQUESTED)
        self.assertEqual(first.rejection_reason, "Banner missing")
        self.assertIsNotNone(first.superseded_at)

        reviewed = StudentActivity.objects.get(kind=ActivityKind.ASSIGNMENT_REVIEWED)
        self.assertFalse(reviewed.is_student_action)
        self.assertEqual(reviewed.actor, self.tutor)

    def test_a_reviewed_attempt_is_never_reviewed_again(self):
        submission = self.hand_in()
        review_assignment(self.tutor, submission, decision=SubmissionStatus.APPROVED, score=Decimal("18"))
        with self.assertRaisesMessage(AssignmentStateError, "never edited"):
            review_assignment(self.tutor, submission, decision=SubmissionStatus.REJECTED, rejection_reason="x")

    def test_approved_work_needs_nothing_more(self):
        submission = self.hand_in()
        review_assignment(self.tutor, submission, decision=SubmissionStatus.APPROVED)
        with self.assertRaisesMessage(AssignmentStateError, "approved"):
            self.hand_in()

    def test_a_score_above_the_maximum_is_refused(self):
        submission = self.hand_in()
        with self.assertRaisesMessage(AssignmentStateError, "maximum"):
            review_assignment(self.tutor, submission, decision=SubmissionStatus.APPROVED, score=Decimal("21"))

    def test_sending_work_back_requires_a_reason(self):
        submission = self.hand_in()
        with self.assertRaisesMessage(AssignmentStateError, "reason"):
            review_assignment(self.tutor, submission, decision=SubmissionStatus.REJECTED)

    def test_a_student_cannot_review(self):
        submission = self.hand_in()
        with self.assertRaises(PermissionDenied):
            review_assignment(self.student_user, submission, decision=SubmissionStatus.APPROVED)


class SettingAssignmentsTests(WorkflowTestCase):
    def test_an_instructor_sets_work_for_their_own_batch_only(self):
        created = save_assignment(self.tutor, {"title": "Scripts", "type": AssignmentType.PROJECT, "batch": self.batch})
        self.assertEqual(created.batch, self.batch)
        with self.assertRaisesMessage(AssignmentStateError, "B-2"):
            save_assignment(self.tutor, {"title": "Sneaky", "type": AssignmentType.PROJECT, "batch": self.other_batch})

    def test_only_an_administrator_sets_academy_wide_work(self):
        with self.assertRaises(AssignmentStateError):
            save_assignment(self.tutor, {"title": "All", "type": AssignmentType.OTHER, "batch": None})
        self.assertIsNone(save_assignment(self.admin, {"title": "All", "type": AssignmentType.OTHER, "batch": None}).batch)


class RosterTests(WorkflowTestCase):
    def test_every_student_appears_including_those_with_nothing_in(self):
        self.hand_in()
        rows, counts = assignment_roster(self.tutor, self.assignment)
        states = {row.student.full_name: row.state for row in rows}
        self.assertEqual(states, {"Asha": "awaiting", "Bram": "missing"})
        self.assertEqual(counts["submitted"], 1)

    def test_nothing_in_past_the_due_date_reads_as_overdue(self):
        self.assignment.due_at = timezone.now() - timedelta(hours=1)
        self.assignment.save()
        rows, counts = assignment_roster(self.admin, self.assignment)
        self.assertEqual(counts["overdue"], 2)

    def test_an_instructor_sees_only_their_own_students(self):
        wide = Assignment.objects.create(title="Reflection", type=AssignmentType.OTHER, batch=None)
        rows, _ = assignment_roster(self.tutor, wide)
        self.assertEqual({r.student.full_name for r in rows}, {"Asha", "Bram"})


class ViewTests(WorkflowTestCase):
    def test_the_student_hands_in_through_the_portal(self):
        self.client.force_login(self.student_user)
        url = f"/portal/assignments/{self.assignment.pk}/"
        self.assertContains(self.client.get("/portal/assignments/"), "Branding kit")
        response = self.client.post(url, {"response_text": "Done", "submission_url": "https://drive.example/x"})
        self.assertRedirects(response, url)
        self.assertEqual(AssignmentSubmission.objects.filter(student=self.student).count(), 1)
        self.assertContains(self.client.get(url), "Wait for the review")

    def test_a_student_cannot_open_another_batchs_assignment(self):
        other = Assignment.objects.create(title="Hidden", type=AssignmentType.PROJECT, batch=self.other_batch)
        self.client.force_login(self.student_user)
        self.assertEqual(self.client.get(f"/portal/assignments/{other.pk}/").status_code, 404)

    def test_the_instructor_reviews_from_the_attempt_page(self):
        submission = self.hand_in()
        self.client.force_login(self.tutor)
        self.assertContains(self.client.get(f"/assignments/{self.assignment.pk}/"), "Asha")
        url = f"/assignments/attempts/{submission.pk}/"
        response = self.client.post(url, {"decision": SubmissionStatus.APPROVED, "score": "17", "feedback": "Great"})
        self.assertRedirects(response, url)
        submission.refresh_from_db()
        self.assertEqual(submission.status, SubmissionStatus.APPROVED)
        self.assertEqual(submission.score, Decimal("17"))

    def test_another_instructors_student_is_out_of_reach(self):
        submission = submit_assignment(
            self.outsider_user, self.outsider,
            Assignment.objects.create(title="B2 work", type=AssignmentType.PROJECT, batch=self.other_batch),
            submission_url="https://x.example",
        )
        self.client.force_login(self.tutor)
        self.assertEqual(self.client.get(f"/assignments/attempts/{submission.pk}/").status_code, 404)

    def test_management_reads_but_cannot_review(self):
        manager = User.objects.create_user(
            email="m@example.test", password="manager-pw-12345", role=UserRole.MANAGEMENT_READONLY, full_name="M",
        )
        submission = self.hand_in()
        self.client.force_login(manager)
        url = f"/assignments/attempts/{submission.pk}/"
        self.assertNotContains(self.client.get(url), "Record review")
        self.assertEqual(self.client.post(url, {"decision": SubmissionStatus.APPROVED}).status_code, 403)
        self.assertEqual(self.client.get("/assignments/new/").status_code, 403)

    def test_an_instructor_creates_an_assignment_from_the_form(self):
        self.client.force_login(self.tutor)
        response = self.client.post("/assignments/new/", {
            "title": "Scripts", "type": AssignmentType.PROJECT, "batch": str(self.batch.pk),
            "due_at": "2030-01-01T10:00", "max_score": "30", "description": "Three scripts", "is_active": "on",
        })
        created = Assignment.objects.get(title="Scripts")
        self.assertRedirects(response, f"/assignments/{created.pk}/")
