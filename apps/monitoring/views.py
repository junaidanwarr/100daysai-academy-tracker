from django.contrib import messages
from apps.core.access import staff_console
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.models import User
from apps.core.enums import AlertPriority, AlertStatus, UserRole
from apps.core.permissions import can
from apps.monitoring.models import Alert
from apps.monitoring.services import run_alert_scan, update_alert

OPEN_STATUSES = [AlertStatus.NEW, AlertStatus.IN_PROGRESS, AlertStatus.ESCALATED]


@staff_console
def alert_list(request):
    actor = request.user
    if not can(actor.role, "alert", "read"):
        raise PermissionDenied("Your role may not view alerts.")

    alerts = Alert.objects.for_actor(actor).select_related("student", "batch", "assigned_to", "rule")

    status = request.GET.get("status")
    priority = request.GET.get("priority")
    search = request.GET.get("search")

    if status:
        alerts = alerts.filter(status=status)
    else:
        # Default view is the work queue, not a wall of resolved history.
        alerts = alerts.filter(status__in=OPEN_STATUSES)
    if priority:
        alerts = alerts.filter(priority=priority)
    if search:
        from django.db.models import Q

        alerts = alerts.filter(
            Q(title__icontains=search)
            | Q(problem__icontains=search)
            | Q(student__full_name__icontains=search)
            | Q(student__enrollment_id__icontains=search)
        )

    scoped = Alert.objects.for_actor(actor)
    by_status = dict(scoped.values_list("status").annotate(n=Count("id")))

    return render(
        request,
        "monitoring/alert_list.html",
        {
            "alerts": alerts[:300],
            "counts": {
                "open": scoped.open().count(),
                "critical": scoped.open().filter(priority=AlertPriority.CRITICAL).count(),
                "new": by_status.get(AlertStatus.NEW, 0),
                "in_progress": by_status.get(AlertStatus.IN_PROGRESS, 0),
                "resolved": by_status.get(AlertStatus.RESOLVED, 0),
            },
            "statuses": AlertStatus.choices,
            "priorities": AlertPriority.choices,
            "filters": {"status": status or "", "priority": priority or "", "search": search or ""},
            "can_scan": actor.role == UserRole.SUPER_ADMIN,
        },
    )


@staff_console
def alert_detail(request, pk):
    actor = request.user
    if not can(actor.role, "alert", "read"):
        raise PermissionDenied("Your role may not view alerts.")

    alert = get_object_or_404(
        Alert.objects.for_actor(actor).select_related("student", "batch", "assigned_to", "resolved_by", "rule"), pk=pk
    )

    return render(
        request,
        "monitoring/alert_detail.html",
        {
            "alert": alert,
            "statuses": AlertStatus.choices,
            "priorities": AlertPriority.choices,
            "staff": User.objects.filter(
                role__in=[UserRole.INSTRUCTOR, UserRole.SUPER_ADMIN], is_active=True, deleted_at__isnull=True
            ),
            "can_update": can(actor.role, "alert", "update"),
        },
    )


@staff_console
@require_http_methods(["POST"])
def alert_update(request, pk):
    actor = request.user
    alert = get_object_or_404(Alert.objects.for_actor(actor), pk=pk)

    assignee_id = request.POST.get("assigned_to")
    assignee = User.objects.filter(pk=assignee_id).first() if assignee_id else None

    try:
        update_alert(
            actor,
            alert,
            status=request.POST.get("status") or None,
            assigned_to=assignee,
            priority=request.POST.get("priority") or None,
            resolution_note=request.POST.get("resolution_note") or None,
        )
        messages.success(request, "Alert updated.")
    except Exception as exc:  # noqa: BLE001
        messages.error(request, str(exc))

    return redirect("monitoring:alert_detail", pk=alert.pk)


@staff_console
@require_http_methods(["POST"])
def alert_scan(request):
    """
    Runs the same evaluation the scheduled job runs, so an admin can confirm a
    threshold change took effect without waiting for the next cycle.
    """
    if request.user.role != UserRole.SUPER_ADMIN:
        raise PermissionDenied("Only a Super Admin can run a scan manually.")

    result = run_alert_scan()
    note = f" {len(result.errors)} rule(s) errored." if result.errors else ""
    messages.success(
        request,
        f"Scan complete: {result.created} new alert(s), {result.suppressed} suppressed by cooldown, "
        f"{result.rules_evaluated} rule(s) evaluated.{note}",
    )
    return redirect("monitoring:alert_list")
