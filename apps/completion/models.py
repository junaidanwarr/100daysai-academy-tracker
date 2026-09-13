"""
Completion records, signatures, grievances and communication logs
(specification sections 16 and 17).
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.enums import (
    CommunicationType,
    CompletionRecordStatus,
    GrievanceCategory,
    GrievanceStatus,
    SignerRole,
)


class CompletionRecord(models.Model):
    """
    Immutable once signed. A correction creates version + 1 and points
    `superseded_by` at it (specification 23.10 - 23.12).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey("academy.Student", on_delete=models.CASCADE, related_name="completion_records")
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=24, choices=CompletionRecordStatus.choices, default=CompletionRecordStatus.DRAFT, db_index=True
    )
    # Full factual snapshot rendered into the document, frozen at issue time so
    # later data changes never alter an issued record.
    content = models.JSONField()
    # Version of the acknowledgment/terms text used.
    template_version = models.CharField(max_length=40)
    pdf = models.FileField(upload_to="completion/%Y/", null=True, blank=True)
    pdf_url = models.URLField(max_length=500, null=True, blank=True)
    # SHA-256 of the generated PDF, for tamper evidence.
    pdf_hash = models.CharField(max_length=64, null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="issued_records"
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="supersedes"
    )
    superseded_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "completion_records"
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["student", "version"], name="uniq_completion_student_version")
        ]

    def __str__(self):
        return f"{self.student_id} completion v{self.version}"

    @property
    def is_locked(self) -> bool:
        """Signed documents become read-only; corrections issue a new version."""
        return self.status in {CompletionRecordStatus.SIGNED, CompletionRecordStatus.SUPERSEDED}


class Signature(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    completion_record = models.ForeignKey(CompletionRecord, on_delete=models.CASCADE, related_name="signatures")
    signer_role = models.CharField(max_length=32, choices=SignerRole.choices)
    signer_name = models.CharField(max_length=160)
    signer_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="signatures"
    )
    signature_image_url = models.TextField(null=True, blank=True)
    # SHA-256 over record id + signer + timestamp, for verification.
    signature_hash = models.CharField(max_length=64)
    signed_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "signatures"
        constraints = [
            models.UniqueConstraint(
                fields=["completion_record", "signer_role"], name="uniq_signature_record_role"
            )
        ]

    def __str__(self):
        return f"{self.signer_name} ({self.signer_role})"


class Grievance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Human-facing reference, e.g. GRV-2026-0007.
    reference = models.CharField(max_length=32, unique=True)
    student = models.ForeignKey("academy.Student", on_delete=models.CASCADE, related_name="grievances")
    category = models.CharField(max_length=32, choices=GrievanceCategory.choices)
    subject = models.CharField(max_length=200)
    description = models.TextField()
    submitted_at = models.DateTimeField(default=timezone.now)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="handled_grievances"
    )
    status = models.CharField(
        max_length=24, choices=GrievanceStatus.choices, default=GrievanceStatus.SUBMITTED, db_index=True
    )
    academy_response = models.TextField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    student_acknowledged_at = models.DateTimeField(null=True, blank=True)
    appeal_requested_at = models.DateTimeField(null=True, blank=True)
    appeal_outcome = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "grievances"
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.reference}: {self.subject}"


class GrievanceAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grievance = models.ForeignKey(Grievance, on_delete=models.CASCADE, related_name="attachments")
    file_name = models.CharField(max_length=255)
    file = models.FileField(upload_to="grievances/%Y/%m/", null=True, blank=True)
    file_url = models.URLField(max_length=500, null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)
    mime_type = models.CharField(max_length=120, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "grievance_attachments"

    def __str__(self):
        return self.file_name


class CommunicationLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey("academy.Student", on_delete=models.CASCADE, related_name="communication_logs")
    type = models.CharField(max_length=32, choices=CommunicationType.choices)
    subject = models.CharField(max_length=200)
    summary = models.TextField()
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="communication_logs"
    )
    action_plan = models.TextField(null=True, blank=True)
    follow_up_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "communication_logs"
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["student", "-occurred_at"])]

    def __str__(self):
        return self.subject
