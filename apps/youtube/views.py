from django.conf import settings as dj_settings
from django.contrib import messages
from apps.core.access import staff_console
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from apps.core.audit import diff_fields, snapshot, write_audit
from apps.core.enums import AuditAction, ChannelStatus, MetricSource, SubmissionStatus, SyncStatus, VideoType
from apps.core.middleware import current_request_meta
from apps.core.permissions import can
from apps.youtube import oauth, services
from apps.youtube.api import YoutubeApiError
from apps.youtube.forms import ChannelForm
from apps.youtube.models import Video, YoutubeChannel

AUDITED_CHANNEL_FIELDS = [
    "channel_name", "channel_url", "youtube_channel_id", "niche", "sub_niche",
    "content_type", "status", "monetization_status", "ownership_verified",
]

# Metrics available from each source. Rendered on the channel page so a user is
# never left guessing whether a blank means "none" or "not connected".
PUBLIC_METRICS = ["Subscribers", "Total views", "Video count", "Last upload"]
PRIVATE_METRICS = [
    "Impressions", "Click-through rate", "Watch time", "Average view duration",
    "Average percentage viewed", "Audience retention", "Traffic sources", "Revenue",
]


@staff_console
def channel_list(request):
    actor = request.user
    if not can(actor.role, "channel", "read"):
        raise PermissionDenied("Your role may not view channels.")

    channels = (
        YoutubeChannel.objects.for_actor(actor)
        .select_related("student", "oauth_grant")
        .annotate(video_count=Count("videos", filter=Q(videos__deleted_at__isnull=True)))
    )

    search = request.GET.get("search")
    status = request.GET.get("status")

    if search:
        channels = channels.filter(
            Q(channel_name__icontains=search)
            | Q(channel_url__icontains=search)
            | Q(youtube_channel_id__icontains=search)
            | Q(student__full_name__icontains=search)
            | Q(student__enrollment_id__icontains=search)
        )
    if status:
        channels = channels.filter(status=status)

    return render(
        request,
        "youtube/channel_list.html",
        {
            "channels": channels,
            "statuses": ChannelStatus.choices,
            "filters": {"search": search or "", "status": status or ""},
            "can_create": can(actor.role, "channel", "create"),
        },
    )


@staff_console
def channel_detail(request, pk):
    actor = request.user
    if not can(actor.role, "channel", "read"):
        raise PermissionDenied("Your role may not view channels.")

    channel = get_object_or_404(
        YoutubeChannel.objects.for_actor(actor).select_related("student", "oauth_grant"), pk=pk
    )
    public_snapshot, private_snapshot = services.latest_snapshots(channel)

    return render(
        request,
        "youtube/channel_detail.html",
        {
            "channel": channel,
            "latest_snapshot": public_snapshot,
            "analytics_snapshot": private_snapshot,
            "availability": services.availability(channel),
            "growth": services.growth_summary(channel),
            "last_successful_sync": channel.sync_runs.filter(status=SyncStatus.SUCCESS).first(),
            "recent_runs": channel.sync_runs.all()[:5],
            "videos": channel.videos.all()[:10],
            "public_metrics": PUBLIC_METRICS,
            "private_metrics": PRIVATE_METRICS,
            "can_update": can(actor.role, "channel", "update"),
            "sync_configured": services.public_sync_available(),
            "oauth_configured": oauth.is_configured(),
            "quota_remaining": services.quota_remaining_today(),
        },
    )


@staff_console
@require_http_methods(["GET", "POST"])
def channel_create(request):
    actor = request.user
    if not can(actor.role, "channel", "create"):
        raise PermissionDenied("Your role may not add channels.")

    can_override = can(actor.role, "channel", "override")
    form = ChannelForm(request.POST or None, actor=actor, can_override=can_override)

    if request.POST and "student" not in request.POST and request.GET.get("student"):
        form = ChannelForm(actor=actor, can_override=can_override, initial={"student": request.GET["student"]})
    elif request.method == "GET" and request.GET.get("student"):
        form = ChannelForm(actor=actor, can_override=can_override, initial={"student": request.GET["student"]})

    if request.method == "POST" and form.is_valid():
        channel = form.save(commit=False)

        # A channel may be recorded at any time, but it cannot be marked
        # APPROVED before the student's research is approved (spec 23.3).
        research_approved = channel.student.research_submissions.filter(status=SubmissionStatus.APPROVED).exists()
        downgraded = False
        if channel.status in (ChannelStatus.APPROVED, ChannelStatus.ACTIVE, ChannelStatus.MONETIZED) and not research_approved:
            channel.status = ChannelStatus.PENDING
            downgraded = True

        channel.save()

        override_reason = form.cleaned_data.get("link_override_reason")
        write_audit(
            actor=actor,
            action=AuditAction.OVERRIDE if override_reason else AuditAction.CREATE,
            entity_type="YoutubeChannel",
            entity_id=channel.pk,
            summary=(
                f'Added channel "{channel.channel_name}" for {channel.student.full_name} '
                f"({channel.student.enrollment_id})"
                + (f" — link override: {override_reason}" if override_reason else "")
                + (" — status held at PENDING until research is approved" if downgraded else "")
            ),
            after={"channel_name": channel.channel_name, "status": channel.status},
            **current_request_meta(),
        )

        if downgraded:
            messages.warning(
                request,
                "Channel saved, but held at Pending: this student has no approved research submission yet.",
            )
        else:
            messages.success(request, f"Added {channel.channel_name}.")
        return redirect(channel.get_absolute_url())

    return render(request, "youtube/channel_form.html", {"form": form, "is_edit": False})


@staff_console
@require_http_methods(["GET", "POST"])
def channel_edit(request, pk):
    actor = request.user
    if not can(actor.role, "channel", "update"):
        raise PermissionDenied("Your role may not edit channels.")

    channel = get_object_or_404(YoutubeChannel.objects.for_actor(actor), pk=pk)
    before = snapshot(channel, AUDITED_CHANNEL_FIELDS)
    form = ChannelForm(
        request.POST or None, instance=channel, actor=actor, can_override=can(actor.role, "channel", "override")
    )

    if request.method == "POST" and form.is_valid():
        channel = form.save()
        changed_before, changed_after = diff_fields(before, snapshot(channel, AUDITED_CHANNEL_FIELDS))
        write_audit(
            actor=actor,
            action=AuditAction.UPDATE,
            entity_type="YoutubeChannel",
            entity_id=channel.pk,
            summary=f'Updated channel "{channel.channel_name}"',
            before=changed_before,
            after=changed_after,
            **current_request_meta(),
        )
        messages.success(request, "Channel updated.")
        return redirect(channel.get_absolute_url())

    return render(request, "youtube/channel_form.html", {"form": form, "is_edit": True, "channel": channel})


# --- Synchronisation --------------------------------------------------------


@staff_console
@require_POST
def channel_sync(request, pk):
    """Manual sync. It spends quota and changes stored figures, so it is a POST and it is audited."""
    actor = request.user
    channel = get_object_or_404(YoutubeChannel.objects.for_actor(actor), pk=pk)
    if not can(actor.role, "channel", "update"):
        raise PermissionDenied("Your role may not synchronise channels.")

    try:
        outcome = services.sync_now(actor, channel)
    except services.SyncNotConfigured as exc:
        messages.warning(request, str(exc))
        return redirect(channel.get_absolute_url())

    if outcome.status == SyncStatus.FAILED:
        messages.error(request, f"Sync failed: {outcome.error}")
    elif outcome.status == SyncStatus.PARTIAL:
        messages.warning(
            request,
            f"Partly synced: {outcome.videos_seen} video(s) updated, but some data could not be fetched. "
            "The channel page shows what is missing and why.",
        )
    else:
        detail = "public figures and analytics" if outcome.analytics else "public figures only"
        messages.success(
            request, f"Synced {outcome.videos_seen} video(s) — {detail}. Quota used: {outcome.quota_used}."
        )
    return redirect(channel.get_absolute_url())


# --- OAuth consent ----------------------------------------------------------


@staff_console
@require_http_methods(["GET", "POST"])
def analytics_connect(request, pk):
    """
    The consent screen. Shown in plain language before anyone is sent to Google:
    a button labelled "authorize" is not informed consent.
    """
    actor = request.user
    channel = get_object_or_404(YoutubeChannel.objects.for_actor(actor), pk=pk)
    if not can(actor.role, "channel", "update"):
        raise PermissionDenied("Your role may not connect analytics for this channel.")

    if request.method == "POST":
        try:
            return redirect(services.start_connection(actor, channel))
        except (services.SyncNotConfigured, oauth.OauthNotConfigured) as exc:
            messages.warning(request, str(exc))
            return redirect(channel.get_absolute_url())

    return render(
        request,
        "youtube/analytics_connect.html",
        {
            "channel": channel,
            "oauth_configured": oauth.is_configured(),
            "availability": services.availability(channel),
            **oauth.consent_context(include_revenue=services.track_revenue()),
        },
    )


@staff_console
def analytics_callback(request):
    """
    Google's redirect target. The channel comes from the signed state, never
    from a query parameter the caller could choose.
    """
    try:
        payload = oauth.read_state(request.GET.get("state", ""))
    except oauth.OauthStateError as exc:
        messages.error(request, str(exc))
        return redirect("youtube:channel_list")

    channel = get_object_or_404(YoutubeChannel.objects.for_actor(request.user), pk=payload["channel"])

    if request.GET.get("error"):
        messages.info(
            request,
            "Analytics access was not granted, so only public figures will be available. "
            "You can connect at any time.",
        )
        return redirect(channel.get_absolute_url())

    code = request.GET.get("code", "")
    if not code:
        messages.error(request, "Google did not return an authorization code. Please try again.")
        return redirect(channel.get_absolute_url())

    try:
        services.complete_connection(
            request.user, channel, code, ip_address=current_request_meta().get("ip_address")
        )
    except (services.SyncNotConfigured, oauth.OauthNotConfigured) as exc:
        messages.error(request, str(exc))
        return redirect(channel.get_absolute_url())
    except YoutubeApiError as exc:
        messages.error(request, f"Google rejected the authorization: {exc}")
        return redirect(channel.get_absolute_url())

    messages.success(
        request,
        "Analytics connected. Private metrics appear after the next sync, and you can "
        "disconnect at any time from this page.",
    )
    return redirect(channel.get_absolute_url())


@staff_console
@require_POST
def analytics_disconnect(request, pk):
    actor = request.user
    channel = get_object_or_404(YoutubeChannel.objects.for_actor(actor), pk=pk)
    if not can(actor.role, "channel", "update"):
        raise PermissionDenied("Your role may not disconnect analytics for this channel.")

    reason = (request.POST.get("reason") or "").strip() or "Disconnected by the account holder."
    if services.disconnect(actor, channel, reason=reason):
        messages.success(
            request,
            "Analytics disconnected. Figures already collected are kept as a record of what was true "
            "at the time; no new private metrics will be fetched.",
        )
    else:
        messages.info(request, "There was no active analytics connection to disconnect.")
    return redirect(channel.get_absolute_url())


# --- Videos -----------------------------------------------------------------


@staff_console
def video_list(request):
    actor = request.user
    if not can(actor.role, "video", "read"):
        raise PermissionDenied("Your role may not view videos.")

    videos = (
        Video.objects.filter(channel__in=YoutubeChannel.objects.for_actor(actor))
        .select_related("channel", "channel__student")
        .annotate(latest_views=Max("snapshots__views", filter=Q(snapshots__source=MetricSource.PUBLIC_API)))
        # Explicit, and nulls last: a video with no publish date recorded should
        # not head the list, and pagination needs a deterministic order.
        .order_by(F("published_at").desc(nulls_last=True))
    )

    search = request.GET.get("search")
    video_type = request.GET.get("type")
    channel_id = request.GET.get("channel")

    if search:
        videos = videos.filter(
            Q(title__icontains=search)
            | Q(channel__channel_name__icontains=search)
            | Q(channel__student__full_name__icontains=search)
        )
    if video_type:
        videos = videos.filter(video_type=video_type)
    if channel_id:
        videos = videos.filter(channel_id=channel_id)

    return render(
        request,
        "youtube/video_list.html",
        {
            "page": Paginator(videos, 50).get_page(request.GET.get("page")),
            "types": VideoType.choices,
            "filters": {"search": search or "", "type": video_type or "", "channel": channel_id or ""},
            "sync_configured": services.public_sync_available(),
        },
    )


@staff_console
def video_detail(request, pk):
    actor = request.user
    if not can(actor.role, "video", "read"):
        raise PermissionDenied("Your role may not view videos.")

    video = get_object_or_404(
        Video.objects.filter(channel__in=YoutubeChannel.objects.for_actor(actor)).select_related(
            "channel", "channel__student"
        ),
        pk=pk,
    )
    private = (
        video.snapshots.filter(source=MetricSource.ANALYTICS_API)
        .prefetch_related("traffic_sources")
        .first()
    )
    # Retention is fetched separately and lands on its own snapshot, so the
    # newest analytics row is not necessarily the one holding a curve.
    retention = (
        video.snapshots.filter(source=MetricSource.ANALYTICS_API, retention_points__isnull=False)
        .prefetch_related("retention_points")
        .distinct()
        .first()
    )

    return render(
        request,
        "youtube/video_detail.html",
        {
            "video": video,
            "channel": video.channel,
            "public_snapshot": video.snapshots.filter(source=MetricSource.PUBLIC_API).first(),
            "analytics_snapshot": private,
            "traffic_sources": private.traffic_sources.all() if private else [],
            "retention_points": retention.retention_points.all() if retention else [],
            "availability": services.availability(video.channel),
            "can_fetch_retention": can(actor.role, "channel", "update"),
        },
    )


@staff_console
@require_POST
def video_retention(request, pk):
    """One Analytics report per video, so it is fetched deliberately rather than on every sync."""
    actor = request.user
    video = get_object_or_404(Video.objects.filter(channel__in=YoutubeChannel.objects.for_actor(actor)), pk=pk)
    if not can(actor.role, "channel", "update"):
        raise PermissionDenied("Your role may not fetch retention data.")

    try:
        points = services.sync_retention(actor, video)
    except services.SyncNotConfigured as exc:
        messages.warning(request, str(exc))
        return redirect("youtube:video_detail", pk=video.pk)
    except YoutubeApiError as exc:
        messages.error(request, f"Retention could not be fetched: {exc}")
        return redirect("youtube:video_detail", pk=video.pk)

    if points:
        messages.success(request, f"Fetched {points} retention point(s).")
    else:
        messages.info(
            request,
            "YouTube returned no retention curve for this video. That normally means it has too few "
            "views for YouTube to report one.",
        )
    return redirect("youtube:video_detail", pk=video.pk)


# --- Analytics overview -----------------------------------------------------


@staff_console
def analytics_overview(request):
    """
    Synchronisation status across every channel in scope: what is linked, what
    is authorized, when each was last synced, and what is left of today's quota.
    """
    actor = request.user
    if not can(actor.role, "analytics", "read"):
        raise PermissionDenied("Your role may not view analytics.")

    channels = (
        YoutubeChannel.objects.for_actor(actor)
        .select_related("student", "oauth_grant")
        .order_by("-last_synced_at")
    )

    rows = []
    connected = linked = 0
    for channel in channels:
        state = services.availability(channel)
        linked += bool(channel.youtube_channel_id)
        connected += bool(state["private_available"])
        public_snapshot, private_snapshot = services.latest_snapshots(channel)
        rows.append(
            {
                "channel": channel,
                "availability": state,
                "public": public_snapshot,
                "private": private_snapshot,
                "last_run": channel.sync_runs.first(),
            }
        )

    return render(
        request,
        "youtube/analytics_overview.html",
        {
            "rows": rows,
            "total": len(rows),
            "linked": linked,
            "connected": connected,
            "sync_configured": services.public_sync_available(),
            "oauth_configured": oauth.is_configured(),
            "cadence": services.cadence().replace("_", " ").lower(),
            "quota_used": services.quota_used_today(),
            "quota_ceiling": dj_settings.YOUTUBE_DAILY_QUOTA_UNITS,
            "quota_remaining": services.quota_remaining_today(),
            "private_metrics": PRIVATE_METRICS,
        },
    )
