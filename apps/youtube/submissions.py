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
from apps.core.enums import ActivityKind, AuditAction, ChannelStatus, NotificationChannel, SubmissionStatus, UserRole
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


def _notify_staff(student, channel: YoutubeChannel) -> None:
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
            title=f"Channel to review: {channel.channel_name}",
            body=(
                f"{student.full_name} ({student.enrollment_id}) submitted their YouTube channel. "
                "Check it matches the approved niche, then set its status."
            ),
            link_url=channel.get_absolute_url(),
        )
