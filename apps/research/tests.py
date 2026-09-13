"""Weighted scoring tests — pure logic, no database required."""

from django.test import SimpleTestCase

from apps.research.scoring import ScoredCriterion, explain_score, score_submission


def criterion(**over) -> ScoredCriterion:
    defaults = {
        "criterion_id": "c1",
        "name": "Criterion",
        "weightage": 10.0,
        "max_score": 100.0,
        "min_passing_score": 50.0,
        "is_required": True,
        "score": 0.0,
    }
    return ScoredCriterion(**{**defaults, **over})


class ScoringTests(SimpleTestCase):
    def test_returns_zero_for_no_criteria_rather_than_dividing_by_zero(self):
        result = score_submission([])
        self.assertEqual(result.total, 0)
        self.assertFalse(result.passed)
        self.assertEqual(result.breakdown, [])

    def test_scores_a_perfect_submission_as_one_hundred(self):
        result = score_submission([
            criterion(criterion_id="a", weightage=30, score=100),
            criterion(criterion_id="b", weightage=70, score=100),
        ])
        self.assertEqual(result.total, 100)
        self.assertTrue(result.passed)

    def test_weights_criteria_proportionally(self):
        # 100% on a weight-75 criterion, 0% on a weight-25 one -> 75.
        result = score_submission([
            criterion(criterion_id="a", weightage=75, score=100, is_required=False),
            criterion(criterion_id="b", weightage=25, score=0, is_required=False),
        ])
        self.assertEqual(result.total, 75)

    def test_normalises_weights_so_a_partial_set_can_still_reach_one_hundred(self):
        # Two criteria weighted 10 and 10 out of a nominal 100 still yield 100
        # when both are perfect — weights are relative, not absolute.
        result = score_submission([
            criterion(criterion_id="a", weightage=10, score=100),
            criterion(criterion_id="b", weightage=10, score=100),
        ])
        self.assertEqual(result.total, 100)

    def test_respects_a_non_hundred_max_score(self):
        result = score_submission([criterion(max_score=5, min_passing_score=3, score=4)])
        self.assertEqual(result.total, 80)

    def test_clamps_a_score_above_the_maximum(self):
        result = score_submission([criterion(max_score=100, score=250)])
        self.assertEqual(result.total, 100)
        self.assertEqual(result.breakdown[0].score, 100)

    def test_clamps_a_negative_score_to_zero(self):
        self.assertEqual(score_submission([criterion(score=-50)]).total, 0)

    def test_a_failed_required_criterion_fails_the_whole_submission(self):
        # High total, but a mandatory piece of research is missing.
        result = score_submission([
            criterion(criterion_id="a", weightage=90, score=100, is_required=False),
            criterion(criterion_id="b", weightage=10, score=10, is_required=True,
                      min_passing_score=50, name="Competitors"),
        ])
        self.assertGreater(result.total, 50)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_required, ["Competitors"])

    def test_an_optional_criterion_below_its_pass_mark_does_not_fail_the_submission(self):
        result = score_submission([
            criterion(criterion_id="a", weightage=50, score=100),
            criterion(criterion_id="b", weightage=50, score=10, is_required=False),
        ])
        self.assertEqual(result.failed_required, [])
        self.assertTrue(result.passed)

    def test_explains_the_score_in_words_an_evaluator_can_paste_to_a_student(self):
        text = explain_score(score_submission([
            criterion(criterion_id="a", name="Niche clarity", weightage=50, score=90),
            criterion(criterion_id="b", name="Keywords", weightage=50, score=20, min_passing_score=50),
        ]))
        self.assertIn("Weighted total", text)
        self.assertIn("Keywords", text)
