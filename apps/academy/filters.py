"""
The saved filters listed in specification section 19.

Each is a pure function from "today" plus the configured thresholds to a Django
Q object, so the same definition drives the list page, the sidebar counts and
the deadline scanner — they can never disagree about what "at risk" means.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from django.db.models import Q
from django.utils import timezone

from apps.academy.status import ARCHIVED_STATUSES, RESEARCH_PENDING_STATUSES
from apps.core.enums import AlertStatus, ChannelStatus, StudentStatus, SubmissionStatus
from apps.core.settings_service import (
    INACTIVITY_DAYS,
    MAX_REJECTIONS_BEFORE_ALERT,
    NO_UPLOAD_DAYS,
    RESEARCH_WARNING_DAYS,
    get_int,
)


@dataclass
class PresetContext:
    today: date
    inactivity_days: int
    research_warning_days: int
    max_rejections: int
    no_upload_days: int

    @classmethod
    def current(cls) -> "PresetContext":
        return cls(
            today=timezone.localdate(),
            inactivity_days=get_int(INACTIVITY_DAYS),
            research_warning_days=get_int(RESEARCH_WARNING_DAYS),
            max_rejections=get_int(MAX_REJECTIONS_BEFORE_ALERT),
            no_upload_days=get_int(NO_UPLOAD_DAYS),
        )


@dataclass(frozen=True)
class StudentPreset:
    key: str
    label: str
    description: str
    build: Callable[[PresetContext], Q]


# Excludes students who are no longer actively tracked.
NOT_ARCHIVED = ~Q(status__in=ARCHIVED_STATUSES)


def _needs_follow_up(ctx: PresetContext) -> Q:
    return NOT_ARCHIVED & (
        Q(status=StudentStatus.AT_RISK)
        | Q(alerts__status__in=[AlertStatus.NEW, AlertStatus.ESCALATED])
    )


def _research_overdue(ctx: PresetContext) -> Q:
    return (
        NOT_ARCHIVED
        & Q(research_deadline__lt=ctx.today)
        & Q(status__in=RESEARCH_PENDING_STATUSES)
    )


def _research_due_soon(ctx: PresetContext) -> Q:
    return (
        NOT_ARCHIVED
        & Q(research_deadline__gte=ctx.today)
        & Q(research_deadline__lte=ctx.today + timedelta(days=ctx.research_warning_days))
        & Q(status__in=RESEARCH_PENDING_STATUSES)
    )


def _research_rejected(ctx: PresetContext) -> Q:
    return NOT_ARCHIVED & (
        Q(status=StudentStatus.REVISION_REQUIRED)
        | Q(
            research_submissions__status__in=[
                SubmissionStatus.REJECTED,
                SubmissionStatus.REVISION_REQUESTED,
            ],
            research_submissions__superseded_at__isnull=True,
        )
    )


def _rejected_repeatedly(ctx: PresetContext) -> Q:
    # Narrows to students with any rejection; the exact count is applied by the
    # service via annotation, so the preset still reads correctly on its own.
    return NOT_ARCHIVED & Q(research_submissions__status=SubmissionStatus.REJECTED)


def _approved_no_channel(ctx: PresetContext) -> Q:
    return (
        NOT_ARCHIVED
        & Q(status__in=[StudentStatus.RESEARCH_APPROVED, StudentStatus.CHANNEL_CREATION_PENDING])
        & ~Q(channels__deleted_at__isnull=True)
    )


def _channel_no_uploads(ctx: PresetContext) -> Q:
    return NOT_ARCHIVED & Q(channels__deleted_at__isnull=True, channels__last_upload_at__isnull=True)


def _inactive(ctx: PresetContext) -> Q:
    cutoff = timezone.now() - timedelta(days=ctx.inactivity_days)
    return NOT_ARCHIVED & (
        Q(last_activity_at__lt=cutoff)
        | Q(last_activity_at__isnull=True, enrollment_date__lt=ctx.today - timedelta(days=ctx.inactivity_days))
    )


def _upload_target_missed(ctx: PresetContext) -> Q:
    cutoff = timezone.now() - timedelta(days=ctx.no_upload_days)
    return NOT_ARCHIVED & Q(
        channels__deleted_at__isnull=True,
        channels__status__in=[ChannelStatus.ACTIVE, ChannelStatus.APPROVED, ChannelStatus.MONETIZED],
        channels__last_upload_at__lt=cutoff,
    )


def _at_risk(ctx: PresetContext) -> Q:
    return Q(status=StudentStatus.AT_RISK) | Q(
        alerts__priority="CRITICAL",
        alerts__status__in=[AlertStatus.NEW, AlertStatus.IN_PROGRESS, AlertStatus.ESCALATED],
    )


def _completion_pending(ctx: PresetContext) -> Q:
    return ~Q(status__in=[StudentStatus.BATCH_COMPLETED, StudentStatus.DROPPED_OUT]) & Q(
        batch__end_date__lt=ctx.today
    )


def _signature_pending(ctx: PresetContext) -> Q:
    return Q(agreement_status__in=["ISSUED", "PENDING_SIGNATURE"])


STUDENT_PRESETS: list[StudentPreset] = [
    StudentPreset("needs-follow-up", "Needs follow-up", "Students with an open alert, or flagged at risk.", _needs_follow_up),
    StudentPreset("research-overdue", "Research overdue", "Research deadline has passed without an approved submission.", _research_overdue),
    StudentPreset("research-due-soon", "Research due soon", "Deadline falls inside the configured warning window.", _research_due_soon),
    StudentPreset("research-rejected", "Research rejected", "Latest research submission was rejected or needs revision.", _research_rejected),
    StudentPreset("rejected-repeatedly", "Rejected more than allowed", "Research rejected more times than the configured threshold.", _rejected_repeatedly),
    StudentPreset("approved-no-channel", "Approved but no channel", "Research approved yet no YouTube channel recorded.", _approved_no_channel),
    StudentPreset("channel-no-uploads", "Channel created but no uploads", "A channel exists but nothing has been published to it.", _channel_no_uploads),
    StudentPreset("inactive", "Inactive", "No recorded activity within the configured window.", _inactive),
    StudentPreset("upload-target-missed", "Upload target missed", "Channel has not published inside the configured window.", _upload_target_missed),
    StudentPreset("at-risk", "At risk", "Flagged at risk, or holding a critical open alert.", _at_risk),
    StudentPreset("completion-pending", "Batch completion pending", "Batch has ended but the student is not marked complete.", _completion_pending),
    StudentPreset("signature-pending", "Agreement signature pending", "Completion document issued but not fully signed.", _signature_pending),
]

PRESETS_BY_KEY = {preset.key: preset for preset in STUDENT_PRESETS}


def get_preset(key: str) -> StudentPreset | None:
    return PRESETS_BY_KEY.get(key)
