"""
Rebuilds the activity log from records that already exist.

For a deployment that predates the log: every status change, research and
assignment attempt, review, channel, connected grant, published video and
student sign-in already carries a timestamp, so its entry can be written with
the moment it actually happened. Safe to run repeatedly — each entry is keyed
on the record it describes and written at most once.
"""

from __future__ import annotations

from apps.academy.activity import record_activity
from apps.academy.models import Student, StudentStatusHistory
from apps.core.enums import ActivityKind, AuditAction, UserRole
from apps.core.models import AuditLog


def backfill_activity() -> dict[str, int]:
    from apps.assignments.models import AssignmentSubmission
    from apps.research.models import ResearchSubmission
    from apps.youtube.models import ChannelOauthGrant, Video, YoutubeChannel

    written: dict[str, int] = {}
    labels = dict(Student._meta.get_field("status").choices)

    def log(student, kind, summary, **kwargs):
        if record_activity(student, kind, summary, dedupe=True, **kwargs):
            written[kind] = written.get(kind, 0) + 1

    for h in StudentStatusHistory.objects.select_related("student", "changed_by"):
        summary = (
            f"Status changed from {labels.get(h.from_status, h.from_status)} to {labels.get(h.to_status, h.to_status)}"
            if h.from_status
            else f"Status set to {labels.get(h.to_status, h.to_status)}"
        )
        log(h.student, ActivityKind.STATUS_CHANGED, summary, actor=h.changed_by, detail=h.reason,
            target=h, occurred_at=h.created_at)

    for r in ResearchSubmission.objects.select_related("student", "evaluator").exclude(submitted_at=None):
        log(r.student, ActivityKind.RESEARCH_SUBMITTED,
            f"Submitted research v{r.version}" + (f": {r.topic}" if r.topic else ""),
            actor=r.student.user, target=r, occurred_at=r.submitted_at)
        if r.reviewed_at:
            log(r.student, ActivityKind.RESEARCH_REVIEWED,
                f"Research v{r.version} {r.get_status_display().lower()}"
                + (f" ({r.score}%)" if r.score is not None else ""),
                actor=r.evaluator, detail=r.feedback or r.rejection_reason, target=r, occurred_at=r.reviewed_at)

    for a in AssignmentSubmission.objects.select_related("student", "assignment", "evaluator").exclude(submitted_at=None):
        log(a.student, ActivityKind.ASSIGNMENT_SUBMITTED,
            f'Submitted "{a.assignment.title}" (attempt {a.version})' + (" via the LMS" if a.lms_submission_id else ""),
            actor=a.student.user, target=a, occurred_at=a.submitted_at)
        if a.reviewed_at:
            log(a.student, ActivityKind.ASSIGNMENT_REVIEWED,
                f'"{a.assignment.title}" attempt {a.version} {a.get_status_display().lower()}'
                + (f" ({a.score})" if a.score is not None else ""),
                actor=a.evaluator, detail=a.feedback or a.rejection_reason, target=a, occurred_at=a.reviewed_at)

    for c in YoutubeChannel.objects.select_related("student"):
        log(c.student, ActivityKind.CHANNEL_ADDED, f'Channel "{c.channel_name}" recorded',
            target=c, occurred_at=c.created_at)

    for g in ChannelOauthGrant.objects.select_related("channel__student").filter(revoked_at=None):
        log(g.channel.student, ActivityKind.CHANNEL_CONNECTED,
            f'Connected YouTube Analytics for "{g.channel.channel_name}"', target=g, occurred_at=g.consented_at)

    for v in Video.objects.select_related("channel__student").exclude(published_at=None):
        log(v.channel.student, ActivityKind.VIDEO_PUBLISHED, f'Published "{v.title}" on {v.channel.channel_name}',
            target=v, occurred_at=v.published_at, metadata={"video_type": v.video_type})

    sign_ins = AuditLog.objects.filter(
        action=AuditAction.LOGIN, entity_type="User", actor__role=UserRole.STUDENT,
        actor__student_profile__isnull=False, summary__endswith="signed in.",
    ).select_related("actor__student_profile")
    for entry in sign_ins:
        log(entry.actor.student_profile, ActivityKind.LOGIN, "Signed in to the portal",
            actor=entry.actor, target=entry, occurred_at=entry.created_at)

    return written
