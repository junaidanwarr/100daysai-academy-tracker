"""
The four niche-validation rules — pure arithmetic, no database required.

These matter more than the subjective criteria tests: a wrong threshold here
either waves through a niche that cannot be reproduced, or blocks a student
whose research was sound.
"""

from datetime import date, timedelta

from django.test import SimpleTestCase

from apps.research.competitor_rules import (
    check_channel_age,
    check_growth_consistency,
    check_subscriber_ratio,
    check_video_count,
    evaluate_channel,
)

TODAY = date(2026, 8, 4)


class VideoCountTests(SimpleTestCase):
    def test_passes_inside_the_band(self):
        for count in (20, 25, 30):
            self.assertTrue(check_video_count(count).passed, count)

    def test_fails_just_below_and_just_above(self):
        self.assertTrue(check_video_count(19).failed)
        self.assertTrue(check_video_count(31).failed)

    def test_says_which_side_it_failed_on(self):
        self.assertIn("below 20", check_video_count(12).detail)
        self.assertIn("above 30", check_video_count(80).detail)

    def test_missing_data_is_unknown_not_a_pass(self):
        # A rule with nothing to check must never report success.
        result = check_video_count(None)
        self.assertTrue(result.unknown)
        self.assertFalse(result.passed)

    def test_respects_a_retuned_band(self):
        self.assertTrue(check_video_count(40, {"min_videos": 35, "max_videos": 50}).passed)


class SubscriberRatioTests(SimpleTestCase):
    def test_the_stated_example_passes_exactly_at_the_line(self):
        # 1,000 views and 5 subscribers is exactly 0.5%.
        self.assertTrue(check_subscriber_ratio(5, 1000).passed)
        self.assertTrue(check_subscriber_ratio(50, 10000).passed)

    def test_one_subscriber_short_fails(self):
        self.assertTrue(check_subscriber_ratio(4, 1000).failed)

    def test_a_strong_ratio_passes(self):
        self.assertTrue(check_subscriber_ratio(4200, 720000).passed)

    def test_the_failure_says_what_was_expected(self):
        detail = check_subscriber_ratio(900, 610000).detail
        self.assertIn("below 0.5%", detail)
        self.assertIn("3,050", detail)  # 0.5% of 610,000

    def test_zero_views_is_unknown_rather_than_a_division_error(self):
        self.assertTrue(check_subscriber_ratio(10, 0).unknown)

    def test_missing_either_figure_is_unknown(self):
        self.assertTrue(check_subscriber_ratio(None, 1000).unknown)
        self.assertTrue(check_subscriber_ratio(10, None).unknown)

    def test_a_measured_zero_subscribers_fails_rather_than_going_unknown(self):
        # Zero subscribers is a measurement, not missing data.
        result = check_subscriber_ratio(0, 50000)
        self.assertTrue(result.failed)


class ChannelAgeTests(SimpleTestCase):
    def age(self, days):
        return check_channel_age(TODAY - timedelta(days=days), today=TODAY)

    def test_passes_between_two_and_three_months(self):
        for days in (60, 75, 90):
            self.assertTrue(self.age(days).passed, days)

    def test_too_young_fails(self):
        result = self.age(31)
        self.assertTrue(result.failed)
        self.assertIn("younger than 60", result.detail)

    def test_too_old_fails_because_it_proves_nothing_about_now(self):
        result = self.age(400)
        self.assertTrue(result.failed)
        self.assertIn("does not prove the niche works now", result.detail)

    def test_missing_date_is_unknown(self):
        self.assertTrue(check_channel_age(None, today=TODAY).unknown)

    def test_a_future_date_is_refused_rather_than_read_as_negative_age(self):
        self.assertTrue(check_channel_age(TODAY + timedelta(days=5), today=TODAY).unknown)


class GrowthConsistencyTests(SimpleTestCase):
    def test_the_stated_good_case_passes(self):
        # 5k, 8k, 10k — steady climb.
        result = check_growth_consistency([5000, 8000, 10000])
        self.assertTrue(result.passed)
        self.assertIn("rising", result.detail)

    def test_the_stated_bad_case_is_a_red_flag(self):
        # 10k, 100k, 5k — one viral video, then collapse.
        result = check_growth_consistency([10000, 100000, 5000])
        self.assertTrue(result.failed)
        self.assertTrue(result.is_red_flag)

    def test_the_bad_case_names_the_spike_and_the_single_hit(self):
        detail = check_growth_consistency([10000, 100000, 5000]).detail.lower()
        self.assertIn("spike", detail)
        self.assertIn("one hit", detail)

    def test_flat_performance_passes(self):
        result = check_growth_consistency([9000, 9200, 8900, 9100, 9050])
        self.assertTrue(result.passed)
        self.assertIn("holding steady", result.detail)

    def test_a_sustained_decline_fails(self):
        result = check_growth_consistency([20000, 19000, 18000, 6000, 5000, 4000])
        self.assertTrue(result.failed)
        self.assertIn("falling away", result.detail)

    def test_fewer_than_three_videos_cannot_show_a_trend(self):
        self.assertTrue(check_growth_consistency([5000, 8000]).unknown)
        self.assertTrue(check_growth_consistency([]).unknown)
        self.assertTrue(check_growth_consistency(None).unknown)

    def test_all_zero_views_is_unknown_rather_than_a_pass(self):
        self.assertTrue(check_growth_consistency([0, 0, 0, 0]).unknown)

    def test_growth_is_always_marked_as_the_red_flag_rule(self):
        # Whether it passes or fails, this is the rule the academy weights most.
        self.assertTrue(check_growth_consistency([5000, 8000, 10000]).is_red_flag)


class WholeChannelTests(SimpleTestCase):
    def clean_channel(self, **over):
        defaults = {
            "channel_name": "Quiet Cash Notes",
            "video_count": 24,
            "subscriber_count": 4200,
            "view_count": 720_000,
            "oldest_video_date": TODAY - timedelta(days=74),
            "views_oldest_first": [4100, 5200, 6800, 7400, 9100, 11200],
            "today": TODAY,
        }
        return evaluate_channel(**{**defaults, **over})

    def test_a_channel_meeting_every_rule_passes(self):
        verdict = self.clean_channel()
        self.assertTrue(verdict.passes_all)
        self.assertEqual(verdict.failed, [])
        self.assertEqual(verdict.summary, "Meets all four niche-validation rules.")

    def test_one_failure_is_enough_to_stop_passes_all(self):
        verdict = self.clean_channel(video_count=58)
        self.assertFalse(verdict.passes_all)
        self.assertEqual(len(verdict.failed), 1)

    def test_the_growth_failure_raises_a_red_flag(self):
        verdict = self.clean_channel(views_oldest_first=[10000, 100000, 5000, 4200, 3800, 3100])
        self.assertTrue(verdict.has_red_flag)

    def test_a_channel_with_no_data_never_reports_as_passing(self):
        verdict = evaluate_channel(channel_name="Unknown", today=TODAY)
        self.assertFalse(verdict.passes_all)
        self.assertEqual(len(verdict.unknown), 4)
        self.assertIn("could not be checked", verdict.summary)

    def test_all_four_rules_are_always_reported(self):
        # Even when unchecked — an evaluator should see the gap, not a short list.
        self.assertEqual(len(evaluate_channel(channel_name="X").results), 4)
