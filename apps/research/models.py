"""
Research submission and evaluation (specification section 5).

Governing rule: an attempt is never edited after review. A resubmission creates
version N+1 and marks the previous one superseded, so the scores and feedback
given on every earlier attempt stay readable forever (specification 23.4).
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.enums import DocumentType, MetricSource, RatingLevel, SubmissionStatus
from apps.core.models import SoftDeleteModel


class ResearchCriteriaSet(SoftDeleteModel):
    """
    A versioned bundle of criteria. Batches point at a set, so changing criteria
    for a new batch never re-scores an old one.
    """

    name = models.CharField(max_length=160)
    version = models.PositiveIntegerField(default=1)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = "research_criteria_sets"
        ordering = ["-is_default", "name"]
        constraints = [models.UniqueConstraint(fields=["name", "version"], name="uniq_criteria_set_name_version")]

    def __str__(self):
        return f"{self.name} v{self.version}"

    @property
    def total_weight(self):
        return sum(c.weightage for c in self.criteria.filter(is_active=True, deleted_at__isnull=True))


class ResearchCriterion(SoftDeleteModel):
    """
    Admin-configurable. Adding or reweighting a criterion never requires a code
    change (specification section 5).
    """

    criteria_set = models.ForeignKey(ResearchCriteriaSet, on_delete=models.CASCADE, related_name="criteria")
    name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    # Relative weight within the set. Scores are normalised across the set.
    weightage = models.DecimalField(max_digits=6, decimal_places=2)
    min_passing_score = models.DecimalField(max_digits=6, decimal_places=2)
    max_score = models.DecimalField(max_digits=6, decimal_places=2, default=100)
    is_required = models.BooleanField(default=True, help_text="Failing a required criterion fails the submission.")
    evidence_required = models.BooleanField(default=False)
    evaluator_notes = models.TextField(null=True, blank=True)
    display_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "research_criteria"
        ordering = ["display_order", "name"]
        verbose_name_plural = "research criteria"

    def __str__(self):
        return self.name


class ResearchSubmission(models.Model):
    """
    One row per attempt. Never updated after review — a resubmission creates
    version + 1 (specification 23.4).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey("academy.Student", on_delete=models.CASCADE, related_name="research_submissions")
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=24, choices=SubmissionStatus.choices, default=SubmissionStatus.DRAFT, db_index=True
    )

    topic = models.CharField(max_length=200, null=True, blank=True)
    niche = models.CharField(max_length=160, null=True, blank=True)
    sub_niche = models.CharField(max_length=160, null=True, blank=True)
    target_audience = models.CharField(max_length=300, null=True, blank=True)
    target_country = models.CharField(max_length=120, null=True, blank=True)
    content_format = models.CharField(max_length=160, null=True, blank=True)
    video_length_minutes = models.PositiveIntegerField(null=True, blank=True)
    upload_frequency = models.CharField(max_length=160, null=True, blank=True)
    competition_level = models.CharField(max_length=16, choices=RatingLevel.choices, null=True, blank=True)
    earning_potential = models.CharField(max_length=16, choices=RatingLevel.choices, null=True, blank=True)
    keyword_research = models.TextField(null=True, blank=True)
    content_gap_analysis = models.TextField(null=True, blank=True)
    monetization_potential = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    evaluator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_research"
    )
    # Weighted total derived from ResearchCriterionScore rows.
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    # Set when this attempt was superseded by a later version.
    superseded_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "research_submissions"
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["student", "version"], name="uniq_research_student_version")
        ]
        indexes = [models.Index(fields=["student", "-created_at"])]

    def __str__(self):
        return f"{self.student_id} research v{self.version}"

    @property
    def is_closed(self) -> bool:
        """Reviewed attempts are permanently read-only."""
        return self.status in {
            SubmissionStatus.APPROVED,
            SubmissionStatus.REJECTED,
            SubmissionStatus.REVISION_REQUESTED,
        }


class ResearchCompetitor(models.Model):
    """
    A reference channel the student offers as proof the niche works.

    The extra metrics exist so the four niche-validation rules can be checked
    arithmetically rather than taken on trust — see
    ``apps.research.competitor_rules``. Every one is nullable, because a figure
    the student could not obtain must stay distinguishable from a zero, and a
    rule with no data reports "could not be checked" rather than passing.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(ResearchSubmission, on_delete=models.CASCADE, related_name="competitors")
    channel_name = models.CharField(max_length=200)
    channel_url = models.URLField(max_length=500)
    subscriber_count = models.BigIntegerField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    # --- Niche-validation metrics ---
    youtube_channel_id = models.CharField(max_length=64, null=True, blank=True)
    video_count = models.IntegerField(null=True, blank=True)
    view_count = models.BigIntegerField(null=True, blank=True)
    oldest_video_at = models.DateTimeField(null=True, blank=True)
    newest_video_at = models.DateTimeField(null=True, blank=True)
    # Views per video, oldest first — the series rule 4 reads for spikes and
    # decline. Kept as a list rather than a summary so the judgement can be
    # re-derived if the thresholds change.
    video_views_series = models.JSONField(default=list, blank=True)
    # PUBLIC_API when fetched from YouTube, MANUAL when the student typed it.
    metrics_source = models.CharField(
        max_length=20, choices=MetricSource.choices, null=True, blank=True
    )
    metrics_fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "research_competitors"

    def __str__(self):
        return self.channel_name

    @property
    def oldest_video_date(self):
        return self.oldest_video_at.date() if self.oldest_video_at else None

    @property
    def has_metrics(self) -> bool:
        return any(
            value is not None
            for value in (self.video_count, self.view_count, self.subscriber_count, self.oldest_video_at)
        )


class ResearchCriterionScore(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(ResearchSubmission, on_delete=models.CASCADE, related_name="criterion_scores")
    criterion = models.ForeignKey(ResearchCriterion, on_delete=models.PROTECT, related_name="scores")
    score = models.DecimalField(max_digits=6, decimal_places=2)
    passed = models.BooleanField()
    evaluator_note = models.TextField(null=True, blank=True)
    evidence_url = models.URLField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "research_criterion_scores"
        constraints = [
            models.UniqueConstraint(fields=["submission", "criterion"], name="uniq_score_submission_criterion")
        ]

    def __str__(self):
        return f"{self.criterion_id}: {self.score}"


class ResearchAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(ResearchSubmission, on_delete=models.CASCADE, related_name="attachments")
    type = models.CharField(max_length=24, choices=DocumentType.choices, default=DocumentType.RESEARCH_FILE)
    file_name = models.CharField(max_length=255)
    file = models.FileField(upload_to="research/%Y/%m/", null=True, blank=True)
    file_url = models.URLField(max_length=500, null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)
    mime_type = models.CharField(max_length=120, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "research_attachments"

    def __str__(self):
        return self.file_name
