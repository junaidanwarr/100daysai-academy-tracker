"""LMS adapter tests — CSV parsing, status normalisation, webhook signatures."""

import hashlib
import hmac
import json

from django.test import SimpleTestCase, override_settings

from apps.assignments.lms import normalise_status, parse_csv, webhook_adapter
from apps.core.enums import SubmissionStatus

HEADER = (
    "lms_student_id,student_email,assignment_id,assignment_title,"
    "submitted_at,attempt,status,score,feedback,file_url"
)


class StatusNormalisationTests(SimpleTestCase):
    def test_maps_the_many_words_platforms_use_for_the_same_state(self):
        self.assertEqual(normalise_status("turned in"), SubmissionStatus.SUBMITTED)
        self.assertEqual(normalise_status("Returned"), SubmissionStatus.REVISION_REQUESTED)
        self.assertEqual(normalise_status("PASSED"), SubmissionStatus.APPROVED)
        self.assertEqual(normalise_status("late"), SubmissionStatus.OVERDUE)
        self.assertEqual(normalise_status("needs-revision"), SubmissionStatus.REVISION_REQUESTED)

    def test_treats_an_unknown_status_as_submitted_rather_than_dropping_the_row(self):
        self.assertEqual(normalise_status("something-odd"), SubmissionStatus.SUBMITTED)
        self.assertEqual(normalise_status(None), SubmissionStatus.SUBMITTED)


class CsvParsingTests(SimpleTestCase):
    def test_parses_a_well_formed_export(self):
        rows, errors = parse_csv(
            f"{HEADER}\nLMS-1000,a@b.com,A1,Research,2026-08-01,1,submitted,80,Good,https://x/y.pdf"
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, SubmissionStatus.SUBMITTED)
        self.assertEqual(rows[0].score, 80)
        self.assertEqual(len(rows[0].files), 1)

    def test_builds_a_deterministic_id_so_reimport_cannot_duplicate_an_attempt(self):
        row = f"{HEADER}\nLMS-1000,a@b.com,A1,Research,2026-08-01,2,approved,90,,"
        first = parse_csv(row)[0][0]
        second = parse_csv(row)[0][0]
        self.assertEqual(first.external_id, second.external_id)
        self.assertEqual(first.external_id, "A1:LMS-1000:2")

    def test_reports_a_row_with_no_way_to_match_a_student_instead_of_guessing(self):
        rows, errors = parse_csv(f"{HEADER}\n,,A1,Research,2026-08-01,1,submitted,,,")
        self.assertEqual(rows, [])
        self.assertIn("lms_student_id or student_email", errors[0])

    def test_rejects_an_unparseable_date_rather_than_storing_a_wrong_one(self):
        rows, errors = parse_csv(f"{HEADER}\nLMS-1,a@b.com,A1,R,not-a-date,1,submitted,,,")
        self.assertEqual(rows, [])
        self.assertIn("not a date", errors[0])

    def test_keeps_good_rows_when_one_row_is_bad(self):
        rows, errors = parse_csv(
            f"{HEADER}\nLMS-1,a@b.com,A1,R,2026-08-01,1,submitted,,,\n,,A2,R,2026-08-01,1,submitted,,,"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 1)

    def test_defaults_a_missing_attempt_number_to_one(self):
        rows, _ = parse_csv(f"{HEADER}\nLMS-1,a@b.com,A1,R,2026-08-01,,submitted,,,")
        self.assertEqual(rows[0].attempt, 1)

    def test_reports_an_empty_file_rather_than_raising(self):
        rows, errors = parse_csv("")
        self.assertEqual(rows, [])
        self.assertTrue(errors)


@override_settings(LMS_WEBHOOK_SECRET="test-secret")
class WebhookSignatureTests(SimpleTestCase):
    body = b'{"submissions":[{"submission_id":"WH-1","assignment_id":"A1","student_id":"LMS-1"}]}'

    def signature(self, secret=b"test-secret") -> str:
        return hmac.new(secret, self.body, hashlib.sha256).hexdigest()

    def test_accepts_a_valid_signature(self):
        self.assertTrue(webhook_adapter.verify(self.body, self.signature()))
        self.assertTrue(webhook_adapter.verify(self.body, f"sha256={self.signature()}"))

    def test_rejects_a_missing_or_wrong_signature(self):
        self.assertFalse(webhook_adapter.verify(self.body, None))
        self.assertFalse(webhook_adapter.verify(self.body, "deadbeef"))
        self.assertFalse(webhook_adapter.verify(self.body, self.signature(b"other-secret")))

    def test_rejects_a_tampered_body(self):
        signature = self.signature()
        self.assertFalse(webhook_adapter.verify(self.body + b" ", signature))

    @override_settings(LMS_WEBHOOK_SECRET="")
    def test_refuses_everything_when_no_secret_is_configured(self):
        # An unauthenticated endpoint that writes student records is worse than
        # one that does not work yet.
        self.assertFalse(webhook_adapter.verify(self.body, self.signature()))
        self.assertFalse(webhook_adapter.is_configured())

    def test_parses_wrapped_and_bare_payloads(self):
        wrapped = webhook_adapter.parse(self.body.decode())
        self.assertEqual(len(wrapped), 1)
        self.assertEqual(wrapped[0].external_id, "WH-1")

        bare = webhook_adapter.parse(json.dumps({"submission_id": "WH-2", "assignment_id": "A1"}))
        self.assertEqual(len(bare), 1)
        self.assertEqual(bare[0].external_id, "WH-2")

    def test_drops_entries_missing_the_identifiers_needed_to_match_anything(self):
        self.assertEqual(webhook_adapter.parse(json.dumps([{"foo": "bar"}])), [])
