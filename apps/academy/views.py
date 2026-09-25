from django.contrib import messages
from apps.core.access import staff_console
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.academy.activity import activity_summary, filtered_activities, log_manual_activity
from apps.academy.filters import STUDENT_PRESETS, get_preset
from apps.academy.forms import ActivityLogForm, BatchForm, StatusChangeForm, StudentForm
from apps.academy.models import Batch, Student
from apps.academy.services import (
    change_status,
    create_student,
    filtered_students,
    preset_counts,
    update_student,
)
from apps.academy.status import ROADMAP_ORDER, allowed_transitions
from apps.core.enums import ActivityKind, StudentStatus, SubmissionStatus, UserRole
from apps.core.permissions import can
from apps.core.settings_service import get_int, RESEARCH_DAYS
from apps.academy.status import InvalidTransition


@staff_console
def student_list(request):
    actor = request.user
    if not can(actor.role, "student", "read"):
        raise PermissionDenied("Your role may not view students.")

    preset = request.GET.get("preset") or None
    queryset = filtered_students(
        actor,
        preset=preset,
        search=request.GET.get("search") or None,
        batch_id=request.GET.get("batch") or None,
        status=request.GET.get("status") or None,
    )

    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    active_preset = get_preset(preset) if preset else None

    return render(
        request,
        "academy/student_list.html",
        {
            "page_obj": page,
            "presets": STUDENT_PRESETS,
            "preset_counts": preset_counts(actor),
            "active_preset": active_preset,
            "batches": Batch.objects.all(),
            "statuses": StudentStatus.choices,
            "filters": {
                "search": request.GET.get("search", ""),
                "batch": request.GET.get("batch", ""),
                "status": request.GET.get("status", ""),
                "preset": preset or "",
            },
            "can_create": can(actor.role, "student", "create"),
        },
    )


@staff_console
def student_detail(request, pk):
    actor = request.user
    if not can(actor.role, "student", "read"):
        raise PermissionDenied("Your role may not view students.")

    student = get_object_or_404(
        Student.objects.for_actor(actor).select_related("batch", "instructor", "user"), pk=pk
    )

    status_form = None
    if can(actor.role, "student", "update"):
        status_form = StatusChangeForm(
            allowed=allowed_transitions(student.status),
            can_override=can(actor.role, "student", "override"),
        )

    return render(
        request,
        "academy/student_detail.html",
        {
            "student": student,
            "status_form": status_form,
            "channels": student.channels.all(),
            "submissions": student.research_submissions.all(),
            "assignment_attempts": student.assignment_submissions.select_related("assignment").order_by(
                "-submitted_at", "-created_at"
            )[:20],
            "activities": student.activities.select_related("actor")[:15],
            "activity_count": student.activities.count(),
            "activity_form": ActivityLogForm() if can(actor.role, "student", "update") else None,
            "history": student.status_history.select_related("changed_by")[:50],
            "open_alerts": student.alerts.filter(status__in=["NEW", "IN_PROGRESS", "ESCALATED"]),
            "can_update": can(actor.role, "student", "update"),
        },
    )


@staff_console
@require_http_methods(["GET", "POST"])
def student_create(request):
    if not can(request.user.role, "student", "create"):
        raise PermissionDenied("Your role may not enroll students.")

    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student = create_student(request.user, form.cleaned_data)
        messages.success(request, f"Enrolled {student.full_name} as {student.enrollment_id}.")
        return redirect(student.get_absolute_url())

    return render(
        request,
        "academy/student_form.html",
        {"form": form, "is_edit": False, "default_research_days": get_int(RESEARCH_DAYS)},
    )


@staff_console
@require_http_methods(["GET", "POST"])
def student_edit(request, pk):
    actor = request.user
    if not can(actor.role, "student", "update"):
        raise PermissionDenied("Your role may not edit students.")

    student = get_object_or_404(Student.objects.for_actor(actor), pk=pk)
    form = StudentForm(request.POST or None, instance=student)

    if request.method == "POST" and form.is_valid():
        update_student(actor, student, form.cleaned_data)
        messages.success(request, "Student updated.")
        return redirect(student.get_absolute_url())

    return render(
        request,
        "academy/student_form.html",
        {"form": form, "is_edit": True, "student": student, "default_research_days": get_int(RESEARCH_DAYS)},
    )


@staff_console
@require_http_methods(["POST"])
def student_change_status(request, pk):
    actor = request.user
    student = get_object_or_404(Student.objects.for_actor(actor), pk=pk)

    form = StatusChangeForm(
        request.POST,
        allowed=allowed_transitions(student.status),
        can_override=can(actor.role, "student", "override"),
    )

    if form.is_valid():
        try:
            change_status(
                actor,
                student,
                form.cleaned_data["to_status"],
                reason=form.cleaned_data.get("reason") or None,
                override=form.cleaned_data.get("override", False),
            )
            messages.success(request, "Status updated.")
        except InvalidTransition as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Select a valid status.")

    return redirect(student.get_absolute_url())


@staff_console
def batch_list(request):
    actor = request.user
    if not can(actor.role, "batch", "read"):
        raise PermissionDenied("Your role may not view batches.")

    batches = Batch.objects.select_related("instructor").annotate(
        student_count=Count("students", filter=Q(students__deleted_at__isnull=True))
    )
    if actor.role == UserRole.INSTRUCTOR:
        batches = batches.filter(instructor=actor)

    return render(
        request,
        "academy/batch_list.html",
        {
            "batches": batches,
            "default_research_days": get_int(RESEARCH_DAYS),
            "can_create": can(actor.role, "batch", "create"),
        },
    )


@staff_console
def batch_detail(request, pk):
    actor = request.user
    if not can(actor.role, "batch", "read"):
        raise PermissionDenied("Your role may not view batches.")

    batch = get_object_or_404(Batch.objects.select_related("instructor"), pk=pk)
    if actor.role == UserRole.INSTRUCTOR and batch.instructor_id != actor.pk:
        raise PermissionDenied("That batch is not assigned to you.")

    students = batch.students.filter(deleted_at__isnull=True).annotate(
        channel_count=Count("channels", filter=Q(channels__deleted_at__isnull=True))
    )

    approved = batch.students.filter(research_submissions__status=SubmissionStatus.APPROVED).distinct().count()
    rejected = batch.students.filter(
        research_submissions__status__in=[SubmissionStatus.REJECTED, SubmissionStatus.REVISION_REQUESTED]
    ).distinct().count()
    reviewed = approved + rejected
    total = students.count()
    completed = students.filter(status=StudentStatus.BATCH_COMPLETED).count()
    channels_created = sum(s.channel_count for s in students)

    return render(
        request,
        "academy/batch_detail.html",
        {
            "batch": batch,
            "students": students,
            "stats": {
                "total": total,
                "at_risk": students.filter(status=StudentStatus.AT_RISK).count(),
                "completed": completed,
                "channels_created": channels_created,
                "approval_rate": round(approved / reviewed * 100) if reviewed else None,
                "channel_rate": round(channels_created / total * 100) if total else None,
                "completion_rate": round(completed / total * 100) if total else None,
            },
        },
    )


@staff_console
@require_http_methods(["GET", "POST"])
def batch_create(request):
    if not can(request.user.role, "batch", "create"):
        raise PermissionDenied("Your role may not create batches.")

    form = BatchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        batch = form.save()
        messages.success(request, f"Created batch {batch.code}.")
        return redirect("academy:batch_detail", pk=batch.pk)

    return render(
        request,
        "academy/batch_form.html",
        {"form": form, "default_research_days": get_int(RESEARCH_DAYS)},
    )


@staff_console
@require_http_methods(["POST"])
def student_log_activity(request, pk):
    actor = request.user
    if not can(actor.role, "student", "update"):
        raise PermissionDenied("Your role may not log activity.")

    student = get_object_or_404(Student.objects.for_actor(actor), pk=pk)
    form = ActivityLogForm(request.POST)
    if form.is_valid():
        activity = log_manual_activity(
            actor,
            student,
            kind=form.cleaned_data["kind"],
            summary=form.cleaned_data["summary"],
            detail=form.cleaned_data.get("detail") or None,
            occurred_at=form.cleaned_data.get("occurred_at"),
        )
        messages.success(request, f"Logged: {activity.get_kind_display().lower()}.")
    else:
        errors = "; ".join(e for errs in form.errors.values() for e in errs)
        messages.error(request, f"Activity not logged. {errors}")
    return redirect(student.get_absolute_url() + "#activity")


ACTIVITY_WINDOWS = [(1, "Last 24 hours"), (7, "Last 7 days"), (30, "Last 30 days"), (90, "Last 90 days")]


@staff_console
def activity_feed(request):
    """Everything every student in scope has done, and everything done for them."""
    actor = request.user
    if not can(actor.role, "student", "read"):
        raise PermissionDenied("Your role may not view student activity.")

    try:
        days = int(request.GET.get("days") or 7)
    except ValueError:
        days = 7
    if days not in dict(ACTIVITY_WINDOWS):
        days = 7

    student = None
    student_id = request.GET.get("student") or None
    if student_id:
        student = Student.objects.for_actor(actor).filter(pk=student_id).first()
        if student is None:
            raise PermissionDenied("That student is not in your scope.")

    batch_id = request.GET.get("batch") or None
    queryset = filtered_activities(
        actor,
        student_id=student_id,
        batch_id=batch_id,
        kind=request.GET.get("kind") or None,
        # A single student's page shows their whole history unless narrowed.
        days=None if student and not request.GET.get("days") else days,
        search=request.GET.get("search") or None,
        actor_filter=request.GET.get("by") or None,
    )
    page = Paginator(queryset, 50).get_page(request.GET.get("page"))

    batches = Batch.objects.all()
    if actor.role == UserRole.INSTRUCTOR:
        batches = batches.filter(Q(instructor=actor) | Q(students__instructor=actor)).distinct()

    query = request.GET.copy()
    query.pop("page", None)

    return render(
        request,
        "academy/activity_feed.html",
        {
            "page_obj": page,
            "summary": None if student else activity_summary(actor, days=days, batch_id=batch_id),
            "student": student,
            "batches": batches,
            "kinds": ActivityKind.choices,
            "windows": ACTIVITY_WINDOWS,
            "days": days,
            "querystring": query.urlencode(),
            "filters": {
                "search": request.GET.get("search", ""),
                "batch": batch_id or "",
                "kind": request.GET.get("kind", ""),
                "by": request.GET.get("by", ""),
                "days": request.GET.get("days", ""),
            },
        },
    )
