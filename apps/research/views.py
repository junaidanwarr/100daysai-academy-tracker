from django.conf import settings
from django.contrib import messages
from apps.core.access import staff_console
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.academy.models import Batch, Student
from apps.core.enums import SubmissionStatus, UserRole
from apps.core.permissions import can
from apps.core.settings_service import competitor_thresholds
from apps.research.competitor_metrics import (
    CompetitorLookupError,
    evaluate_submission,
    refresh_competitor,
)
from apps.research.forms import CriterionForm, ResearchSubmissionForm, ReviewForm
from apps.research.models import ResearchCriteriaSet, ResearchCriterion, ResearchSubmission
from apps.research.services import (
    SubmissionStateError,
    archive_criterion,
    criteria_for_student,
    review_queue_counts,
    review_research,
    scoped_submissions,
    submit_research,
    upsert_criterion,
)

AWAITING = [SubmissionStatus.SUBMITTED, SubmissionStatus.UNDER_REVIEW, SubmissionStatus.RESUBMITTED]


def _competitor_rows(draft) -> list[dict]:
    """
    Competitor rows shaped for the portal's repeatable-row widget, so a saved
    draft comes back with every figure the student already typed rather than
    just the channel name.
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


@staff_console
def submission_list(request):
    actor = request.user
    if not can(actor.role, "research", "read"):
        raise PermissionDenied("Your role may not view research submissions.")
    if actor.role == UserRole.STUDENT:
        return redirect("research:portal_research")

    submissions = scoped_submissions(actor)

    status = request.GET.get("status")
    search = request.GET.get("search")
    batch = request.GET.get("batch")

    if status == "ALL":
        pass
    elif status:
        submissions = submissions.filter(status=status)
    else:
        # Default view is the review queue, not a wall of closed attempts.
        submissions = submissions.filter(status__in=AWAITING)

    if batch:
        submissions = submissions.filter(student__batch_id=batch)
    if search:
        submissions = submissions.filter(
            Q(topic__icontains=search)
            | Q(niche__icontains=search)
            | Q(student__full_name__icontains=search)
            | Q(student__enrollment_id__icontains=search)
        )

    return render(
        request,
        "research/submission_list.html",
        {
            "submissions": submissions[:300],
            "counts": review_queue_counts(actor),
            "statuses": SubmissionStatus.choices,
            "batches": Batch.objects.all(),
            "filters": {"status": status or "", "search": search or "", "batch": batch or ""},
            "can_configure": can(actor.role, "research", "configure"),
        },
    )


@staff_console
@require_http_methods(["GET", "POST"])
def submission_detail(request, pk):
    actor = request.user
    if not can(actor.role, "research", "read"):
        raise PermissionDenied("Your role may not view research submissions.")

    submission = get_object_or_404(
        scoped_submissions(actor).prefetch_related("competitors", "criterion_scores__criterion"), pk=pk
    )
    _, criteria = criteria_for_student(submission.student)
    may_review = can(actor.role, "research", "review") and not submission.is_closed

    form = ReviewForm(request.POST or None, criteria=criteria) if may_review else None

    if request.method == "POST" and may_review and form.is_valid():
        try:
            _, explanation = review_research(
                actor,
                submission,
                decision=form.cleaned_data["decision"],
                scores=form.scores(),
                notes=form.notes(),
                feedback=form.cleaned_data.get("feedback") or None,
                rejection_reason=form.cleaned_data.get("rejection_reason") or None,
            )
            messages.success(request, f"Review recorded. {explanation}")
            return redirect("research:submission_detail", pk=submission.pk)
        except SubmissionStateError as exc:
            messages.error(request, str(exc))

    history = ResearchSubmission.objects.filter(student=submission.student).order_by("-version")

    # The four niche-validation rules, recomputed live so a threshold change in
    # Settings takes effect immediately rather than leaving stale verdicts.
    verdict = evaluate_submission(submission)
    competitor_rows = list(zip(submission.competitors.all(), verdict.channels))

    return render(
        request,
        "research/submission_detail.html",
        {
            "submission": submission,
            "form": form,
            "criteria": criteria,
            "history": history,
            "may_review": may_review,
            "keyword_research": submission.keyword_research,
            "verdict": verdict,
            "competitor_rows": competitor_rows,
            "thresholds": competitor_thresholds(),
            "can_fetch_metrics": bool(settings.YOUTUBE_API_KEY) and can(actor.role, "research", "review"),
        },
    )


@staff_console
@require_POST
def refresh_competitors(request, pk):
    """
    Pulls public figures for every competitor channel on a submission.

    Costs roughly 3 quota units per channel and reads only public data, so no
    student consent is involved. Deliberately a manual action rather than
    something that happens on page load: a page that silently spends API quota
    every time someone opens it will exhaust the day's allowance by lunchtime.
    """
    actor = request.user
    if not can(actor.role, "research", "review"):
        raise PermissionDenied("Your role may not refresh competitor figures.")

    submission = get_object_or_404(scoped_submissions(actor), pk=pk)
    refreshed, failures, truncated = 0, [], 0

    for competitor in submission.competitors.all():
        try:
            outcome = refresh_competitor(competitor)
            refreshed += 1
            truncated += bool(outcome["truncated"])
        except CompetitorLookupError as exc:
            failures.append(f"{competitor.channel_name}: {exc}")

    if refreshed:
        messages.success(request, f"Updated figures for {refreshed} channel(s) from the public YouTube API.")
    if truncated:
        messages.info(
            request,
            f"{truncated} channel(s) have more uploads than were sampled, so their age and growth "
            "figures are read from the most recent uploads only.",
        )
    for failure in failures:
        messages.warning(request, failure)
    if not refreshed and not failures:
        messages.info(request, "There are no competitor channels on this submission to refresh.")

    return redirect("research:submission_detail", pk=submission.pk)


@staff_console
@require_http_methods(["GET", "POST"])
def criteria_editor(request):
    actor = request.user
    if not can(actor.role, "research", "read"):
        raise PermissionDenied("Your role may not view evaluation criteria.")

    editable = can(actor.role, "research", "configure")

    if request.method == "POST":
        if not editable:
            raise PermissionDenied("Your role may not configure evaluation criteria.")

        criterion_id = request.POST.get("criterion_id")
        criterion = ResearchCriterion.all_objects.filter(pk=criterion_id).first() if criterion_id else None

        if request.POST.get("action") == "retire" and criterion:
            archive_criterion(actor, criterion)
            messages.success(request, "Criterion retired. Past scores are unaffected.")
            return redirect("research:criteria")

        criteria_set = get_object_or_404(ResearchCriteriaSet, pk=request.POST.get("criteria_set"))
        form = CriterionForm(request.POST, instance=criterion)
        if form.is_valid():
            upsert_criterion(actor, criteria_set, form.cleaned_data, criterion)
            messages.success(request, "Criterion saved. It applies to reviews from now on.")
            return redirect("research:criteria")
        messages.error(request, form.errors.as_text())

    sets = ResearchCriteriaSet.objects.prefetch_related("criteria").all()

    return render(
        request,
        "research/criteria.html",
        {
            "criteria_sets": [
                {
                    "set": criteria_set,
                    "criteria": [
                        {"criterion": c, "form": CriterionForm(instance=c, prefix=str(c.pk))}
                        for c in criteria_set.criteria.filter(deleted_at__isnull=True).order_by("display_order")
                    ],
                    "blank_form": CriterionForm(prefix=f"new-{criteria_set.pk}"),
                    "active_count": criteria_set.criteria.filter(is_active=True, deleted_at__isnull=True).count(),
                    "total_weight": criteria_set.total_weight,
                }
                for criteria_set in sets
            ],
            "editable": editable,
        },
    )


# --- Student portal ---------------------------------------------------------
