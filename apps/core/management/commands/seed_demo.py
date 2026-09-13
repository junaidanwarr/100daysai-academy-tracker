"""
Seeds a database that exercises the whole Phase 1 surface: students at every
stage, deliberately overdue deadlines so the alert scan has something to find,
and channels in mixed states.

Credentials are printed to the console once and are never rendered in the UI.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.academy.models import Batch, Student, StudentStatusHistory
from apps.academy.status import STATUS_TO_STAGE
from apps.accounts.models import User
from apps.core.crypto import generate_password
from apps.core.enums import (
    ChannelStatus,
    ContentType,
    MonetizationStatus,
    RatingLevel,
    StudentStatus,
    SubmissionStatus,
    UserRole,
)
from apps.core.models import SystemSetting
from apps.monitoring.models import AlertRule, ScoringFactor
from apps.research.models import ResearchCriteriaSet, ResearchCriterion, ResearchSubmission
from apps.youtube.models import YoutubeChannel

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

# (name, city, country, status, research_offset_days, activity_offset_days, channel)
STUDENTS = [
    ("Ayesha Khan", "Lahore", "Pakistan", StudentStatus.ENROLLED, None, -1, None),
    ("Bilal Ahmed", "Karachi", "Pakistan", StudentStatus.RESEARCH_PENDING, -2, -2, None),
    ("Chloe Martin", "Manchester", "United Kingdom", StudentStatus.RESEARCH_IN_PROGRESS, -6, -1, None),
    # Deliberately overdue: research started well beyond the 15-day window.
    ("Daniyal Raza", "Islamabad", "Pakistan", StudentStatus.RESEARCH_IN_PROGRESS, -24, -12, None),
    ("Emma Fischer", "Berlin", "Germany", StudentStatus.RESEARCH_IN_PROGRESS, -19, -9, None),
    ("Farhan Iqbal", "Faisalabad", "Pakistan", StudentStatus.ASSIGNMENT_SUBMITTED, -12, -1, None),
    ("Grace Owusu", "Accra", "Ghana", StudentStatus.REVISION_REQUIRED, -17, -4, None),
    ("Hamza Sheikh", "Multan", "Pakistan", StudentStatus.RESEARCH_APPROVED, -30, -3, None),
    ("Isabella Rossi", "Milan", "Italy", StudentStatus.CHANNEL_CREATION_PENDING, -34, -6, None),
    ("Junaid Malik", "Rawalpindi", "Pakistan", StudentStatus.CHANNEL_CREATED, -40, -2, ("Quiet Money Lab", "Personal finance", ChannelStatus.APPROVED, None)),
    ("Kiran Devi", "Delhi", "India", StudentStatus.CONTENT_PRODUCTION_STARTED, -46, -1, ("Mindful Mornings", "Wellness", ChannelStatus.ACTIVE, -4)),
    ("Liam O'Connor", "Dublin", "Ireland", StudentStatus.ACTIVE, -55, -1, ("Stoic Shorts Daily", "Philosophy", ChannelStatus.ACTIVE, -2)),
    ("Maryam Noor", "Peshawar", "Pakistan", StudentStatus.ACTIVE, -58, -2, ("History In 60", "History", ChannelStatus.MONETIZED, -3)),
    # Upload gap beyond the 10-day limit.
    ("Nadia Hussain", "Sialkot", "Pakistan", StudentStatus.ACTIVE, -62, -14, ("Frugal Kitchen", "Cooking", ChannelStatus.ACTIVE, -21)),
    ("Omar Farooq", "Dubai", "United Arab Emirates", StudentStatus.INACTIVE, -50, -18, None),
    ("Priya Sharma", "Mumbai", "India", StudentStatus.AT_RISK, -44, -21, None),
    ("Qasim Baig", "Quetta", "Pakistan", StudentStatus.ACTIVE, -66, -1, ("Tech Explained Urdu", "Technology", ChannelStatus.ACTIVE, -1)),
    ("Rebecca Stone", "Toronto", "Canada", StudentStatus.BATCH_COMPLETED, -95, -30, ("Budget Travel Files", "Travel", ChannelStatus.COMPLETED, -25)),
    ("Sana Tariq", "Hyderabad", "Pakistan", StudentStatus.DROPPED_OUT, -70, -45, None),
    ("Tariq Mehmood", "Gujranwala", "Pakistan", StudentStatus.ACTIVE, -60, -2, ("Auto Insights", "Automotive", ChannelStatus.ACTIVE, -6)),
]

# Reference channels for the submission awaiting review, chosen so each of the
# four niche-validation rules is demonstrably exercised — one clean pass, and
# one failure of each kind including the growth red flag.
# (name, videos, subs, views, age_days, views per video oldest-first)
DEMO_COMPETITORS = [
    ("Quiet Cash Notes", 24, 4200, 720_000, 74, [4100, 5200, 6800, 7400, 9100, 11200]),
    ("Money Minute Facts", 58, 31000, 4_100_000, 210, [22000, 24000, 26000, 25000, 27000, 29000]),
    ("Silent Wealth Clips", 22, 900, 610_000, 68, [7200, 8100, 9400, 8800, 10100, 11500]),
    ("Finance Shorts Lab", 26, 5100, 880_000, 31, [6000, 7200, 8400, 9100, 9900, 10800]),
    ("Budget Bites Daily", 21, 3300, 1_240_000, 71, [10000, 100000, 5000, 4200, 3800, 3100]),
]


def _seed_competitors(submission, now):
    """Attaches the demonstration competitor set with its metrics."""
    from apps.core.enums import MetricSource
    from apps.research.models import ResearchCompetitor

    for name, videos, subs, views, age_days, series in DEMO_COMPETITORS:
        ResearchCompetitor.objects.create(
            submission=submission,
            channel_name=name,
            channel_url=f"https://www.youtube.com/@{name.lower().replace(' ', '')}",
            subscriber_count=subs,
            view_count=views,
            video_count=videos,
            oldest_video_at=now - timedelta(days=age_days),
            newest_video_at=now - timedelta(days=2),
            video_views_series=series,
            metrics_source=MetricSource.MANUAL,
            metrics_fetched_at=now,
        )


APPROVED_STATUSES = {
    StudentStatus.RESEARCH_APPROVED, StudentStatus.CHANNEL_CREATION_PENDING,
    StudentStatus.CHANNEL_CREATED, StudentStatus.CONTENT_PRODUCTION_STARTED,
    StudentStatus.ACTIVE, StudentStatus.INACTIVE, StudentStatus.AT_RISK,
    StudentStatus.BATCH_COMPLETED,
}


class Command(BaseCommand):
    help = "Seeds settings, alert rules, criteria, staff accounts, batches and demonstration students."

    def add_arguments(self, parser):
        parser.add_argument("--admin-email", default="admin@100daysai.local")
        parser.add_argument("--admin-password", default=None, help="Generated if omitted.")

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()
        today = timezone.localdate()
        self.stdout.write("Seeding 100DaysAI Academy Tracker…\n")

        for key, value, label, value_type, category, description in SETTINGS:
            SystemSetting.objects.update_or_create(
                key=key,
                defaults={"value": value, "label": label, "value_type": value_type,
                          "category": category, "description": description},
            )
        self.stdout.write(f"  ok  {len(SETTINGS)} system settings")

        for key, name, evaluator, params, priority, channels, cooldown, action in ALERT_RULES:
            AlertRule.objects.update_or_create(
                key=key,
                defaults={"name": name, "condition": {"evaluator": evaluator, "params": params},
                          "priority": priority, "channels": channels, "cooldown_hours": cooldown,
                          "recommended_action": action, "is_active": True},
            )
        self.stdout.write(f"  ok  {len(ALERT_RULES)} alert rules")

        for order, (key, label, weight, needs_analytics) in enumerate(SCORING_FACTORS):
            ScoringFactor.objects.update_or_create(
                key=key,
                defaults={"label": label, "weightage": weight,
                          "requires_analytics": needs_analytics, "display_order": order},
            )
        self.stdout.write(f"  ok  {len(SCORING_FACTORS)} scoring factors")

        criteria_set, _ = ResearchCriteriaSet.objects.update_or_create(
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
        self.stdout.write(f"  ok  {len(CRITERIA)} research criteria")

        credentials = []

        def make_user(email, name, role):
            user = User.objects.filter(email=email).first()
            if user:
                return user, None
            password = options["admin_password"] if role == UserRole.SUPER_ADMIN and options["admin_password"] else generate_password()
            user = User.objects.create_user(
                email=email, password=password, full_name=name, role=role,
                is_staff=role == UserRole.SUPER_ADMIN, is_superuser=role == UserRole.SUPER_ADMIN,
            )
            credentials.append((role, email, password))
            return user, password

        admin, _ = make_user(options["admin_email"], "Academy Administrator", UserRole.SUPER_ADMIN)
        instructor1, _ = make_user("instructor1@100daysai.local", "Zoya Rahman", UserRole.INSTRUCTOR)
        instructor2, _ = make_user("instructor2@100daysai.local", "Imran Siddiqui", UserRole.INSTRUCTOR)
        make_user("manager@100daysai.local", "Operations Manager", UserRole.MANAGEMENT_READONLY)
        self.stdout.write("  ok  4 user accounts")

        batch1, _ = Batch.objects.update_or_create(
            code="YTA-2026-01",
            defaults={"name": "January 2026 Cohort", "instructor": instructor1,
                      "start_date": today - timedelta(days=70), "end_date": today + timedelta(days=20),
                      "status": "ACTIVE", "research_days": None, "criteria_set": criteria_set,
                      "description": "Uses the academy-wide research window."},
        )
        batch2, _ = Batch.objects.update_or_create(
            code="YTA-2026-02",
            defaults={"name": "February 2026 Cohort", "instructor": instructor2,
                      "start_date": today - timedelta(days=35), "end_date": today + timedelta(days=55),
                      # Deliberately different, to prove the window is per-batch
                      # and not a constant.
                      "status": "ACTIVE", "research_days": 21, "criteria_set": criteria_set,
                      "description": "Overrides the research window to 21 days."},
        )
        self.stdout.write("  ok  2 batches")

        if Student.objects.exists():
            self.stdout.write(f"  --  {Student.objects.count()} students already present, skipping student seed")
        else:
            for index, (name, city, country, status, research_offset, activity_offset, channel) in enumerate(STUDENTS):
                batch = batch1 if index % 2 == 0 else batch2
                instructor = instructor1 if index % 2 == 0 else instructor2
                window = batch.research_days or 15

                research_start = today + timedelta(days=research_offset) if research_offset is not None else None
                enrollment_date = today + timedelta(days=(research_offset or -3) - 3)

                student = Student.objects.create(
                    enrollment_id=f"100DAI-{enrollment_date.year}-{index + 1:04d}",
                    full_name=name,
                    email=f"{name.lower().replace(' ', '.').replace(chr(39), '')}@example.com",
                    phone=f"+92 300 {1000000 + index}",
                    city=city, country=country,
                    enrollment_date=enrollment_date,
                    batch=batch, instructor=instructor,
                    course_start_date=enrollment_date,
                    expected_completion_date=enrollment_date + timedelta(days=100),
                    research_start_date=research_start,
                    research_deadline=research_start + timedelta(days=window) if research_start else None,
                    status=status,
                    roadmap_stage=STATUS_TO_STAGE[status],
                    last_activity_at=now + timedelta(days=activity_offset) if activity_offset is not None else None,
                    lms_student_id=f"LMS-{1000 + index}",
                )

                StudentStatusHistory.objects.create(
                    student=student, to_status=status, to_stage=student.roadmap_stage,
                    reason="Seeded record", changed_by=admin,
                )

                if status in {StudentStatus.ASSIGNMENT_SUBMITTED, StudentStatus.REVISION_REQUIRED}:
                    submission = ResearchSubmission.objects.create(
                        student=student, version=1,
                        status=SubmissionStatus.REVISION_REQUESTED if status == StudentStatus.REVISION_REQUIRED else SubmissionStatus.UNDER_REVIEW,
                        topic="Faceless finance explainers", niche="Personal finance",
                        target_country="United States", submitted_at=now - timedelta(days=5),
                        competition_level=RatingLevel.MEDIUM, earning_potential=RatingLevel.HIGH,
                        evaluator=instructor if status == StudentStatus.REVISION_REQUIRED else None,
                        reviewed_at=now - timedelta(days=3) if status == StudentStatus.REVISION_REQUIRED else None,
                        rejection_reason="Competitor analysis covers only two channels; five are required." if status == StudentStatus.REVISION_REQUIRED else None,
                    )
                    _seed_competitors(submission, now)

                if status in APPROVED_STATUSES:
                    approved_at = now + timedelta(days=(research_offset or -30) + 12)
                    ResearchSubmission.objects.create(
                        student=student, version=1, status=SubmissionStatus.APPROVED,
                        topic=channel[1] if channel else "General interest",
                        niche=channel[1] if channel else "General interest",
                        target_country="United States",
                        submitted_at=now + timedelta(days=(research_offset or -30) + 10),
                        reviewed_at=approved_at, approved_at=approved_at,
                        evaluator=instructor, score=72 + (index % 20),
                        feedback="Solid niche definition and competitor set. Approved to proceed to channel creation.",
                    )

                if channel:
                    channel_name, niche, channel_status, upload_offset = channel
                    YoutubeChannel.objects.create(
                        student=student, channel_name=channel_name,
                        channel_url=f"https://www.youtube.com/@{channel_name.lower().replace(' ', '')}",
                        niche=niche,
                        content_type=[ContentType.SHORTS, ContentType.LONG_FORM, ContentType.MIXED][index % 3],
                        target_country="United States", primary_language="English",
                        upload_schedule="3 videos per week", status=channel_status,
                        monetization_status=MonetizationStatus.APPROVED if channel_status == ChannelStatus.MONETIZED else MonetizationStatus.UNKNOWN,
                        channel_creation_date=today + timedelta(days=(research_offset or -40) + 20),
                        last_upload_at=now + timedelta(days=upload_offset) if upload_offset is not None else None,
                        ownership_verified=channel_status != ChannelStatus.PENDING,
                    )

            self.stdout.write(f"  ok  {len(STUDENTS)} students with submissions and channels")

        self.stdout.write("\nSeed complete.\n")

        if credentials:
            line = "-" * 64
            self.stdout.write(line)
            self.stdout.write("  ACCOUNT CREDENTIALS - shown once, here only.")
            self.stdout.write("  These are never displayed anywhere in the application UI.")
            self.stdout.write("  Change them after first sign-in.")
            self.stdout.write(line)
            for role, email, password in credentials:
                self.stdout.write(f"  {role:<20} {email:<34} {password}")
            self.stdout.write(line)
            self.stdout.write(
                "\n  Note: the Super Admin has no authenticator enrolled yet, so the\n"
                "  first sign-in goes straight through. Enroll TOTP before going live.\n"
            )
