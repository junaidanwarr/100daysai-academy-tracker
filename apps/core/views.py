"""Dashboard, settings, audit log, staff, phase placeholders and the cron endpoint."""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib import messages
from apps.core.access import staff_console
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.academy.models import Batch, Student
from apps.academy.status import ROADMAP_ORDER
from apps.core.crypto import safe_equal
from apps.core.enums import AuditAction, ChannelStatus, StudentStatus, SubmissionStatus, UserRole
from apps.core.models import AuditLog, SystemSetting
from apps.core.permissions import can, can_access
from apps.core.settings_service import (
    INACTIVITY_DAYS,
    NO_UPLOAD_DAYS,
    feature_flags,
    get_int,
    update_setting,
)
from apps.monitoring.models import Alert, AlertRule
from apps.youtube.models import Video, YoutubeChannel


@staff_console
def dashboard(request):
    """Administration dashboard aggregates (specification section 13)."""
    actor = request.user
    if actor.role == UserRole.STUDENT:
        return redirect("academy:portal")

    today = timezone.localdate()
    now = timezone.now()
    inactivity_days = get_int(INACTIVITY_DAYS)
    no_upload_days = get_int(NO_UPLOAD_DAYS)

    students = Student.objects.for_actor(actor)
    channels = YoutubeChannel.objects.for_actor(actor)

    research_approved = students.filter(research_submissions__status=SubmissionStatus.APPROVED).distinct().count()
    research_rejected = students.filter(
        research_submissions__status__in=[SubmissionStatus.REJECTED, SubmissionStatus.REVISION_REQUESTED]
    ).distinct().count()
    reviewed_total = research_approved + research_rejected

    total_students = students.count()
    channels_created = channels.count()
    completed = students.filter(status=StudentStatus.BATCH_COMPLETED).count()

    stage_counts = dict(students.values_list("roadmap_stage").annotate(n=Count("id")))
    status_counts = sorted(
        [{"status": row["status"], "count": row["n"]} for row in students.values("status").annotate(n=Count("id"))],
        key=lambda r: -r["count"],
    )

    counters = {
        "total_students": total_students,
        "active_students": students.filter(
            status__in=[StudentStatus.ACTIVE, StudentStatus.CONTENT_PRODUCTION_STARTED]
        ).count(),
        "in_research": students.filter(
            status__in=[StudentStatus.RESEARCH_PENDING, StudentStatus.RESEARCH_IN_PROGRESS]
        ).count(),
        "pending_review": students.filter(
            research_submissions__status__in=[
                SubmissionStatus.SUBMITTED, SubmissionStatus.UNDER_REVIEW, SubmissionStatus.RESUBMITTED
            ]
        ).distinct().count(),
        "research_approved": research_approved,
        "research_rejected": research_rejected,
        "channels_created": channels_created,
        "active_channels": channels.filter(status__in=[ChannelStatus.ACTIVE, ChannelStatus.APPROVED]).count(),
        "monetized_channels": channels.filter(status=ChannelStatus.MONETIZED).count(),
        "total_videos": Video.objects.filter(channel__in=channels).count(),
        "no_recent_uploads": channels.filter(
            Q(last_upload_at__isnull=True) | Q(last_upload_at__lt=now - timedelta(days=no_upload_days)),
            status__in=[ChannelStatus.ACTIVE, ChannelStatus.APPROVED, ChannelStatus.MONETIZED],
        ).count(),
        "at_risk": students.filter(status=StudentStatus.AT_RISK).count(),
        "completed": completed,
        "overdue_deadlines": students.filter(
            research_deadline__lt=today,
            status__in=[
                StudentStatus.ENROLLED, StudentStatus.RESEARCH_PENDING,
                StudentStatus.RESEARCH_IN_PROGRESS, StudentStatus.REVISION_REQUIRED,
            ],
        ).count(),
        "open_alerts": Alert.objects.for_actor(actor).open().count(),
        "inactive_students": students.tracked().filter(
            Q(last_activity_at__lt=now - timedelta(days=inactivity_days))
            | Q(last_activity_at__isnull=True, enrollment_date__lt=today - timedelta(days=inactivity_days))
        ).count(),
    }

    context = {
        "counters": counters,
        # Null rather than 0 when nothing has been reviewed — a 0% approval rate
        # and "no submissions yet" are very different facts.
        "research_approval_rate": round(research_approved / reviewed_total * 100) if reviewed_total else None,
        "channel_creation_rate": round(channels_created / total_students * 100) if total_students else None,
        "completion_rate": round(completed / total_students * 100) if total_students else None,
        "stage_chart": [
            {"label": stage.replace("_", " ").title(), "count": stage_counts.get(stage, 0)}
            for stage in ROADMAP_ORDER
        ],
        "status_chart": status_counts[:8],
        "upcoming_deadlines": students.filter(
            research_deadline__gte=today,
            research_deadline__lte=today + timedelta(days=7),
            status__in=[
                StudentStatus.ENROLLED, StudentStatus.RESEARCH_PENDING,
                StudentStatus.RESEARCH_IN_PROGRESS, StudentStatus.REVISION_REQUIRED,
            ],
        ).order_by("research_deadline")[:10],
        "recent_alerts": Alert.objects.for_actor(actor).open().select_related("student")[:8],
        "inactivity_days": inactivity_days,
        "no_upload_days": no_upload_days,
        "scope_note": {
            UserRole.INSTRUCTOR: "Showing students assigned to you.",
            UserRole.MANAGEMENT_READONLY: "Read-only view across the whole academy.",
        }.get(actor.role, "Across the whole academy."),
    }
    return render(request, "core/dashboard.html", context)


@staff_console
@require_http_methods(["GET", "POST"])
def settings_view(request):
    if not can_access(request.user.role, "setting"):
        raise PermissionDenied("Your role may not view settings.")

    if request.method == "POST":
        key = request.POST.get("key", "")
        try:
            update_setting(request.user, key, request.POST.get("value", ""))
            messages.success(request, "Saved. Deadlines are re-dated on the next scheduled run.")
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f"Could not save that setting: {exc}")
        return redirect("core:settings")

    grouped: dict[str, list] = {}
    for setting in SystemSetting.objects.all():
        grouped.setdefault(setting.category, []).append(setting)

    return render(
        request,
        "core/settings.html",
        {
            "grouped_settings": sorted(grouped.items()),
            "alert_rules": AlertRule.objects.all(),
            "flags": feature_flags(),
            "editable": can(request.user.role, "setting", "configure"),
        },
    )


@staff_console
def audit_log(request):
    if not can_access(request.user.role, "audit"):
        raise PermissionDenied("Your role may not view the audit log.")

    logs = AuditLog.objects.select_related("actor")
    action = request.GET.get("action")
    entity = request.GET.get("entity_type")

    if action:
        logs = logs.filter(action=action)
    if entity:
        logs = logs.filter(entity_type=entity)

    page = Paginator(logs, 50).get_page(request.GET.get("page"))

    return render(
        request,
        "core/audit_log.html",
        {
            "page_obj": page,
            "actions": AuditAction.choices,
            "entity_types": AuditLog.objects.values_list("entity_type", flat=True).distinct()[:40],
            "selected_action": action,
            "selected_entity": entity,
        },
    )


@staff_console
def staff(request):
    if not can_access(request.user.role, "staff"):
        raise PermissionDenied("Your role may not view staff accounts.")

    from apps.accounts.models import User

    users = (
        User.objects.filter(deleted_at__isnull=True)
        .select_related("mfa")
        .annotate(
            student_count=Count("assigned_students", filter=Q(assigned_students__deleted_at__isnull=True), distinct=True),
            batch_count=Count("led_batches", distinct=True),
        )
        .order_by("role", "full_name")
    )
    return render(request, "core/staff.html", {"users": users})


@staff_console
def notifications(request):
    from apps.monitoring.models import Notification

    if request.method == "POST":
        Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        return redirect("core:notifications")

    delivery = (
        Notification.objects.filter(user=request.user)
        .exclude(channel="IN_APP")
        .values("channel", "delivery_status")
        .annotate(n=Count("id"))
    )
    return render(
        request,
        "core/notifications.html",
        {
            "notifications": Notification.objects.filter(user=request.user, channel="IN_APP")[:60],
            "delivery": delivery,
        },
    )


# --- Phase placeholders -----------------------------------------------------

PHASE_SECTIONS = {
    # Videos and Analytics were placeholders until Phase 3; they are now served
    # by apps.youtube and no longer belong here.
    "performance": (4, "Performance", "Weighted student scoring and target tracking. Scores are explainable: each one shows the factors that produced it, their weights, and any factor excluded because the data was not available.", [
        "Admin-weighted scoring factors covering research, consistency, uploads and engagement",
        "Bands: Excellent, Satisfactory, Needs Improvement, At Risk, Unsatisfactory",
        "Per-factor breakdown explaining why a student received their score",
        "Targets per batch, student or channel with expected versus actual comparison",
    ]),
    "reports": (4, "Reports", "Filtered reporting with PDF, Excel and CSV export. Every export is written to the audit log, including who ran it and what filter produced it.", [
        "Report by student, batch, instructor, status, niche, country or date range",
        "Student progress, research evaluation and assignment history reports",
        "Channel analytics, video performance and batch performance reports",
        "PDF, Excel, CSV and a printable view",
    ]),
    "agreements": (5, "Agreements", "Final batch completion and acknowledgment documents. Each is a factual, version-controlled record of the training delivered and the work completed. Once signed it becomes read-only; a correction issues a new version rather than editing the old one.", [
        "Completion document generated from the student's actual record, frozen at issue time",
        "Student, instructor and academy representative signatures with timestamps",
        "PDF generation with a content hash for tamper evidence",
        "Neutral acknowledgment wording plus grievance and dispute-resolution clauses",
    ]),
    "grievances": (5, "Grievances", "Complaint intake and communication records, protecting both the student and the academy by keeping a transparent, timestamped account of what was raised and what was done about it.", [
        "Student-submitted complaints with category, evidence and a reference number",
        "Assignment to a staff member, academy response and recorded resolution",
        "Student acknowledgment and an appeal path",
        "Communication log covering feedback, warnings, meetings and action plans",
    ]),
}


@staff_console
def phase_placeholder(request, section: str):
    phase, title, summary, will_include = PHASE_SECTIONS[section]
    return render(
        request,
        "core/phase_placeholder.html",
        {"phase": phase, "title": title, "summary": summary, "will_include": will_include},
    )


# --- Cron endpoint ----------------------------------------------------------


@csrf_exempt
@require_POST
def cron_endpoint(request, job: str):
    """
    HTTP trigger for background jobs, for hosts without a persistent worker.
    The handlers are the same functions the scheduler calls.
    """
    from django.conf import settings as dj
    from apps.monitoring.jobs import JOBS

    secret = dj.CRON_SECRET
    # Refusing when unset is deliberate: an unauthenticated endpoint that can
    # mutate student records is worse than one that does not work yet.
    if not secret:
        return JsonResponse({"error": "CRON_SECRET is not configured on the server."}, status=401)

    header = request.headers.get("Authorization", "")
    supplied = header[7:] if header.startswith("Bearer ") else request.headers.get("X-Cron-Secret", "")

    if not supplied or not safe_equal(supplied, secret):
        return JsonResponse({"error": "Invalid cron secret."}, status=401)

    if job not in JOBS:
        return JsonResponse({"error": f'Unknown job "{job}".', "available": list(JOBS)}, status=404)

    started = timezone.now()
    try:
        result = JOBS[job]()
        duration = int((timezone.now() - started).total_seconds() * 1000)
        return JsonResponse({"job": job, "ok": True, "duration_ms": duration, "result": result})
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"job": job, "ok": False, "error": str(exc)}, status=500)
