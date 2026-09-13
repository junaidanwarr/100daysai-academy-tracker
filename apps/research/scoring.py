"""
Weighted research scoring.

Pure functions with no database access, so the rules are unit-testable and
identical wherever they run — the evaluation form previews with exactly the
figure the service will store.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoredCriterion:
    criterion_id: str
    name: str
    weightage: float
    max_score: float
    min_passing_score: float
    is_required: bool
    score: float


@dataclass
class CriterionBreakdown:
    criterion_id: str
    name: str
    score: float
    max_score: float
    percent: float
    weightage: float
    weighted_points: float
    passed: bool


@dataclass
class ScoreResult:
    total: float = 0.0
    passed: bool = False
    # Required criteria scored below their pass mark.
    failed_required: list[str] = field(default_factory=list)
    breakdown: list[CriterionBreakdown] = field(default_factory=list)


def _round(value: float) -> float:
    return round(value + 0.0, 2)


def score_submission(criteria: list[ScoredCriterion]) -> ScoreResult:
    """
    Weights are normalised across whatever criteria were actually scored, so
    disabling a criterion re-spreads its weight instead of silently capping the
    achievable total below 100.
    """
    if not criteria:
        return ScoreResult()

    total_weight = sum(c.weightage for c in criteria)
    failed_required: list[str] = []
    breakdown: list[CriterionBreakdown] = []

    for c in criteria:
        maximum = c.max_score if c.max_score > 0 else 100.0
        clamped = max(0.0, min(float(c.score), maximum))
        percent = (clamped / maximum) * 100
        share = (c.weightage / total_weight) if total_weight > 0 else 0.0
        passed = clamped >= c.min_passing_score

        if c.is_required and not passed:
            failed_required.append(c.name)

        breakdown.append(
            CriterionBreakdown(
                criterion_id=c.criterion_id,
                name=c.name,
                score=clamped,
                max_score=maximum,
                percent=_round(percent),
                weightage=c.weightage,
                weighted_points=_round(percent * share),
                passed=passed,
            )
        )

    total = _round(sum(b.weighted_points for b in breakdown))

    return ScoreResult(
        total=total,
        # A required criterion below its pass mark fails the whole submission,
        # however high the weighted total. Otherwise a strong score elsewhere
        # could mask a missing mandatory piece of research.
        passed=not failed_required and total >= 50,
        failed_required=failed_required,
        breakdown=breakdown,
    )


def explain_score(result: ScoreResult) -> str:
    """Human summary of why a submission scored what it did."""
    if not result.breakdown:
        return "No criteria were scored."

    parts = [f"Weighted total {result.total}%."]

    if result.failed_required:
        parts.append(f"Required criteria below their pass mark: {', '.join(result.failed_required)}.")

    weakest = min(result.breakdown, key=lambda b: b.percent)
    strongest = max(result.breakdown, key=lambda b: b.percent)
    if weakest.criterion_id != strongest.criterion_id:
        parts.append(
            f"Strongest: {strongest.name} ({strongest.percent}%). "
            f"Weakest: {weakest.name} ({weakest.percent}%)."
        )

    return " ".join(parts)


def breakdown_as_json(result: ScoreResult) -> list[dict]:
    """Serialisable form, for storing alongside an evaluation."""
    return [
        {
            "criterion_id": b.criterion_id,
            "name": b.name,
            "score": b.score,
            "max_score": b.max_score,
            "percent": b.percent,
            "weightage": b.weightage,
            "weighted_points": b.weighted_points,
            "passed": b.passed,
        }
        for b in result.breakdown
    ]
