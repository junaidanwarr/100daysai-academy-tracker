"""
A student submitting their own channel.

Staff can still record a channel from the console exactly as before. This is the
student-side door: once research is approved, the student hands in the channel
they created, it lands UNDER_REVIEW, and their instructor is told. Nothing a
student submits can mark a channel approved, active or verified — those stay
staff decisions, made on the console's edit page.
"""

from __future__ import annotations

from django.db import transaction

from apps.academy.activity import record_activity
from apps.core.audit import write_audit
from apps.core.enums import (
    ActivityKind, AuditAction, ChannelStatus, NotificationChannel, StudentStatus, SubmissionStatus, UserRole,
)
from apps.core.middleware import current_request_meta
from apps.core.permissions import assert_can
from apps.youtube.models import YoutubeChannel


class ChannelSubmissionError(Exception):
    """The student cannot submit a channel as things stand."""


def approved_research(student):
    """The approved research the channel is meant to carry out, newest first."""
    return (
        student.research_submissions.filter(status=SubmissionStatus.APPROVED)
        .order_by("-version")
        .first()
    )


def channel_block_reason(student) -> str | None:
    if approved_research(student) is None:
        return "You can submit your channel once your research has been approved."
    return None


@transaction.atomic
def submit_student_channel(actor, student, form) -> YoutubeChannel:
    """
    Saves a StudentChannelForm as a channel UNDER_REVIEW for ``student``.

    ``actor`` must be the student's own login; the portal guarantees it, and
    this checks again so the service cannot be misused from elsewhere.
    """
    assert_can(actor.role, "channel", "create")
    if actor.role != UserRole.STUDENT or student.user_id != actor.pk:
        raise ChannelSubmissionError("Only the student can submit their own channel here.")

    blocked = channel_block_reason(student)
    if blocked:
        raise ChannelSubmissionError(blocked)

    research = approved_research(student)
    channel = form.save(commit=False)
    channel.student = student
    channel.status = ChannelStatus.UNDER_REVIEW
    channel.ownership_verified = False
    # The niche the student was approved for, so staff can compare at a glance.
    channel.niche = research.niche
    channel.sub_niche = research.sub_niche
    channel.target_audience = research.target_audience
    channel.target_country = research.target_country
    channel.content_format = research.content_format
    channel.save()

    write_audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="YoutubeChannel",
        entity_id=channel.pk,
        summary=(
            f'{student.full_name} ({student.enrollment_id}) submitted channel '
            f'"{channel.channel_name}" for review'
        ),
        after={"channel_name": channel.channel_name, "channel_url": channel.channel_url, "status": channel.status},
        **current_request_meta(),
    )

    record_activity(
        student,
        ActivityKind.CHANNEL_SUBMITTED,
        f'Submitted channel "{channel.channel_name}" for review',
        actor=actor,
        target=channel,
    )

    _notify_staff(student, channel)
    return channel


# Statuses in which the student may no longer touch the record at all.
FROZEN_STATUSES = (ChannelStatus.SUSPENDED, ChannelStatus.TERMINATED, ChannelStatus.COMPLETED)

STUDENT_EDITABLE_FIELDS = [
    "channel_name", "channel_url", "youtube_channel_id", "channel_creation_date",
    "content_type", "primary_language", "upload_schedule", "notes",
]


def identity_locked(channel: YoutubeChannel) -> bool:
    """Link and channel ID stay editable only until staff have looked at it."""
    return channel.status not in (ChannelStatus.UNDER_REVIEW, ChannelStatus.PENDING)


def edit_block_reason(channel: YoutubeChannel) -> str | None:
    if channel.status in FROZEN_STATUSES:
        return f"This channel is {channel.get_status_display().lower()}, so it can no longer be edited."
    return None


@transaction.atomic
def update_student_channel(actor, student, channel: YoutubeChannel, form) -> list[str]:
    """
    Saves a student's edit of their own channel and returns the fields that
    changed. Staff are told, and the audit log records the difference.
    """
    from apps.core.audit import diff_fields, snapshot

    if actor.role != UserRole.STUDENT or student.user_id != actor.pk or channel.student_id != student.pk:
        raise ChannelSubmissionError("Only the student can edit their own channel here.")
    blocked = edit_block_reason(channel)
    if blocked:
        raise ChannelSubmissionError(blocked)

    def fields(instance):
        # A blank text box and an empty column are the same answer.
        return {k: (None if v == "" else v) for k, v in snapshot(instance, STUDENT_EDITABLE_FIELDS).items()}

    before = fields(YoutubeChannel.objects.get(pk=channel.pk))
    channel = form.save()
    changed_before, changed_after = diff_fields(before, fields(channel))
    if not changed_after:
        return []

    # An authorization belongs to the channel it was granted for. A different
    # channel ID means a different channel, so the old grant must not be used.
    if "youtube_channel_id" in changed_after:
        grant = getattr(channel, "oauth_grant", None)
        if grant and not grant.revoked_at:
            from apps.youtube import services

            services.disconnect(actor, channel, reason="Channel ID changed by the student.")

    write_audit(
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="YoutubeChannel",
        entity_id=channel.pk,
        summary=f'{student.full_name} ({student.enrollment_id}) edited channel "{channel.channel_name}"',
        before=changed_before,
        after=changed_after,
        **current_request_meta(),
    )
    _notify_staff(student, channel, edited=sorted(changed_after))
    return sorted(changed_after)


def _notify_staff(student, channel: YoutubeChannel, edited: list[str] | None = None) -> None:
    from apps.accounts.models import User
    from apps.monitoring.models import Notification

    if student.instructor_id:
        recipients = [student.instructor_id]
    else:
        recipients = list(
            User.objects.active().filter(role=UserRole.SUPER_ADMIN).values_list("pk", flat=True)
        )

    for user_id in recipients:
        Notification.objects.create(
            user_id=user_id,
            channel=NotificationChannel.IN_APP,
            title=(f"Channel edited: {channel.channel_name}" if edited else f"Channel to review: {channel.channel_name}"),
            body=(
                f"{student.full_name} ({student.enrollment_id}) updated their channel: "
                + ", ".join(YoutubeChannel._meta.get_field(name).verbose_name for name in edited)
                + "."
                if edited
                else f"{student.full_name} ({student.enrollment_id}) submitted their YouTube channel. "
                "Check it matches the approved niche, then set its status."
            ),
            link_url=channel.get_absolute_url(),
        )


# --- Staff confirming a channel ---------------------------------------------

# A channel in any of these states has been confirmed by staff.
CONFIRMED_STATUSES = (ChannelStatus.APPROVED, ChannelStatus.ACTIVE, ChannelStatus.MONETIZED)

# Where the student is still waiting on a channel. A student further along —
# or inactive, at risk, completed — is left exactly where staff put them.
AWAITING_CHANNEL = (StudentStatus.RESEARCH_APPROVED, StudentStatus.CHANNEL_CREATION_PENDING)


def research_is_approved(student) -> bool:
    return student.research_submissions.filter(status=SubmissionStatus.APPROVED).exists()


def channel_confirmed(actor, channel: YoutubeChannel, previous_status: str | None) -> bool:
    """
    Called after staff save a channel. When it has just become confirmed, moves
    the student to Channel Created and tells them. Returns whether the student
    moved.

    Only a channel *entering* a confirmed state counts, so re-saving an already
    approved channel never moves anyone, and a student is only ever moved
    forward along the roadmap, never back.
    """
    from apps.academy.services import change_status
    from apps.academy.status import can_transition

    if channel.status not in CONFIRMED_STATUSES or previous_status in CONFIRMED_STATUSES:
        return False

    student = channel.student
    _notify_student(student, channel)

    if student.status not in AWAITING_CHANNEL or not can_transition(student.status, StudentStatus.CHANNEL_CREATED):
        return False

    # A consequence of a channel review staff are already authorized to make,
    # so the student-update check is not repeated (see change_status).
    change_status(
        actor, student, StudentStatus.CHANNEL_CREATED,
        reason=f'Channel "{channel.channel_name}" confirmed',
        propagated=True,
    )
    return True


def _notify_student(student, channel: YoutubeChannel) -> None:
    from apps.monitoring.models import Notification

    if not student.user_id:
        return
    Notification.objects.create(
        user_id=student.user_id,
        channel=NotificationChannel.IN_APP,
        title=f"Channel confirmed: {channel.channel_name}",
        body=(
            "Your instructor confirmed your channel."
            + (
                " Its figures appear on your channel page after each sync."
                if channel.youtube_channel_id
                else " Figures appear once its channel ID has been recorded."
            )
        ),
        link_url=f"/portal/channels/{channel.pk}/",
    )
