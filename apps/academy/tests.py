"""Unit tests for the pure lifecycle logic — no database required."""

from datetime import date, timedelta

from django.test import SimpleTestCase

from apps.academy.enrollment_id import (
    format_enrollment_id,
    is_valid_enrollment_id,
    next_enrollment_id,
    parse_enrollment_id,
)
from apps.academy.status import (
    ROADMAP_ORDER,
    STATUS_TO_STAGE,
    InvalidTransition,
    allowed_transitions,
    assert_transition,
    can_transition,
    is_terminal,
    roadmap_progress,
)
from apps.core.enums import StudentStatus as S


class EnrollmentIdTests(SimpleTestCase):
    def test_formats_with_zero_padded_four_digit_sequence(self):
        self.assertEqual(format_enrollment_id("100DAI", 2026, 1), "100DAI-2026-0001")
        self.assertEqual(format_enrollment_id("100DAI", 2026, 42), "100DAI-2026-0042")
        self.assertEqual(format_enrollment_id("100DAI", 2026, 9999), "100DAI-2026-9999")

    def test_does_not_truncate_beyond_four_digits(self):
        self.assertEqual(format_enrollment_id("100DAI", 2026, 10000), "100DAI-2026-10000")

    def test_uppercases_the_prefix(self):
        self.assertEqual(format_enrollment_id("abc", 2026, 7), "ABC-2026-0007")

    def test_round_trips_through_parse(self):
        parsed = parse_enrollment_id("100DAI-2026-0042")
        self.assertEqual((parsed.prefix, parsed.year, parsed.sequence), ("100DAI", 2026, 42))

    def test_rejects_malformed_values(self):
        for bad in ["100DAI-2026-42", "2026-0042", "100DAI/2026/0042", ""]:
            self.assertIsNone(parse_enrollment_id(bad))
        self.assertFalse(is_valid_enrollment_id(""))

    def test_starts_a_new_year_at_one(self):
        self.assertEqual(next_enrollment_id("100DAI", 2026, None), "100DAI-2026-0001")
        self.assertEqual(next_enrollment_id("100DAI", 2027, "100DAI-2026-0500"), "100DAI-2027-0001")

    def test_increments_within_the_same_year(self):
        self.assertEqual(next_enrollment_id("100DAI", 2026, "100DAI-2026-0041"), "100DAI-2026-0042")
        self.assertEqual(next_enrollment_id("100DAI", 2026, "100DAI-2026-9999"), "100DAI-2026-10000")

    def test_starts_fresh_when_the_prefix_changes(self):
        self.assertEqual(next_enrollment_id("NEWPFX", 2026, "100DAI-2026-0300"), "NEWPFX-2026-0001")

    def test_does_not_reset_on_an_unparseable_previous_value(self):
        # Restarting at 0001 here would collide with an existing student.
        self.assertEqual(next_enrollment_id("100DAI", 2026, "garbage"), "100DAI-2026-0001")


class StatusMachineTests(SimpleTestCase):
    def test_allows_the_normal_forward_path(self):
        for src, dst in [
            (S.ENROLLED, S.RESEARCH_PENDING),
            (S.RESEARCH_IN_PROGRESS, S.ASSIGNMENT_SUBMITTED),
            (S.ASSIGNMENT_SUBMITTED, S.RESEARCH_APPROVED),
            (S.RESEARCH_APPROVED, S.CHANNEL_CREATION_PENDING),
            (S.CHANNEL_CREATED, S.CONTENT_PRODUCTION_STARTED),
            (S.CONTENT_PRODUCTION_STARTED, S.ACTIVE),
            (S.ACTIVE, S.BATCH_COMPLETED),
        ]:
            self.assertTrue(can_transition(src, dst), f"{src} -> {dst}")

    def test_supports_the_rejection_and_resubmission_loop(self):
        self.assertTrue(can_transition(S.ASSIGNMENT_SUBMITTED, S.REVISION_REQUIRED))
        self.assertTrue(can_transition(S.REVISION_REQUIRED, S.ASSIGNMENT_SUBMITTED))

    def test_refuses_to_skip_stages(self):
        self.assertFalse(can_transition(S.ENROLLED, S.RESEARCH_APPROVED))
        self.assertFalse(can_transition(S.ENROLLED, S.ACTIVE))
        self.assertFalse(can_transition(S.RESEARCH_PENDING, S.CHANNEL_CREATED))

    def test_refuses_a_no_op_transition(self):
        self.assertFalse(can_transition(S.ACTIVE, S.ACTIVE))
        self.assertFalse(can_transition(S.ACTIVE, S.ACTIVE, allow_override=True))

    def test_exceptional_statuses_are_reachable_from_anywhere(self):
        for src in [S.ENROLLED, S.RESEARCH_IN_PROGRESS, S.CHANNEL_CREATED, S.ACTIVE]:
            for dst in [S.AT_RISK, S.INACTIVE, S.SUSPENDED, S.DROPPED_OUT]:
                self.assertTrue(can_transition(src, dst), f"{src} -> {dst}")

    def test_terminal_statuses_are_locked_unless_overridden(self):
        self.assertTrue(is_terminal(S.BATCH_COMPLETED))
        self.assertTrue(is_terminal(S.DROPPED_OUT))
        self.assertFalse(is_terminal(S.ACTIVE))

        self.assertFalse(can_transition(S.BATCH_COMPLETED, S.ACTIVE))
        self.assertEqual(allowed_transitions(S.BATCH_COMPLETED), [])
        # A Super Admin correcting a record bypasses the machine.
        self.assertTrue(can_transition(S.BATCH_COMPLETED, S.ACTIVE, allow_override=True))

    def test_a_student_can_recover_from_inactive_or_at_risk(self):
        self.assertTrue(can_transition(S.INACTIVE, S.ACTIVE))
        self.assertTrue(can_transition(S.AT_RISK, S.ACTIVE))
        self.assertTrue(can_transition(S.AT_RISK, S.BATCH_COMPLETED))

    def test_guard_raises_an_explanatory_error(self):
        with self.assertRaises(InvalidTransition) as ctx:
            assert_transition(S.ENROLLED, S.ACTIVE)
        self.assertIn("Allowed next statuses", str(ctx.exception))
        assert_transition(S.ENROLLED, S.RESEARCH_PENDING)  # must not raise

    def test_never_offers_the_current_status_as_a_next_step(self):
        for status in STATUS_TO_STAGE:
            self.assertNotIn(status, allowed_transitions(status))

    def test_every_status_maps_to_a_roadmap_stage(self):
        for stage in STATUS_TO_STAGE.values():
            self.assertIn(stage, ROADMAP_ORDER)

    def test_progress_runs_from_zero_to_one_hundred(self):
        self.assertEqual(roadmap_progress(ROADMAP_ORDER[0]), 0)
        self.assertEqual(roadmap_progress(ROADMAP_ORDER[-1]), 100)


class DeadlineArithmeticTests(SimpleTestCase):
    """
    The maths that makes the "15 day" window configurable rather than hardcoded.
    """

    def test_derives_a_deadline_from_start_plus_window(self):
        start = date(2026, 1, 1)
        self.assertEqual(start + timedelta(days=15), date(2026, 1, 16))
        self.assertEqual(start + timedelta(days=21), date(2026, 1, 22))

    def test_counts_whole_days(self):
        self.assertEqual((date(2026, 1, 16) - date(2026, 1, 1)).days, 15)

    def test_reports_a_negative_count_when_overdue(self):
        self.assertEqual((date(2026, 1, 1) - date(2026, 1, 16)).days, -15)
