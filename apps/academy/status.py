"""
Student lifecycle rules (specification sections 1 and 4).

Pure functions with no database access, so the state machine is unit-testable
and the same rules apply whether a transition is made by a person or by a
background job.
"""

from __future__ import annotations

from apps.core.enums import RoadmapStage, StudentStatus

S = StudentStatus
R = RoadmapStage

# Allowed forward transitions along the roadmap. Terminal and exceptional
# statuses are handled separately rather than duplicated into every entry.
PROGRESSION: dict[str, list[str]] = {
    S.ENROLLED: [S.RESEARCH_PENDING, S.RESEARCH_IN_PROGRESS],
    S.RESEARCH_PENDING: [S.RESEARCH_IN_PROGRESS, S.ASSIGNMENT_SUBMITTED],
    S.RESEARCH_IN_PROGRESS: [S.ASSIGNMENT_SUBMITTED],
    S.ASSIGNMENT_SUBMITTED: [S.REVISION_REQUIRED, S.RESEARCH_APPROVED],
    S.REVISION_REQUIRED: [S.ASSIGNMENT_SUBMITTED],
    S.RESEARCH_APPROVED: [S.CHANNEL_CREATION_PENDING, S.CHANNEL_CREATED],
    S.CHANNEL_CREATION_PENDING: [S.CHANNEL_CREATED],
    S.CHANNEL_CREATED: [S.CONTENT_PRODUCTION_STARTED],
    S.CONTENT_PRODUCTION_STARTED: [S.ACTIVE],
    S.ACTIVE: [S.BATCH_COMPLETED],
    S.INACTIVE: [S.ACTIVE, S.CONTENT_PRODUCTION_STARTED],
    S.AT_RISK: [S.ACTIVE, S.CONTENT_PRODUCTION_STARTED, S.BATCH_COMPLETED],
    S.BATCH_COMPLETED: [],
    S.DROPPED_OUT: [],
    S.SUSPENDED: [],
}

# Reachable from almost anywhere, because real students go quiet, get flagged,
# quit or get suspended at any point in the journey.
EXCEPTIONAL = [S.INACTIVE, S.AT_RISK, S.DROPPED_OUT, S.SUSPENDED]

# Once here, only a Super Admin override moves the student again.
TERMINAL = [S.BATCH_COMPLETED, S.DROPPED_OUT]

STATUS_TO_STAGE: dict[str, str] = {
    S.ENROLLED: R.ENROLLMENT,
    S.RESEARCH_PENDING: R.NICHE_RESEARCH,
    S.RESEARCH_IN_PROGRESS: R.NICHE_RESEARCH,
    S.ASSIGNMENT_SUBMITTED: R.ASSIGNMENT_REVIEW,
    S.REVISION_REQUIRED: R.ASSIGNMENT_SUBMISSION,
    S.RESEARCH_APPROVED: R.RESEARCH_APPROVED,
    S.CHANNEL_CREATION_PENDING: R.CHANNEL_CREATION,
    S.CHANNEL_CREATED: R.CHANNEL_CREATION,
    S.CONTENT_PRODUCTION_STARTED: R.CONTENT_PRODUCTION,
    S.ACTIVE: R.PERFORMANCE_MONITORING,
    S.INACTIVE: R.PERFORMANCE_MONITORING,
    S.AT_RISK: R.PERFORMANCE_MONITORING,
    S.BATCH_COMPLETED: R.BATCH_COMPLETION,
    S.DROPPED_OUT: R.BATCH_COMPLETION,
    S.SUSPENDED: R.BATCH_COMPLETION,
}

ROADMAP_ORDER = [
    R.ENROLLMENT,
    R.NICHE_RESEARCH,
    R.ASSIGNMENT_SUBMISSION,
    R.ASSIGNMENT_REVIEW,
    R.RESEARCH_APPROVED,
    R.CHANNEL_CREATION,
    R.CONTENT_PRODUCTION,
    R.PERFORMANCE_MONITORING,
    R.BATCH_COMPLETION,
]

# Statuses that mean the student is still actively being tracked.
ARCHIVED_STATUSES = [S.BATCH_COMPLETED, S.DROPPED_OUT, S.SUSPENDED]

# Statuses where research is still outstanding.
RESEARCH_PENDING_STATUSES = [
    S.ENROLLED,
    S.RESEARCH_PENDING,
    S.RESEARCH_IN_PROGRESS,
    S.REVISION_REQUIRED,
]


class InvalidTransition(Exception):
    status_code = 422

    def __init__(self, from_status: str, to_status: str):
        allowed = ", ".join(allowed_transitions(from_status)) or "none"
        super().__init__(
            f"Cannot move a student from {from_status} to {to_status}. "
            f"Allowed next statuses: {allowed}."
        )


def is_terminal(status: str) -> bool:
    return status in TERMINAL


def can_transition(from_status: str, to_status: str, allow_override: bool = False) -> bool:
    """
    `allow_override` corresponds to a Super Admin correcting a record
    (specification section 3), which is always audited.
    """
    if from_status == to_status:
        return False
    if allow_override:
        return True
    if is_terminal(from_status):
        return False
    if to_status in EXCEPTIONAL:
        return True
    return to_status in PROGRESSION.get(from_status, [])


def allowed_transitions(from_status: str) -> list[str]:
    if is_terminal(from_status):
        return []
    seen: list[str] = []
    for status in [*PROGRESSION.get(from_status, []), *EXCEPTIONAL]:
        if status != from_status and status not in seen:
            seen.append(status)
    return seen


def assert_transition(from_status: str, to_status: str, allow_override: bool = False) -> None:
    if not can_transition(from_status, to_status, allow_override):
        raise InvalidTransition(from_status, to_status)


def roadmap_progress(stage: str) -> int:
    """Percentage of the roadmap completed, for progress bars."""
    if stage not in ROADMAP_ORDER:
        return 0
    return round(ROADMAP_ORDER.index(stage) / (len(ROADMAP_ORDER) - 1) * 100)


# Grouped by meaning — green healthy, amber waiting, red problem — so a list of
# students is scannable without reading every label.
STATUS_TONES: dict[str, str] = {
    S.ENROLLED: "slate",
    S.RESEARCH_PENDING: "amber",
    S.RESEARCH_IN_PROGRESS: "blue",
    S.ASSIGNMENT_SUBMITTED: "indigo",
    S.REVISION_REQUIRED: "orange",
    S.RESEARCH_APPROVED: "emerald",
    S.CHANNEL_CREATION_PENDING: "amber",
    S.CHANNEL_CREATED: "teal",
    S.CONTENT_PRODUCTION_STARTED: "cyan",
    S.ACTIVE: "emerald",
    S.INACTIVE: "slate",
    S.AT_RISK: "red",
    S.BATCH_COMPLETED: "violet",
    S.DROPPED_OUT: "slate",
    S.SUSPENDED: "red",
}
