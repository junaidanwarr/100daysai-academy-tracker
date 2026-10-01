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

from apps.academy.activity import record_activity
from apps.academy.backfill import backfill_activity
from apps.academy.models import Batch, Student, StudentStatusHistory
from apps.academy.status import STATUS_TO_STAGE
from apps.accounts.models import User
from apps.core.crypto import generate_password
from apps.core.defaults import ALERT_RULES, CRITERIA, SCORING_FACTORS, SETTINGS, install_defaults
from apps.core.enums import (
    ActivityKind,
    AssignmentType,
    ChannelStatus,
    ContentType,
    MonetizationStatus,
    RatingLevel,
    StudentStatus,
    SubmissionStatus,
    UserRole,
)
from apps.assignments.models import Assignment, AssignmentSubmission
from apps.research.models import ResearchCriteriaSet, ResearchSubmission
from apps.youtube.models import YoutubeChannel

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


# (title, type, batch index or None for all, due offset days, max score, instructions)
ASSIGNMENTS = [
    ("Channel branding kit", AssignmentType.PROJECT, 0, -2, 20,
     "Channel name shortlist, logo, banner and a one-paragraph channel description. Link a Drive folder."),
    ("First three scripts", AssignmentType.PROJECT, 0, 5, 30,
     "Write scripts for your first three videos, each with a hook, three beats and a call to action."),
    ("Thumbnail A/B pair", AssignmentType.VIDEO_SUBMISSION, 1, -3, 10,
     "Two thumbnail variants for the same video, with a sentence on what each is testing."),
    ("Weekly reflection", AssignmentType.OTHER, None, 2, None,
     "What did you publish, what did you learn, and what is blocking you? Five sentences is plenty."),
]


def _seed_assignments(batches, instructors, now):
    """Assignments with a spread of attempts: approved, sent back, awaiting, late and missing."""
    assignments = []
    for title, kind, batch_index, due_offset, max_score, description in ASSIGNMENTS:
        assignments.append(Assignment.objects.create(
            title=title, type=kind, batch=batches[batch_index] if batch_index is not None else None,
            due_at=now + timedelta(days=due_offset), max_score=max_score, description=description,
        ))
    branding, scripts, thumbnails, reflection = assignments

    # Only students who are engaged get attempts, and each attempt predates
    # their last recorded activity so the seed tells one consistent story.
    engaged = list(
        Student.objects.filter(last_activity_at__gte=now - timedelta(days=7))
        .exclude(status__in=[StudentStatus.ENROLLED, StudentStatus.RESEARCH_PENDING])
        .order_by("enrollment_id")
    )
    written = 0
    for index, student in enumerate(engaged):
        instructor = instructors[0] if student.batch_id == batches[0].pk else instructors[1]
        handed_in = student.last_activity_at - timedelta(hours=6)
        link = f"https://drive.google.com/drive/folders/demo-{student.enrollment_id.lower()}"

        work = branding if student.batch_id == batches[0].pk else thumbnails
        if index % 4 == 3:
            continue  # nothing handed in: shows up as overdue on the roster
        if index % 4 == 2:
            # Sent back once, resubmitted, now awaiting review.
            AssignmentSubmission.objects.create(
                assignment=work, student=student, version=1, status=SubmissionStatus.REVISION_REQUESTED,
                submitted_at=handed_in - timedelta(days=4), reviewed_at=handed_in - timedelta(days=3),
                evaluator=instructor, score=4 if work.max_score == 10 else 8,
                submission_url=link, superseded_at=handed_in,
                rejection_reason="The brief asks for every element; the banner is missing.",
                feedback="Good start — the name shortlist is strong.",
            )
            AssignmentSubmission.objects.create(
                assignment=work, student=student, version=2, status=SubmissionStatus.RESUBMITTED,
                submitted_at=handed_in, submission_url=link,
                response_text="Added the banner and tightened the description.",
            )
        elif index % 4 == 1:
            AssignmentSubmission.objects.create(
                assignment=work, student=student, version=1, status=SubmissionStatus.SUBMITTED,
                submitted_at=handed_in, submission_url=link,
            )
        else:
            AssignmentSubmission.objects.create(
                assignment=work, student=student, version=1, status=SubmissionStatus.APPROVED,
                submitted_at=handed_in - timedelta(days=2), reviewed_at=handed_in - timedelta(days=1),
                approved_at=handed_in - timedelta(days=1), evaluator=instructor,
                score=(work.max_score or 10) - (index % 3), submission_url=link,
                feedback="Clear and complete. Carry this consistency into the scripts.",
            )
        written += 1 + (index % 4 == 2)

        if index % 3 == 0:
            AssignmentSubmission.objects.create(
                assignment=reflection, student=student, version=1, status=SubmissionStatus.SUBMITTED,
                submitted_at=handed_in + timedelta(hours=2),
                response_text="Published two Shorts, learned that the first second decides retention. "
                              "Blocked on voiceover quality.",
            )
            written += 1
    return len(assignments), written


def _seed_attendance(instructors, now):
    """Two live classes: most engaged students attended, a few did not."""
    count = 0
    for days_ago, topic in [(8, "Hooks and retention"), (1, "Thumbnails that earn the click")]:
        held = now - timedelta(days=days_ago)
        for student in Student.objects.tracked().order_by("enrollment_id"):
            attended = student.last_activity_at and student.last_activity_at >= held - timedelta(days=1)
            record_activity(
                student,
                ActivityKind.CLASS_ATTENDED if attended else ActivityKind.CLASS_MISSED,
                f"Live class: {topic}",
                actor=student.instructor or instructors[0],
                occurred_at=held,
                metadata={"manual": True},
            )
            count += 1
    return count


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

        added = install_defaults()
        self.stdout.write(f"  ok  {len(SETTINGS)} system settings ({added['settings']} new)")
        self.stdout.write(f"  ok  {len(ALERT_RULES)} alert rules ({added['alert_rules']} new)")
        self.stdout.write(f"  ok  {len(SCORING_FACTORS)} scoring factors ({added['scoring_factors']} new)")
        self.stdout.write(f"  ok  {len(CRITERIA)} research criteria ({added['criteria']} new)")
        criteria_set = ResearchCriteriaSet.objects.filter(is_default=True).first()

        credentials = []

        def make_user(email, name, role):
            user = User.objects.filter(email=email).first()
            if user:
                return user, None
            password = options["admin_password"] if role == UserRole.SUPER_ADMIN and options["admin_password"] else generate_password()
            user = User.objects.create_user(
                email=email, password=password, full_name=name, role=role,
                is_staff=role == UserRole.SUPER_ADMIN, is_superuser=role == UserRole.SUPER_ADMIN,
                must_change_password=True,
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

        if Assignment.objects.exists():
            self.stdout.write(f"  --  {Assignment.objects.count()} assignments already present, skipping")
        else:
            set_count, attempt_count = _seed_assignments([batch1, batch2], [instructor1, instructor2], now)
            self.stdout.write(f"  ok  {set_count} assignments with {attempt_count} attempts")
            self.stdout.write(f"  ok  {_seed_attendance([instructor1, instructor2], now)} class attendance entries")

        written = backfill_activity()
        self.stdout.write(f"  ok  {sum(written.values())} activity-log entries from existing records")

        self.stdout.write("\nSeed complete.\n")

        if credentials:
            line = "-" * 64
            self.stdout.write(line)
            self.stdout.write("  ACCOUNT CREDENTIALS - shown once, here only.")
            self.stdout.write("  These are never displayed anywhere in the application UI.")
            self.stdout.write("  Each account must set its own password at first sign-in.")
            self.stdout.write(line)
            for role, email, password in credentials:
                self.stdout.write(f"  {role:<20} {email:<34} {password}")
            self.stdout.write(line)
            self.stdout.write(
                "\n  Note: the Super Admin has no authenticator enrolled yet, so the\n"
                "  first sign-in goes straight through. Enroll TOTP before going live.\n"
            )
