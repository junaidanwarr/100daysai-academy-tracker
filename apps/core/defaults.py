"""
The configuration every deployment needs, with no people in it.

Alert rules, research criteria, scoring factors and the tunable settings. Without
these a fresh production database raises no alerts at all and has nothing to
score research against — and ``seed_demo``, the only thing that used to create
them, must never run in production because it also invents twenty students.

``install_defaults`` only ever adds what is missing. It never overwrites a value,
a threshold or an on/off switch an administrator has changed, so it is safe to
run on every deploy.
"""

from __future__ import annotations

from apps.core.models import SystemSetting
from apps.monitoring.models import AlertRule, ScoringFactor
from apps.research.models import ResearchCriteriaSet, ResearchCriterion

SETTINGS = [
    ("research_days", 15, "Research window (days)", "int", "research", "Days from research start to the deadline. A batch may override this."),
    ("research_warning_days", 3, "Deadline warning (days)", "int", "research", "How far ahead of the deadline to warn."),
    ("max_rejections_before_alert", 2, "Rejections before alert", "int", "research", "Raise an alert once research has been rejected more than this many times."),
    ("inactivity_days", 7, "Inactivity threshold (days)", "int", "monitoring", "Days without recorded activity before a student counts as inactive."),
    ("channel_creation_days", 20, "Channel creation target (days)", "int", "monitoring", "Days after research approval by which a channel should exist."),
    ("first_video_days", 3, "First video target (days)", "int", "monitoring", "Days after channel creation by which the first video should be published."),
    ("no_upload_days", 10, "Upload gap limit (days)", "int", "monitoring", "Days without an upload before the channel is flagged."),
    ("min_videos_per_week", 3, "Minimum videos per week", "int", "monitoring", "Baseline upload target used by performance scoring."),
    ("youtube_sync_cadence", "DAILY", "YouTube sync cadence", "string", "integrations", "EVERY_6_HOURS, EVERY_12_HOURS, DAILY, WEEKLY or MANUAL."),
    ("competitor_min_videos", 20, "Competitor min videos", "int", "research", "Fewest uploads a reference channel may have and still prove the niche."),
    ("competitor_max_videos", 30, "Competitor max videos", "int", "research", "Most uploads a reference channel may have. Beyond this it is an established channel, not a fresh proof."),
    ("competitor_min_subs_view_ratio", "0.5", "Competitor min subs/views %", "string", "research", "Subscribers as a minimum percentage of views. 0.5 means 1,000 views should have produced 5 subscribers."),
    ("competitor_min_age_days", 60, "Competitor min age (days)", "int", "research", "Oldest video must be at least this old."),
    ("competitor_max_age_days", 90, "Competitor max age (days)", "int", "research", "Oldest video must be no older than this. The point is to prove the niche works now."),
    ("competitor_spike_multiple", "5", "Growth spike multiple", "string", "research", "A video pulling this multiple of the typical video's views counts as a spike, not growth."),
    ("competitor_max_top_video_share", "50", "Max single-video share %", "string", "research", "If one video is more than this share of all views, the channel is one hit rather than a working format."),
    ("competitor_min_recent_share", "50", "Min recent performance %", "string", "research", "Recent uploads averaging below this share of earlier ones counts as decline."),
    ("enrollment_id_prefix", "100DAI", "Enrollment ID prefix", "string", "general", "Prefix for generated Enrollment IDs. Changing this does not renumber existing students."),
    ("academy_name", "100DaysAI Academy", "Academy name", "string", "general", "Shown on documents and notifications."),
]

ALERT_RULES = [
    ("research_deadline_missed", "Research deadline missed", "RESEARCH_DEADLINE_MISSED", {"grace_days": 0}, "HIGH", ["IN_APP", "EMAIL"], 24, "Contact the student and agree a revised submission date."),
    ("research_deadline_approaching", "Research deadline approaching", "RESEARCH_DEADLINE_APPROACHING", {}, "MEDIUM", ["IN_APP"], 48, "Send a reminder and check whether the student is blocked."),
    ("assignment_not_submitted", "Assignment not submitted", "ASSIGNMENT_NOT_SUBMITTED", {}, "HIGH", ["IN_APP", "EMAIL"], 24, "Confirm the student has LMS access and understands the task."),
    ("rejected_repeatedly", "Research rejected repeatedly", "ASSIGNMENT_REJECTED_REPEATEDLY", {}, "HIGH", ["IN_APP", "EMAIL"], 72, "Book a one-to-one review before the next resubmission."),
    ("approved_no_channel", "Research approved but no channel", "APPROVED_BUT_NO_CHANNEL", {}, "MEDIUM", ["IN_APP"], 48, "Walk the student through channel creation."),
    ("channel_no_uploads", "Channel created but no videos", "CHANNEL_WITHOUT_UPLOADS", {}, "MEDIUM", ["IN_APP"], 48, "Check whether production has started and what is blocking it."),
    ("upload_target_missed", "Upload target missed", "UPLOAD_TARGET_MISSED", {}, "MEDIUM", ["IN_APP"], 72, "Review the upload schedule with the student."),
    ("student_inactive", "Student inactive", "STUDENT_INACTIVE", {}, "MEDIUM", ["IN_APP", "EMAIL"], 72, "Reach out directly and record the response."),
    ("analytics_disconnected", "YouTube Analytics disconnected", "ANALYTICS_DISCONNECTED", {}, "LOW", ["IN_APP"], 168, "Ask the student to reconnect their Google account."),
    ("agreement_unsigned", "Final agreement not signed", "AGREEMENT_UNSIGNED", {"after_days": 7}, "MEDIUM", ["IN_APP", "EMAIL"], 168, "Remind the student to review and sign their completion record."),
    ("assignment_overdue", "Assignment overdue", "ASSIGNMENT_OVERDUE", {"grace_days": 0}, "HIGH", ["IN_APP", "EMAIL"], 24, "Check the student has seen the assignment and agree when it will be handed in."),
]

SCORING_FACTORS = [
    ("research_on_time", "Research submitted on time", 10, False),
    ("research_quality", "Research quality score", 12, False),
    ("attempt_count", "Number of attempts", 6, False),
    ("instructor_evaluation", "Instructor evaluation", 12, False),
    ("channel_on_time", "Channel created on time", 8, False),
    ("videos_uploaded", "Videos uploaded", 10, False),
    ("upload_consistency", "Upload consistency", 10, False),
    ("views", "Views", 6, False),
    ("subscriber_growth", "Subscriber growth", 6, False),
    ("ctr", "Click-through rate", 5, True),
    ("avg_view_duration", "Average view duration", 5, True),
    ("niche_compliance", "Compliance with approved niche", 5, False),
    ("responsiveness", "Responsiveness to feedback", 5, False),
]

CRITERIA = [
    ("Niche clarity", 20, 60, True, False, "The niche and sub-niche are specific enough to build a channel around."),
    ("Competitor analysis", 20, 60, True, True, "At least five comparable channels analysed with links and observations."),
    ("Audience definition", 15, 50, True, False, "Target audience, country and language are clearly identified."),
    ("Keyword research", 15, 50, True, True, "Search demand evidenced with terms, volume and difficulty."),
    ("Content gap analysis", 15, 50, False, False, "A defensible gap the student can occupy."),
    ("Monetization potential", 15, 50, True, False, "Realistic assessment of revenue potential for the niche."),
]



def install_defaults() -> dict[str, int]:
    """Creates whatever is missing. Returns how many of each were added."""
    added = {"settings": 0, "alert_rules": 0, "scoring_factors": 0, "criteria": 0}

    for key, value, label, value_type, category, description in SETTINGS:
        _, created = SystemSetting.objects.get_or_create(
            key=key,
            defaults={"value": value, "label": label, "value_type": value_type,
                      "category": category, "description": description},
        )
        added["settings"] += created

    for key, name, evaluator, params, priority, channels, cooldown, action in ALERT_RULES:
        _, created = AlertRule.objects.get_or_create(
            key=key,
            defaults={"name": name, "condition": {"evaluator": evaluator, "params": params},
                      "priority": priority, "channels": channels, "cooldown_hours": cooldown,
                      "recommended_action": action, "is_active": True},
        )
        added["alert_rules"] += created

    for order, (key, label, weight, needs_analytics) in enumerate(SCORING_FACTORS):
        _, created = ScoringFactor.objects.get_or_create(
            key=key,
            defaults={"label": label, "weightage": weight,
                      "requires_analytics": needs_analytics, "display_order": order},
        )
        added["scoring_factors"] += created

    # Only when no default set exists at all: an academy that has built its own
    # rubric must not have a second one appear beside it.
    if not ResearchCriteriaSet.objects.filter(is_default=True).exists():
        criteria_set, _ = ResearchCriteriaSet.objects.get_or_create(
            name="Standard YouTube Research", version=1,
            defaults={"description": "Default criteria for niche and channel research evaluation.",
                      "is_active": True, "is_default": True},
        )
        if not criteria_set.criteria.exists():
            for order, (name, weight, pass_mark, required, evidence, description) in enumerate(CRITERIA):
                ResearchCriterion.objects.create(
                    criteria_set=criteria_set, name=name, weightage=weight,
                    min_passing_score=pass_mark, is_required=required,
                    evidence_required=evidence, description=description, display_order=order,
                )
                added["criteria"] += 1

    return added
