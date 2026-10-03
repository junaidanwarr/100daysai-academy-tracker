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
