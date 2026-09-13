import logging

from django.contrib import messages
from apps.core.access import staff_console
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.assignments.lms import CSV_COLUMNS, get_adapter, parse_csv, webhook_adapter
from apps.assignments.models import Assignment, LmsConnection
from apps.assignments.services import ingest_submissions, scoped_submissions
from apps.core.context import system_actor
from apps.core.enums import LmsProvider, SubmissionStatus
from apps.core.permissions import can

logger = logging.getLogger(__name__)

AWAITING = [SubmissionStatus.SUBMITTED, SubmissionStatus.UNDER_REVIEW, SubmissionStatus.RESUBMITTED]


@staff_console
@require_http_methods(["GET", "POST"])
def assignment_list(request):
    actor = request.user
    if not can(actor.role, "assignment", "read"):
        raise PermissionDenied("Your role may not view assignments.")

    can_import = can(actor.role, "assignment", "create")

    if request.method == "POST":
        if not can_import:
            raise PermissionDenied("Your role may not import assignments.")

        content = (request.POST.get("csv") or "").strip()
        if not content:
            messages.error(request, "Paste CSV content, or choose a file.")
            return redirect("assignments:assignment_list")

        rows, errors = parse_csv(content)
        if not rows:
            detail = " ".join(errors[:3]) or "Check the column headers."
            messages.error(request, f"Nothing importable. {detail}")
            return redirect("assignments:assignment_list")

        connection = LmsConnection.objects.filter(provider=LmsProvider.CSV).first()
        result = ingest_submissions(actor, rows, connection)

        parts = [f"Imported {result['written']} attempt(s) from {result['read']} row(s)."]
        if result["skipped"]:
            parts.append(f"{result['skipped']} already present.")
        if result["unmatched"]:
            names = ", ".join(result["unmatched"][:5])
            parts.append(f"{len(result['unmatched'])} row(s) matched no student: {names}.")
        if errors:
            parts.append(f"{len(errors)} row(s) malformed.")

        messages.success(request, " ".join(parts))
        return redirect("assignments:assignment_list")

    submissions = scoped_submissions(actor)
    search = request.GET.get("search")
    if search:
        submissions = submissions.filter(
            Q(student__full_name__icontains=search)
            | Q(student__enrollment_id__icontains=search)
            | Q(assignment__title__icontains=search)
        )

    connection = LmsConnection.objects.order_by("-created_at").first()
    adapter = get_adapter()

    return render(
        request,
        "assignments/assignment_list.html",
        {
            "submissions": submissions[:100],
            "assignments": Assignment.objects.annotate(submission_count=Count("submissions")),
            "total_attempts": scoped_submissions(actor).count(),
            "awaiting": scoped_submissions(actor).filter(status__in=AWAITING).count(),
            "adapter_label": adapter.label,
            "connection": connection,
            "csv_columns": CSV_COLUMNS,
            "webhook_configured": webhook_adapter.is_configured(),
            "can_import": can_import,
            "filters": {"search": search or ""},
        },
    )


@csrf_exempt
@require_POST
def lms_webhook(request):
    """
    LMS webhook receiver.

    Signature verification is mandatory: without LMS_WEBHOOK_SECRET the adapter
    refuses everything, because an unauthenticated endpoint that writes student
    records is worse than one that does not work yet.
    """
    raw = request.body
    signature = (
        request.headers.get("X-LMS-Signature")
        or request.headers.get("X-Hub-Signature-256")
        or request.headers.get("X-Signature")
    )

    if not webhook_adapter.verify(raw, signature):
        detail = "Invalid signature." if webhook_adapter.is_configured() else "LMS_WEBHOOK_SECRET is not configured on the server."
        return JsonResponse({"error": detail}, status=401)

    try:
        rows = webhook_adapter.parse(raw.decode())
    except (ValueError, UnicodeDecodeError) as exc:
        return JsonResponse({"error": f"Could not parse payload: {exc}"}, status=400)

    if not rows:
        return JsonResponse({"ok": True, "read": 0, "written": 0, "note": "No recognisable submissions in payload."})

    # Attributed to a named system identity, not to whoever last signed in.
    actor = system_actor("lms-webhook")

    try:
        connection = LmsConnection.objects.filter(provider=LmsProvider.WEBHOOK).first()
        result = ingest_submissions(actor, rows, connection)
        return JsonResponse({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        logger.exception("LMS webhook ingestion failed")
        return JsonResponse({"error": str(exc)}, status=500)
