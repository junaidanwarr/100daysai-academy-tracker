"""
Every enumerated value in the system, in one place.

Django TextChoices rather than native Postgres enums: adding a value is then a
code change plus a no-op migration, instead of an ALTER TYPE that locks the
table. The stored values match the original specification exactly.
"""

from django.db import models


class UserRole(models.TextChoices):
    SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
    INSTRUCTOR = "INSTRUCTOR", "Instructor / Evaluator"
    STUDENT = "STUDENT", "Student"
    MANAGEMENT_READONLY = "MANAGEMENT_READONLY", "Read-only Management"


class StudentStatus(models.TextChoices):
    """All fifteen statuses from specification section 4."""

    ENROLLED = "ENROLLED", "Enrolled"
    RESEARCH_PENDING = "RESEARCH_PENDING", "Research Pending"
    RESEARCH_IN_PROGRESS = "RESEARCH_IN_PROGRESS", "Research in Progress"
    ASSIGNMENT_SUBMITTED = "ASSIGNMENT_SUBMITTED", "Assignment Submitted"
    REVISION_REQUIRED = "REVISION_REQUIRED", "Revision Required"
    RESEARCH_APPROVED = "RESEARCH_APPROVED", "Research Approved"
    CHANNEL_CREATION_PENDING = "CHANNEL_CREATION_PENDING", "Channel Creation Pending"
    CHANNEL_CREATED = "CHANNEL_CREATED", "Channel Created"
    CONTENT_PRODUCTION_STARTED = "CONTENT_PRODUCTION_STARTED", "Content Production Started"
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    AT_RISK = "AT_RISK", "At Risk"
    BATCH_COMPLETED = "BATCH_COMPLETED", "Batch Completed"
    DROPPED_OUT = "DROPPED_OUT", "Dropped Out"
    SUSPENDED = "SUSPENDED", "Suspended"


class RoadmapStage(models.TextChoices):
    ENROLLMENT = "ENROLLMENT", "Enrollment"
    NICHE_RESEARCH = "NICHE_RESEARCH", "Niche Research"
    ASSIGNMENT_SUBMISSION = "ASSIGNMENT_SUBMISSION", "Assignment Submission"
    ASSIGNMENT_REVIEW = "ASSIGNMENT_REVIEW", "Assignment Review"
    RESEARCH_APPROVED = "RESEARCH_APPROVED", "Research Approved"
    CHANNEL_CREATION = "CHANNEL_CREATION", "Channel Creation"
    CONTENT_PRODUCTION = "CONTENT_PRODUCTION", "Content Production"
    PERFORMANCE_MONITORING = "PERFORMANCE_MONITORING", "Performance Monitoring"
    BATCH_COMPLETION = "BATCH_COMPLETION", "Batch Completion"


class BatchStatus(models.TextChoices):
    PLANNED = "PLANNED", "Planned"
    ACTIVE = "ACTIVE", "Active"
    COMPLETED = "COMPLETED", "Completed"
    ARCHIVED = "ARCHIVED", "Archived"


class SubmissionStatus(models.TextChoices):
    """Shared by research and LMS assignments so review works identically."""

    NOT_STARTED = "NOT_STARTED", "Not Started"
    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    REVISION_REQUESTED = "REVISION_REQUESTED", "Revision Requested"
    RESUBMITTED = "RESUBMITTED", "Resubmitted"
    OVERDUE = "OVERDUE", "Overdue"


class ChannelStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
    APPROVED = "APPROVED", "Approved"
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    MONETIZED = "MONETIZED", "Monetized"
    SUSPENDED = "SUSPENDED", "Suspended"
    TERMINATED = "TERMINATED", "Terminated"
    ABANDONED = "ABANDONED", "Abandoned"
    COMPLETED = "COMPLETED", "Completed"


class ContentType(models.TextChoices):
    SHORTS = "SHORTS", "Shorts"
    LONG_FORM = "LONG_FORM", "Long-form"
    MIXED = "MIXED", "Mixed"


class VideoType(models.TextChoices):
    SHORT = "SHORT", "Short"
    LONG_FORM = "LONG_FORM", "Long-form"
    LIVE = "LIVE", "Live"
    PREMIERE = "PREMIERE", "Premiere"


class MonetizationStatus(models.TextChoices):
    NOT_ELIGIBLE = "NOT_ELIGIBLE", "Not Eligible"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    APPLIED = "APPLIED", "Applied"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    UNKNOWN = "UNKNOWN", "Unknown"


class MetricSource(models.TextChoices):
    """
    Where a metric came from. PUBLIC_API values are available for any channel;
    ANALYTICS_API values require the channel owner's OAuth grant; MANUAL values
    were typed in by staff and are not machine-verified.
    """

    PUBLIC_API = "PUBLIC_API", "Public Data API"
    ANALYTICS_API = "ANALYTICS_API", "YouTube Analytics API"
    MANUAL = "MANUAL", "Entered manually"


class SyncCadence(models.TextChoices):
    EVERY_6_HOURS = "EVERY_6_HOURS", "Every 6 hours"
    EVERY_12_HOURS = "EVERY_12_HOURS", "Every 12 hours"
    DAILY = "DAILY", "Daily"
    WEEKLY = "WEEKLY", "Weekly"
    MANUAL = "MANUAL", "Manual only"


class SyncStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    RUNNING = "RUNNING", "Running"
    SUCCESS = "SUCCESS", "Success"
    PARTIAL = "PARTIAL", "Partial"
    FAILED = "FAILED", "Failed"


class PerformanceBand(models.TextChoices):
    EXCELLENT = "EXCELLENT", "Excellent"
    SATISFACTORY = "SATISFACTORY", "Satisfactory"
    NEEDS_IMPROVEMENT = "NEEDS_IMPROVEMENT", "Needs Improvement"
    AT_RISK = "AT_RISK", "At Risk"
    UNSATISFACTORY = "UNSATISFACTORY", "Unsatisfactory"


class TargetStatus(models.TextChoices):
    ON_TRACK = "ON_TRACK", "On Track"
    BEHIND_SCHEDULE = "BEHIND_SCHEDULE", "Behind Schedule"
    TARGET_ACHIEVED = "TARGET_ACHIEVED", "Target Achieved"
    TARGET_MISSED = "TARGET_MISSED", "Target Missed"
    AT_RISK = "AT_RISK", "At Risk"
    NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA", "Not Enough Data"


class TargetScope(models.TextChoices):
    BATCH = "BATCH", "Batch"
    STUDENT = "STUDENT", "Student"
    CHANNEL = "CHANNEL", "Channel"


class TargetComparator(models.TextChoices):
    GTE = "GTE", "At least"
    LTE = "LTE", "At most"
    EQ = "EQ", "Exactly"
    WITHIN_DAYS = "WITHIN_DAYS", "Within days"


class AlertPriority(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


class AlertStatus(models.TextChoices):
    NEW = "NEW", "New"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    RESOLVED = "RESOLVED", "Resolved"
    IGNORED = "IGNORED", "Ignored"
    ESCALATED = "ESCALATED", "Escalated"


class NotificationChannel(models.TextChoices):
    IN_APP = "IN_APP", "In-app"
    EMAIL = "EMAIL", "Email"
    SMS = "SMS", "SMS"
    WHATSAPP = "WHATSAPP", "WhatsApp"
    PUSH = "PUSH", "Push"


class NotificationDeliveryStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"
    SKIPPED = "SKIPPED", "Skipped"


class AgreementStatus(models.TextChoices):
    NOT_ISSUED = "NOT_ISSUED", "Not Issued"
    ISSUED = "ISSUED", "Issued"
    PENDING_SIGNATURE = "PENDING_SIGNATURE", "Pending Signature"
    SIGNED = "SIGNED", "Signed"
    SUPERSEDED = "SUPERSEDED", "Superseded"


class CompletionRecordStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ISSUED = "ISSUED", "Issued"
    PARTIALLY_SIGNED = "PARTIALLY_SIGNED", "Partially Signed"
    SIGNED = "SIGNED", "Signed"
    SUPERSEDED = "SUPERSEDED", "Superseded"


class SignerRole(models.TextChoices):
    STUDENT = "STUDENT", "Student"
    INSTRUCTOR = "INSTRUCTOR", "Instructor"
    ACADEMY_REPRESENTATIVE = "ACADEMY_REPRESENTATIVE", "Academy Representative"


class GrievanceCategory(models.TextChoices):
    ACADEMIC = "ACADEMIC", "Academic"
    INSTRUCTOR_CONDUCT = "INSTRUCTOR_CONDUCT", "Instructor Conduct"
    PLATFORM_ACCESS = "PLATFORM_ACCESS", "Platform Access"
    BILLING = "BILLING", "Billing"
    EVALUATION_DISPUTE = "EVALUATION_DISPUTE", "Evaluation Dispute"
    HARASSMENT = "HARASSMENT", "Harassment"
    OTHER = "OTHER", "Other"


class GrievanceStatus(models.TextChoices):
    SUBMITTED = "SUBMITTED", "Submitted"
    ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
    IN_REVIEW = "IN_REVIEW", "In Review"
    RESOLVED = "RESOLVED", "Resolved"
    CLOSED = "CLOSED", "Closed"
    APPEALED = "APPEALED", "Appealed"


class CommunicationType(models.TextChoices):
    INSTRUCTOR_FEEDBACK = "INSTRUCTOR_FEEDBACK", "Instructor Feedback"
    WARNING_NOTICE = "WARNING_NOTICE", "Warning Notice"
    PERFORMANCE_MEETING = "PERFORMANCE_MEETING", "Performance Meeting"
    MISSED_DEADLINE = "MISSED_DEADLINE", "Missed Deadline"
    SUPPORT_REQUEST = "SUPPORT_REQUEST", "Support Request"
    ACTION_PLAN = "ACTION_PLAN", "Action Plan"


class AuditAction(models.TextChoices):
    CREATE = "CREATE", "Create"
    UPDATE = "UPDATE", "Update"
    DELETE = "DELETE", "Delete"
    RESTORE = "RESTORE", "Restore"
    APPROVE = "APPROVE", "Approve"
    REJECT = "REJECT", "Reject"
    SIGN = "SIGN", "Sign"
    EXPORT = "EXPORT", "Export"
    LOGIN = "LOGIN", "Login"
    LOGIN_FAILED = "LOGIN_FAILED", "Login Failed"
    LOGOUT = "LOGOUT", "Logout"
    OAUTH_GRANT = "OAUTH_GRANT", "OAuth Grant"
    OAUTH_REVOKE = "OAUTH_REVOKE", "OAuth Revoke"
    SETTING_CHANGE = "SETTING_CHANGE", "Setting Change"
    OVERRIDE = "OVERRIDE", "Override"


class DocumentType(models.TextChoices):
    ID_PROOF = "ID_PROOF", "ID Proof"
    AGREEMENT = "AGREEMENT", "Agreement"
    CERTIFICATE = "CERTIFICATE", "Certificate"
    RESEARCH_FILE = "RESEARCH_FILE", "Research File"
    SCREENSHOT = "SCREENSHOT", "Screenshot"
    SPREADSHEET = "SPREADSHEET", "Spreadsheet"
    PROFILE_IMAGE = "PROFILE_IMAGE", "Profile Image"
    OTHER = "OTHER", "Other"


class RatingLevel(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    VERY_HIGH = "VERY_HIGH", "Very High"


class LmsProvider(models.TextChoices):
    MANUAL = "MANUAL", "Manual entry"
    CSV = "CSV", "CSV import"
    WEBHOOK = "WEBHOOK", "Webhook"
    GOOGLE_CLASSROOM = "GOOGLE_CLASSROOM", "Google Classroom"
    MOODLE = "MOODLE", "Moodle"
    TEACHABLE = "TEACHABLE", "Teachable"
    THINKIFIC = "THINKIFIC", "Thinkific"
    KAJABI = "KAJABI", "Kajabi"
    CUSTOM = "CUSTOM", "Custom"


class AssignmentType(models.TextChoices):
    RESEARCH = "RESEARCH", "Research"
    VIDEO_SUBMISSION = "VIDEO_SUBMISSION", "Video Submission"
    QUIZ = "QUIZ", "Quiz"
    PROJECT = "PROJECT", "Project"
    OTHER = "OTHER", "Other"
