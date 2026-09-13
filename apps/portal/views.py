"""
Student-facing views.

Every one of these is scoped by the signed-in user's own profile and none of
them accepts a student id in the URL — there is no identifier to tamper with.
Where a page shows an object with a primary key (a channel, a video), the
lookup is filtered by the student's own rows first, so a guessed UUID is a 404
rather than someone else's record.

What the portal deliberately does not show: the scoring rubric and its
weightages, other students, batch rosters, alerts, the audit log, and the
evaluator's private notes. A student sees the decision and the feedback written
for them, not the machinery that produced it.
"""

from __future__ import annotations

from django.contrib import messages
from django.db.models import F, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.academy.status import ROADMAP_ORDER
from apps.assignments.services import scoped_submissions
from apps.core.enums import MetricSource, SubmissionStatus
from apps.portal.access import student_required, with_student
from apps.research.forms import ResearchSubmissionForm
from apps.research.models import ResearchSubmission
from apps.research.services import SubmissionStateError, submit_research
from apps.youtube import services as youtube_services
from apps.youtube.models import Video, YoutubeChannel

# A student may start a new attempt only when the previous one is closed. These
# are the states that still belong to the evaluator.
AWAITING_REVIEW = [
    SubmissionStatus.SUBMITTED,
    SubmissionStatus.UNDER_REVIEW,
    SubmissionStatus.RESUBMITTED,
]


# --- Overview ---------------------------------------------------------------


@with_student
def overview(request, student):
    """The roadmap, plus whatever needs the student's attention right now."""
    current = ROADMAP_ORDER.index(student.roadmap_stage) if student.roadmap_stage in ROADMAP_ORDER else 0
    submissions = student.research_submissions.all()
    latest = submissions.first()

    assignments = scoped_submissions(request.user)

    return render(
        request,
        "portal/overview.html",
        {
            "active_nav": "portal:overview",
            "student": student,
            "channels": student.channels.all(),
            "latest_submission": latest,
            "submission_count": submissions.count(),
            "awaiting_review": latest is not None and latest.status in AWAITING_REVIEW,
            "needs_resubmission": latest is not None and latest.status in [
                SubmissionStatus.REJECTED,
                SubmissionStatus.REVISION_REQUESTED,
            ],
            "assignment_count": assignments.count(),
            "assignments_awaiting": assignments.filter(status__in=AWAITING_REVIEW).count(),
            "stages": [
                {
                    "label": stage.replace("_", " ").title(),
                    "done": index < current,
                    "current": index == current,
                }
                for index, stage in enumerate(ROADMAP_ORDER)
            ],
            "overdue": bool(
                student.research_deadline
                and student.research_deadline < timezone.localdate()
                and (latest is None or latest.status not in [SubmissionStatus.APPROVED, *AWAITING_REVIEW])
            ),
        },
    )


# --- Research ---------------------------------------------------------------


@with_student
@require_http_methods(["GET", "POST"])
def research(request, student):
    """
    Submit or resubmit research, and read the history of every attempt.

    Attempts are never edited in place: a resubmission becomes version + 1 and
    the earlier attempt keeps its decision and its reason. The only exception is
    an unsubmitted draft, which is still the student's own working copy.
    """
    submissions = (
        ResearchSubmission.objects.filter(student=student)
        .prefetch_related("competitors")
        .order_by("-version")
    )
    latest = submissions.first()
    draft = latest if latest and latest.status == SubmissionStatus.DRAFT else None
    can_submit = not latest or latest.status not in AWAITING_REVIEW

    form = ResearchSubmissionForm(request.POST or None, instance=draft) if can_submit else None

    if request.method == "POST" and can_submit and form.is_valid():
        as_draft = request.POST.get("as_draft") == "1"
        try:
            data = {k: v for k, v in form.cleaned_data.items() if k != "competitors"}
            submit_research(request.user, student, data, form.cleaned_data["competitors"], as_draft=as_draft)
            messages.success(request, "Draft saved." if as_draft else "Research submitted for review.")
            return redirect("portal:research")
        except SubmissionStateError as exc:
            messages.error(request, str(exc))

    return render(
        request,
        "portal/research.html",
        {
            "active_nav": "portal:research",
            "student": student,
            "form": form,
            "draft": draft,
            "latest": latest,
            "can_submit": can_submit,
            "submissions": submissions,
            "existing_competitors": _competitor_rows(draft),
        },
    )


def _competitor_rows(draft) -> list[dict]:
    """
    Competitor rows shaped for the repeatable-row widget, so a saved draft comes
    back with every figure the student already typed rather than just the
    channel name.
    """
    if not draft:
        return []
    rows = []
    for c in draft.competitors.all():
        rows.append(
            {
                "channel_name": c.channel_name,
                "channel_url": c.channel_url,
                "subscriber_count": c.subscriber_count,
                "view_count": c.view_count,
                "video_count": c.video_count,
                "oldest_video_at": c.oldest_video_at.date().isoformat() if c.oldest_video_at else "",
                "video_views_series": ", ".join(str(v) for v in (c.video_views_series or [])),
                "notes": c.notes,
            }
        )
    return rows


# --- Assignments ------------------------------------------------------------


@with_student
def assignments(request, student):
    """Every LMS attempt recorded against this student, newest first."""
    submissions = scoped_submissions(request.user).order_by("-submitted_at", "-created_at")

    return render(
        request,
        "portal/assignments.html",
        {
            "active_nav": "portal:assignments",
            "student": student,
            "submissions": submissions,
            "awaiting": submissions.filter(status__in=AWAITING_REVIEW).count(),
            "approved": submissions.filter(status=SubmissionStatus.APPROVED).count(),
        },
    )


# --- Channels ---------------------------------------------------------------


@with_student
def channels(request, student):
    """
    Read-only. A channel record is created by an instructor once research is
    approved, so a channel cannot exist against unapproved research.
    """
    return render(
        request,
        "portal/channels.html",
        {
            "active_nav": "portal:channels",
            "student": student,
            "channels": YoutubeChannel.objects.for_actor(request.user).order_by("-created_at"),
            "research_approved": student.research_submissions.filter(
                status=SubmissionStatus.APPROVED
            ).exists(),
        },
    )


@with_student
def channel_detail(request, student, pk):
    channel = get_object_or_404(YoutubeChannel.objects.for_actor(request.user), pk=pk)
    return render(
        request,
        "portal/channel_detail.html",
        {
            "active_nav": "portal:channels",
            "student": student,
            "channel": channel,
            "availability": youtube_services.availability(channel),
            "videos": Video.objects.filter(channel=channel).order_by(
                F("published_at").desc(nulls_last=True)
            )[:10],
        },
    )


# --- Videos -----------------------------------------------------------------


@with_student
def videos(request, student):
    own_channels = YoutubeChannel.objects.for_actor(request.user)
    queryset = (
        Video.objects.filter(channel__in=own_channels)
        .select_related("channel")
        .annotate(latest_views=Max("snapshots__views", filter=Q(snapshots__source=MetricSource.PUBLIC_API)))
        .order_by(F("published_at").desc(nulls_last=True))
    )
    return render(
        request,
        "portal/videos.html",
        {
            "active_nav": "portal:videos",
            "student": student,
            "videos": queryset[:100],
            "total": queryset.count(),
            "has_channel": own_channels.exists(),
            "sync_configured": youtube_services.public_sync_available(),
        },
    )


@with_student
def video_detail(request, student, pk):
    video = get_object_or_404(
        Video.objects.filter(channel__in=YoutubeChannel.objects.for_actor(request.user)).select_related("channel"),
        pk=pk,
    )
    private = (
        video.snapshots.filter(source=MetricSource.ANALYTICS_API)
        .prefetch_related("traffic_sources")
        .first()
    )
    retention = (
        video.snapshots.filter(source=MetricSource.ANALYTICS_API, retention_points__isnull=False)
        .prefetch_related("retention_points")
        .distinct()
        .first()
    )
    return render(
        request,
        "portal/video_detail.html",
        {
            "active_nav": "portal:videos",
            "student": student,
            "video": video,
            "channel": video.channel,
            "public_snapshot": video.snapshots.filter(source=MetricSource.PUBLIC_API).first(),
            "analytics_snapshot": private,
            "traffic_sources": private.traffic_sources.all() if private else [],
            "retention_points": retention.retention_points.all() if retention else [],
            "availability": youtube_services.availability(video.channel),
        },
    )


# --- Analytics --------------------------------------------------------------


@with_student
def analytics(request, student):
    """
    The student's own channel metrics.

    Public figures come from the Data API; impressions, CTR, watch time and
    revenue need the channel owner's OAuth consent. Where consent is missing the
    page says so, rather than showing a zero that reads as a measurement.
    """
    rows = []
    for channel in YoutubeChannel.objects.for_actor(request.user).select_related("oauth_grant"):
        public_snapshot, private_snapshot = youtube_services.latest_snapshots(channel)
        rows.append(
            {
                "channel": channel,
                "availability": youtube_services.availability(channel),
                "public": public_snapshot,
                "private": private_snapshot,
            }
        )

    return render(
        request,
        "portal/analytics.html",
        {
            "active_nav": "portal:analytics",
            "student": student,
            "rows": rows,
            "connected": sum(1 for r in rows if r["availability"]["private_available"]),
            "sync_configured": youtube_services.public_sync_available(),
        },
    )


# --- Notifications ----------------------------------------------------------


@student_required
def notifications(request):
    from apps.monitoring.models import Notification

    own = Notification.objects.filter(user=request.user, channel="IN_APP")
    return render(
        request,
        "portal/notifications.html",
        {"active_nav": "portal:notifications", "notifications": own[:60]},
    )


@student_required
@require_POST
def mark_notifications_read(request):
    from apps.monitoring.models import Notification

    Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect("portal:notifications")


# --- Not built yet ----------------------------------------------------------

PORTAL_PLACEHOLDERS = {
    "agreements": (
        5,
        "My agreement",
        "Your batch completion document will appear here once Phase 5 is built. "
        "It is generated from your actual record, signed by you and the academy, "
        "and frozen at that point — a correction issues a new version rather than "
        "editing what you already signed.",
    ),
    "grievances": (
        5,
        "Raise a concern",
        "The complaints channel arrives in Phase 5. You will be able to raise a "
        "concern with supporting evidence, receive a reference number, and see the "
        "academy's response and resolution recorded against it.",
    ),
}


@student_required
def placeholder(request, section: str):
    phase, title, summary = PORTAL_PLACEHOLDERS[section]
    return render(
        request,
        "portal/placeholder.html",
        {"active_nav": f"portal:{section}", "phase": phase, "title": title, "summary": summary},
    )
