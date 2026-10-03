"""
The channel-research workspace on both research pages.

The parsing and table logic run in the browser (static/js/channel-research.js);
these tests pin what the server is responsible for: the tab is offered on both
pages, each person gets their own browser-storage key, and the sample sheet the
empty state offers is actually served.
"""

from datetime import date
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from apps.academy.models import Batch, Student
from apps.accounts.models import User
from apps.core.enums import StudentStatus, UserRole


class ChannelResearchTabTests(TestCase):
    def setUp(self):
        self.tutor = User.objects.create_user(
            email="tutor@example.test", password="tutor-pw-12345", role=UserRole.INSTRUCTOR, full_name="Tutor",
        )
        self.user = User.objects.create_user(
            email="asha@example.test", password="student-pw-12345", role=UserRole.STUDENT, full_name="Asha",
        )
        batch = Batch.objects.create(code="B-1", name="Batch 1", start_date=date(2026, 1, 1))
        Student.objects.create(
            enrollment_id="100DAI-2026-0001", full_name="Asha", email="asha@example.test",
            enrollment_date=date(2026, 1, 5), batch=batch, instructor=self.tutor, user=self.user,
            status=StudentStatus.RESEARCH_IN_PROGRESS,
        )

    def assert_workspace(self, response, user):
        self.assertContains(response, 'data-tab="channel-research"')
        self.assertContains(response, 'id="cr-root"')
        self.assertContains(response, f'data-store-key="channelResearch.v1.{user.pk}"')
        self.assertContains(response, "js/channel-research.js")
        self.assertContains(response, "xlsx/0.18.5/xlsx.full.min.js")
        self.assertContains(response, "PapaParse/5.4.0/papaparse.min.js")

    def test_staff_research_page_has_the_tab_beside_the_review_queue(self):
        self.client.force_login(self.tutor)
        response = self.client.get("/research/")
        self.assertContains(response, 'data-tab="review-queue"')
        self.assert_workspace(response, self.tutor)

    def test_student_research_page_has_the_tab_beside_their_submission(self):
        self.client.force_login(self.user)
        response = self.client.get("/portal/research/")
        self.assertContains(response, 'data-tab="my-submission"')
        self.assertContains(response, "Submit for review")
        self.assert_workspace(response, self.user)

    def test_the_sample_sheet_is_shipped_with_the_static_files(self):
        import json
        path = Path(settings.BASE_DIR) / "static" / "data" / "research-template-sample.json"
        rows = json.loads(path.read_text())
        self.assertEqual(len(rows), 34)
        self.assertEqual(len(rows[0]), 11)  # one value per table column


import json as _json

from apps.core.enums import SubmissionStatus
from apps.research.models import ResearchSubmission
from apps.research.services import review_research


def sheet_payload(rows=None, **extra):
    rows = rows if rows is not None else [
        {"n": 1, "name": "Kickoff Zone", "link": "https://www.youtube.com/channel/UCGZQD0b7VmjFiGXNAMSdZ2Q",
         "angle": "NFL legends", "cat": "NFL / American Football", "videos": 216, "subs": 13300,
         "views": 2361678, "avg": 10933.7, "size": "Medium", "notes": "Joined Jun 2023"},
        {"n": 2, "name": "No Link Channel", "link": None, "angle": "", "cat": "NFL / American Football",
         "videos": 10, "subs": 182, "views": 149376, "avg": 14937.6, "size": "Tiny", "notes": ""},
    ]
    data = {
        "sheet-topic": "Faceless NFL documentaries",
        "sheet-niche": "NFL / American Football",
        "sheet-notes": "",
        "sheet-sheet": _json.dumps({"file": "Maria akram .xlsx", "sheet": "All Channels", "rows": rows}),
    }
    data.update(extra)
    return data


class SheetSubmissionTests(TestCase):
    """A sheet uploaded in the Channel research tab becomes a reviewable research attempt."""

    def setUp(self):
        ChannelResearchTabTests.setUp(self)
        self.student = self.user.student_profile
        self.admin = User.objects.create_user(
            email="admin@example.test", password="admin-pw-12345", role=UserRole.SUPER_ADMIN, full_name="Admin",
        )

    def submit(self, **kw):
        self.client.force_login(self.user)
        return self.client.post("/portal/research/sheet/", sheet_payload(**kw))

    def test_the_tab_offers_submission_once_a_sheet_could_be_loaded(self):
        self.client.force_login(self.user)
        response = self.client.get("/portal/research/")
        self.assertContains(response, 'id="cr-submit"')
        self.assertContains(response, 'name="sheet-topic"')

    def test_submitting_a_sheet_creates_a_research_attempt_in_the_review_queue(self):
        response = self.submit()
        self.assertRedirects(response, "/portal/research/", fetch_redirect_response=False)

        submission = ResearchSubmission.objects.get(student=self.student)
        self.assertEqual(submission.version, 1)
        self.assertEqual(submission.status, SubmissionStatus.SUBMITTED)
        self.assertEqual(submission.topic, "Faceless NFL documentaries")
        self.assertEqual(submission.channel_sheet["file"], "Maria akram .xlsx")
        self.assertEqual(len(submission.channel_sheet["rows"]), 2)

        # Rows with a link become competitors, so the niche rules run on them.
        competitor = submission.competitors.get()
        self.assertEqual(competitor.channel_name, "Kickoff Zone")
        self.assertEqual(competitor.subscriber_count, 13300)
        self.assertEqual(competitor.video_count, 216)
        self.assertIn("Medium", competitor.notes)

        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.ASSIGNMENT_SUBMITTED)

    def test_staff_see_the_sheet_in_the_queue_and_on_the_review_page(self):
        self.submit()
        submission = ResearchSubmission.objects.get(student=self.student)
        self.client.force_login(self.tutor)
        queue = self.client.get("/research/")
        self.assertContains(queue, "Sheet · 2 channels")
        detail = self.client.get(f"/research/{submission.pk}/")
        self.assertContains(detail, 'id="channel-sheet"')
        self.assertContains(detail, 'data-readonly="channel-sheet-data"')
        self.assertContains(detail, "No Link Channel")  # in the embedded sheet data
        self.assertNotContains(detail, "xlsx.full.min.js")  # nothing to parse on a review page

    def test_approval_moves_the_student_on_and_updates_the_dashboard(self):
        self.submit()
        submission = ResearchSubmission.objects.get(student=self.student)
        review_research(self.admin, submission, decision=SubmissionStatus.APPROVED, scores={})

        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.RESEARCH_APPROVED)
        self.client.force_login(self.tutor)
        dashboard = self.client.get("/dashboard/")
        self.assertEqual(dashboard.context["counters"]["research_approved"], 1)
        portal = self.client.get("/portal/research/")  # staff are sent away from the portal
        self.assertNotEqual(portal.status_code, 200)

    def test_a_second_sheet_waits_for_the_review(self):
        self.submit()
        response = self.submit()
        self.assertRedirects(response, "/portal/research/#channel-research", fetch_redirect_response=False)
        self.assertEqual(ResearchSubmission.objects.filter(student=self.student).count(), 1)

    def test_posted_rows_are_checked_on_the_server(self):
        self.submit(rows=[{
            "name": "<script>x</script>", "link": "javascript:alert(1)", "subs": "lots",
            "views": -5, "videos": True, "size": "Huge", "notes": {"nested": 1},
        }])
        row = ResearchSubmission.objects.get(student=self.student).channel_sheet["rows"][0]
        self.assertIsNone(row["link"])
        self.assertIsNone(row["subs"])
        self.assertIsNone(row["views"])
        self.assertIsNone(row["videos"])
        self.assertEqual(row["notes"], "")

    def test_bad_submissions_are_refused_with_a_reason(self):
        cases = {
            "no topic": sheet_payload(**{"sheet-topic": ""}),
            "bad json": sheet_payload(**{"sheet-sheet": "{not json"}),
            "no rows": sheet_payload(rows=[]),
            "too many": sheet_payload(rows=[{"name": f"C{i}"} for i in range(501)]),
        }
        self.client.force_login(self.user)
        for label, data in cases.items():
            with self.subTest(label):
                response = self.client.post("/portal/research/sheet/", data, follow=True)
                self.assertContains(response, "Your sheet was not submitted")
        self.assertFalse(ResearchSubmission.objects.exists())

    def test_only_a_post_from_the_student_counts(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/portal/research/sheet/").status_code, 405)
        self.client.force_login(self.tutor)
        self.client.post("/portal/research/sheet/", sheet_payload())
        self.assertFalse(ResearchSubmission.objects.exists())

    def test_an_enrolled_student_who_submits_moves_through_to_approved(self):
        # Enrolled without a research start date: there is no direct step to
        # Submitted, so the submission must not leave them stuck at Enrolled.
        self.student.status = StudentStatus.ENROLLED
        self.student.save()
        self.submit()
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.ASSIGNMENT_SUBMITTED)
        steps = list(self.student.status_history.order_by("created_at").values_list("to_status", flat=True))
        self.assertEqual(steps[-2:], [StudentStatus.RESEARCH_IN_PROGRESS, StudentStatus.ASSIGNMENT_SUBMITTED])

        submission = ResearchSubmission.objects.get(student=self.student)
        review_research(self.admin, submission, decision=SubmissionStatus.APPROVED, scores={})
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.RESEARCH_APPROVED)
