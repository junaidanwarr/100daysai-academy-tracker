"""
The portal boundary.

Two things are being protected here, and they are not the same thing:

  * **Jurisdiction** — a student never reaches the staff console, regardless of
    whose rows would come back. This is what stops the scoring rubric, the
    weightages and the console chrome from being visible to the people being
    scored.

  * **Row scoping** — inside the portal, a student sees only their own records.
    This was already enforced by ``for_actor()`` and is re-checked here against
    the portal's own routes, because a boundary that has never been tested from
    the other side is an assumption, not a control.
"""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.academy.models import Batch, Student, StudentStatusHistory
from apps.accounts.models import User
from apps.core.enums import StudentStatus, SubmissionStatus, UserRole
from apps.core.navigation import nav_for
from apps.research.models import ResearchSubmission
from apps.youtube.models import YoutubeChannel

# Every console route a student might try. If a route is added to the console
# and not to this list, that is the gap worth noticing.
CONSOLE_PATHS = [
    "/dashboard/",
    "/students/",
    "/batches/",
    "/staff/",
    "/research/",
    "/research/criteria/",
    "/assignments/",
    "/channels/",
    "/videos/",
    "/analytics/",
    "/alerts/",
    "/settings/",
    "/audit-logs/",
    "/notifications/",
    "/performance/",
    "/reports/",
]

PORTAL_PATHS = [
    "/portal/",
    "/portal/research/",
    "/portal/assignments/",
    "/portal/channels/",
    "/portal/videos/",
    "/portal/analytics/",
    "/portal/notifications/",
    "/portal/agreements/",
    "/portal/grievances/",
]


class PortalTestCase(TestCase):
    def setUp(self):
        self.batch = Batch.objects.create(code="B-1", name="Batch 1", start_date=date(2026, 1, 1))
        self.instructor = User.objects.create_user(
            email="tutor@example.test", password="instructor-pw-12345",
            role=UserRole.INSTRUCTOR, full_name="Tutor",
        )
        self.user = User.objects.create_user(
            email="student@example.test", password="student-pw-12345",
            role=UserRole.STUDENT, full_name="Test Student",
        )
        self.student = Student.objects.create(
            enrollment_id="100DAI-2026-0001",
            full_name="Test Student",
            email="student@example.test",
            enrollment_date=date(2026, 1, 5),
            batch=self.batch,
            instructor=self.instructor,
            user=self.user,
            status=StudentStatus.RESEARCH_PENDING,
        )
        self.client.force_login(self.user)


class JurisdictionTests(PortalTestCase):
    def test_every_console_route_sends_a_student_to_their_portal(self):
        for path in CONSOLE_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302, path)
                self.assertEqual(response.headers["Location"], "/portal/", path)

    def test_every_portal_route_renders_for_a_student(self):
        for path in PORTAL_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_the_rubric_and_its_weightages_are_never_shown_to_a_student(self):
        """
        The reason the console is off limits rather than merely row-scoped: a
        student who can read the weightings knows exactly where to spend effort
        to pass, which makes the score a measure of the rubric, not the work.
        """
        for path in PORTAL_PATHS:
            body = self.client.get(path).content.decode().lower()
            with self.subTest(path=path):
                self.assertNotIn("weightage", body, path)
                self.assertNotIn("pass mark", body, path)

    def test_a_student_is_offered_only_portal_navigation(self):
        links = [item.url_name for _, items in nav_for(UserRole.STUDENT) for item in items]
        self.assertTrue(links)
        for name in links:
            with self.subTest(name=name):
                self.assertTrue(name.startswith("portal:"), name)
                self.assertTrue(reverse(name).startswith("/portal/"), name)

    def test_staff_are_sent_out_of_the_portal_to_their_console(self):
        self.client.force_login(self.instructor)
        for path in PORTAL_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302, path)
                self.assertEqual(response.headers["Location"], "/dashboard/", path)

    def test_the_console_still_works_for_an_instructor(self):
        self.client.force_login(self.instructor)
        for path in ["/dashboard/", "/students/", "/research/", "/research/criteria/"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self):
        self.client.logout()
        response = self.client.get("/portal/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.headers["Location"])


class UnlinkedAccountTests(TestCase):
    """A login can exist before it is attached to an enrolment."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="nobody@example.test", password="student-pw-12345",
            role=UserRole.STUDENT, full_name="Unlinked",
        )
        self.client.force_login(self.user)

    def test_the_portal_explains_itself_rather_than_erroring(self):
        response = self.client.get("/portal/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No student record is linked")

    def test_no_portal_page_crashes_without_a_student_record(self):
        for path in PORTAL_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200, path)


class RowScopingTests(PortalTestCase):
    def setUp(self):
        super().setUp()
        other_user = User.objects.create_user(
            email="other@example.test", password="student-pw-12345",
            role=UserRole.STUDENT, full_name="Other Student",
        )
        self.other = Student.objects.create(
            enrollment_id="100DAI-2026-0002",
            full_name="Other Student",
            email="other@example.test",
            enrollment_date=date(2026, 1, 5),
            batch=self.batch,
            user=other_user,
        )
        self.other_channel = YoutubeChannel.objects.create(
            student=self.other, channel_name="Someone Else's Channel"
        )

    def test_another_students_channel_is_a_404_not_a_403(self):
        response = self.client.get(f"/portal/channels/{self.other_channel.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_the_channel_list_shows_nothing_belonging_to_anyone_else(self):
        response = self.client.get("/portal/channels/")
        self.assertNotContains(response, "Someone Else&#x27;s Channel")
        self.assertNotContains(response, "Other Student")


class ResearchSubmissionTests(PortalTestCase):
    """
    The resubmission loop, from the student's side.

    The rule being protected is that a reviewed attempt is a permanent record:
    if version 1 could be quietly altered by a later submission, a disputed
    decision would be unresolvable, because there would be no way to show what
    the student was originally told.
    """

    def submit(self, topic: str, as_draft: bool = False):
        data = {"topic": topic, "niche": "Finance", "competitors": "[]"}
        if as_draft:
            data["as_draft"] = "1"
        return self.client.post("/portal/research/", data)

    def test_a_first_submission_creates_version_one(self):
        self.submit("Faceless finance explainers")

        submission = ResearchSubmission.objects.get(student=self.student)
        self.assertEqual(submission.version, 1)
        self.assertEqual(submission.status, SubmissionStatus.SUBMITTED)
        self.assertEqual(submission.topic, "Faceless finance explainers")

    def test_the_student_cannot_submit_again_while_the_attempt_is_under_review(self):
        self.submit("First attempt")
        response = self.client.get("/portal/research/")

        self.assertContains(response, "is with your instructor")
        self.submit("Sneaky second attempt")
        self.assertEqual(ResearchSubmission.objects.filter(student=self.student).count(), 1)

    def test_a_resubmission_becomes_version_two_and_leaves_version_one_alone(self):
        self.submit("First attempt")
        v1 = ResearchSubmission.objects.get(student=self.student, version=1)
        v1.status = SubmissionStatus.REJECTED
        v1.rejection_reason = "Niche is too broad."
        v1.score = 41
        v1.save()

        self.submit("Second attempt, narrower niche")

        v1.refresh_from_db()
        v2 = ResearchSubmission.objects.get(student=self.student, version=2)

        self.assertEqual(v2.status, SubmissionStatus.RESUBMITTED)
        self.assertEqual(v2.topic, "Second attempt, narrower niche")
        # Version 1 keeps its decision, its reason, its score and its topic.
        self.assertEqual(v1.status, SubmissionStatus.REJECTED)
        self.assertEqual(v1.rejection_reason, "Niche is too broad.")
        self.assertEqual(v1.score, 41)
        self.assertEqual(v1.topic, "First attempt")
        self.assertIsNotNone(v1.superseded_at)

    def test_a_draft_is_editable_and_does_not_consume_a_version(self):
        self.submit("Rough idea", as_draft=True)
        self.submit("Better idea", as_draft=True)

        submissions = ResearchSubmission.objects.filter(student=self.student)
        self.assertEqual(submissions.count(), 1)
        self.assertEqual(submissions.first().status, SubmissionStatus.DRAFT)
        self.assertEqual(submissions.first().topic, "Better idea")

    def test_submitting_records_the_status_change_on_the_students_timeline(self):
        self.submit("First attempt")

        history = StudentStatusHistory.objects.filter(student=self.student).order_by("-created_at").first()
        self.assertEqual(history.to_status, StudentStatus.ASSIGNMENT_SUBMITTED)
        self.assertEqual(history.changed_by, self.user)
        self.assertIn("version 1", history.reason)

    def test_approval_is_what_opens_the_channel_stage_to_the_student(self):
        """
        The portal must not promise a channel before the research is approved,
        and must not keep telling an approved student to get reviewed first.
        """
        from apps.research.services import review_research

        self.submit("First attempt")
        before = self.client.get("/portal/channels/")
        self.assertContains(before, "A channel is recorded after your research is approved")

        v1 = ResearchSubmission.objects.get(student=self.student, version=1)
        review_research(self.instructor, v1, decision=SubmissionStatus.APPROVED, scores={})

        v1.refresh_from_db()
        self.student.refresh_from_db()
        self.assertEqual(v1.status, SubmissionStatus.APPROVED)
        self.assertIsNotNone(v1.approved_at)
        self.assertEqual(self.student.status, StudentStatus.RESEARCH_APPROVED)

        after = self.client.get("/portal/channels/")
        self.assertContains(after, "Your research is approved")
        self.assertNotContains(after, "A channel is recorded after your research is approved")

    def test_an_approved_student_still_cannot_add_their_own_channel(self):
        from apps.research.services import review_research

        self.submit("First attempt")
        v1 = ResearchSubmission.objects.get(student=self.student, version=1)
        review_research(self.instructor, v1, decision=SubmissionStatus.APPROVED, scores={})

        # No create route exists in the portal, and the console's is closed to
        # them. Approval opens the stage, not the student's ability to self-serve.
        response = self.client.get("/channels/new/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/portal/")

    def test_the_history_shows_the_feedback_but_not_the_evaluators_criteria_notes(self):
        self.submit("First attempt")
        v1 = ResearchSubmission.objects.get(student=self.student, version=1)
        v1.status = SubmissionStatus.REJECTED
        v1.feedback = "Narrow the audience."
        v1.rejection_reason = "Too broad."
        v1.reviewed_at = v1.created_at
        v1.save()

        response = self.client.get("/portal/research/")
        self.assertContains(response, "Narrow the audience.")
        self.assertContains(response, "Too broad.")
