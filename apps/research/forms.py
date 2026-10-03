import json
import re
from datetime import datetime

from django import forms
from django.utils import timezone

from apps.core.enums import MetricSource, RatingLevel, SubmissionStatus
from apps.research.models import ResearchCriterion, ResearchSubmission


def _whole_number(value) -> int | None:
    """A blank stays None. A figure the student could not find is not a zero."""
    text = str(value or "").strip().replace(",", "")
    return int(text) if text.isdigit() else None


def _as_datetime(value):
    """Accepts the YYYY-MM-DD a date input produces."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return timezone.make_aware(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _view_series(value) -> list:
    """
    Per-video views, oldest first — typed as "5000, 8000, 10000" or pasted as a
    list. Order carries the meaning, so it is preserved exactly as given.
    """
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").replace("\n", ",").split(",")
    series = []
    for item in items:
        number = _whole_number(item)
        if number is not None:
            series.append(number)
    return series


class ResearchSubmissionForm(forms.ModelForm):
    """
    Competitor rows arrive as a JSON blob from the repeatable field widget, so
    an arbitrary number of rows needs no formset plumbing.
    """

    competitors = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = ResearchSubmission
        fields = [
            "topic", "niche", "sub_niche", "target_audience", "target_country",
            "content_format", "video_length_minutes", "upload_frequency",
            "competition_level", "earning_potential", "keyword_research",
            "content_gap_analysis", "monetization_potential", "notes",
        ]
        widgets = {
            "keyword_research": forms.Textarea(attrs={"rows": 3, "placeholder": "Terms, search volume, difficulty."}),
            "content_gap_analysis": forms.Textarea(attrs={"rows": 3, "placeholder": "What is missing that you can occupy."}),
            "monetization_potential": forms.Textarea(attrs={"rows": 3, "placeholder": "How this niche makes money."}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "video_length_minutes": "Typical video length (minutes)",
            "competition_level": "Estimated competition",
            "earning_potential": "Estimated earning potential",
            "notes": "Anything else",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # "Not assessed" must be a valid answer, so the optional enums carry an
        # explicit blank choice rather than rejecting an empty submission.
        for name in ("competition_level", "earning_potential"):
            self.fields[name].required = False
            self.fields[name].choices = [("", "Not assessed"), *RatingLevel.choices]
        for name, field in self.fields.items():
            if name != "competitors":
                field.required = False
                field.widget.attrs.setdefault("class", "input")

    def clean_competitors(self):
        raw = self.cleaned_data.get("competitors") or "[]"
        try:
            rows = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(rows, list):
            return []

        cleaned = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("channel_name") or "").strip()[:200]
            url = str(row.get("channel_url") or "").strip()[:500]
            if not name or not url:
                continue

            metrics = {
                "subscriber_count": _whole_number(row.get("subscriber_count")),
                "view_count": _whole_number(row.get("view_count")),
                "video_count": _whole_number(row.get("video_count")),
                "oldest_video_at": _as_datetime(row.get("oldest_video_at")),
                "video_views_series": _view_series(row.get("video_views_series")),
            }
            # Only stamp a source when the student actually supplied something;
            # an empty row must not look like a hand-checked one.
            typed_anything = any(
                value not in (None, [], "") for value in metrics.values()
            )

            cleaned.append(
                {
                    "channel_name": name,
                    "channel_url": url,
                    "notes": (str(row.get("notes") or "").strip()[:500]) or None,
                    "metrics_source": MetricSource.MANUAL if typed_anything else None,
                    **metrics,
                }
            )
        return cleaned


class ReviewForm(forms.Form):
    """
    Per-criterion scores are collected as dynamic fields named
    ``score_<criterion_id>`` so the form adapts to whatever criteria are active.
    """

    DECISIONS = [
        ("", "Choose an outcome"),
        (SubmissionStatus.APPROVED, "Approve — student proceeds to channel creation"),
        (SubmissionStatus.REVISION_REQUESTED, "Request revision — student resubmits"),
        (SubmissionStatus.REJECTED, "Reject — student resubmits"),
    ]

    decision = forms.ChoiceField(choices=DECISIONS, widget=forms.Select(attrs={"class": "input"}))
    feedback = forms.CharField(
        required=False,
        label="Feedback to the student",
        widget=forms.Textarea(attrs={"class": "input", "rows": 3, "placeholder": "What was good, what to change."}),
    )
    rejection_reason = forms.CharField(
        required=False,
        label="Reason (if sending back)",
        widget=forms.Textarea(attrs={"class": "input", "rows": 2}),
    )

    def __init__(self, *args, criteria=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.criteria = list(criteria or [])

        for criterion in self.criteria:
            key = str(criterion.pk)
            self.fields[f"score_{key}"] = forms.FloatField(
                required=True,
                min_value=0,
                max_value=float(criterion.max_score),
                initial=0,
                label=criterion.name,
                widget=forms.NumberInput(
                    attrs={
                        "class": "input score-input",
                        "type": "range",
                        "min": 0,
                        "max": float(criterion.max_score),
                        "step": 1,
                        "data-criterion": key,
                        "data-weight": float(criterion.weightage),
                        "data-max": float(criterion.max_score),
                        "data-pass": float(criterion.min_passing_score),
                        "data-required": "1" if criterion.is_required else "0",
                    }
                ),
            )
            self.fields[f"note_{key}"] = forms.CharField(
                required=False,
                widget=forms.TextInput(
                    attrs={"class": "input", "placeholder": "Note for this criterion (optional)"}
                ),
            )

    def scores(self) -> dict[str, float]:
        return {
            str(c.pk): self.cleaned_data.get(f"score_{c.pk}") or 0
            for c in self.criteria
            if f"score_{c.pk}" in self.cleaned_data
        }

    def notes(self) -> dict[str, str]:
        return {
            str(c.pk): self.cleaned_data.get(f"note_{c.pk}", "")
            for c in self.criteria
        }

    def criterion_rows(self):
        """Pairs each criterion with its bound score and note fields, for the template."""
        for criterion in self.criteria:
            yield {
                "criterion": criterion,
                "score_field": self[f"score_{criterion.pk}"],
                "note_field": self[f"note_{criterion.pk}"],
            }


class CriterionForm(forms.ModelForm):
    class Meta:
        model = ResearchCriterion
        fields = [
            "name", "description", "weightage", "max_score", "min_passing_score",
            "is_required", "evidence_required", "display_order", "is_active",
        ]
        labels = {
            "weightage": "Weight",
            "max_score": "Max score",
            "min_passing_score": "Pass mark",
            "is_required": "Required — failing it fails the submission",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "input")

    def clean(self):
        cleaned = super().clean()
        pass_mark = cleaned.get("min_passing_score")
        maximum = cleaned.get("max_score")
        if pass_mark is not None and maximum is not None and pass_mark > maximum:
            raise forms.ValidationError("The pass mark cannot exceed the maximum score.")
        return cleaned


SHEET_MAX_ROWS = 500
_URL = re.compile(r"^https?://\S+$", re.I)


def _sheet_number(value):
    """Numbers arrive already parsed by the browser; anything else is unknown."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value < 0 or value > 10**13:  # NaN, negative, absurd
        return None
    return round(value, 2)


def _sheet_text(value, limit):
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


class SheetSubmissionForm(forms.Form):
    """
    Submits the channel sheet a student loaded in the Channel research tab as a
    research attempt. The sheet is parsed in the browser; everything it sends
    is re-checked here, since anything can be posted to this endpoint.
    """

    # Shares the page with the full research form, so its ids must not clash.
    prefix = "sheet"

    topic = forms.CharField(
        max_length=200, label="Topic",
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "e.g. Faceless sports documentaries"}),
    )
    niche = forms.CharField(
        max_length=160, required=False, label="Niche",
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    notes = forms.CharField(
        required=False, label="Note for your instructor",
        widget=forms.Textarea(attrs={"class": "input", "rows": 2, "placeholder": "Optional"}),
    )
    sheet = forms.CharField(widget=forms.HiddenInput())

    def clean_sheet(self):
        try:
            data = json.loads(self.cleaned_data["sheet"])
        except (TypeError, ValueError):
            raise forms.ValidationError("The sheet could not be read. Load the file again and resubmit.")
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            raise forms.ValidationError("Load a sheet with at least one channel before submitting.")
        if len(rows) > SHEET_MAX_ROWS:
            raise forms.ValidationError(f"A sheet can hold at most {SHEET_MAX_ROWS} channels.")

        clean_rows = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            name = _sheet_text(raw.get("name"), 200)
            if not name:
                continue
            link = _sheet_text(raw.get("link"), 500)
            size = _sheet_text(raw.get("size"), 20)
            clean_rows.append({
                "n": _sheet_number(raw.get("n")),
                "name": name,
                "link": link if _URL.match(link) else None,
                "angle": _sheet_text(raw.get("angle"), 300),
                "cat": _sheet_text(raw.get("cat"), 160),
                "videos": _sheet_number(raw.get("videos")),
                "subs": _sheet_number(raw.get("subs")),
                "views": _sheet_number(raw.get("views")),
                "avg": _sheet_number(raw.get("avg")),
                "size": size,
                "notes": _sheet_text(raw.get("notes"), 1000),
            })
        if not clean_rows:
            raise forms.ValidationError("None of the rows has a channel name.")
        return {
            "file": _sheet_text(data.get("file"), 200) or "Uploaded sheet",
            "sheet": _sheet_text(data.get("sheet"), 100) or None,
            "rows": clean_rows,
        }

    def competitors(self) -> list[dict]:
        """
        The sheet's channels as competitor rows, so the four niche-validation
        rules run on them. A row needs a link to be a competitor; rows without
        one stay visible in the sheet itself.
        """
        out = []
        for r in self.cleaned_data["sheet"]["rows"]:
            if not r["link"]:
                continue
            bits = [b for b in (r["angle"], r["cat"], r["size"], r["notes"]) if b]
            metrics = {
                "subscriber_count": int(r["subs"]) if r["subs"] is not None else None,
                "view_count": int(r["views"]) if r["views"] is not None else None,
                "video_count": int(r["videos"]) if r["videos"] is not None else None,
            }
            out.append({
                "channel_name": r["name"],
                "channel_url": r["link"],
                "notes": " · ".join(bits)[:1000] or None,
                "metrics_source": MetricSource.MANUAL if any(v is not None for v in metrics.values()) else None,
                **metrics,
            })
        return out
