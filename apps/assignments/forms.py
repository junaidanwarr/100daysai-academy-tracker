from django import forms
from django.core.validators import MaxValueValidator

from apps.assignments.models import Assignment
from apps.core.enums import SubmissionStatus


def _style(form):
    for field in form.fields.values():
        if not isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", "input")


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ["title", "type", "batch", "due_at", "max_score", "description", "is_active"]
        labels = {
            "due_at": "Due",
            "max_score": "Maximum score",
            "description": "Instructions",
            "is_active": "Open for submissions",
        }
        widgets = {
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "description": forms.Textarea(attrs={"rows": 6}),
        }
        help_texts = {
            "batch": "Leave blank to set it for every batch.",
            "due_at": "Work handed in after this is accepted but marked late.",
            "max_score": "Leave blank for pass/fail review without a score.",
            "is_active": "Closed assignments stay visible, but no new attempts are accepted.",
        }

    def __init__(self, *args, batches=None, allow_all_batches=True, **kwargs):
        super().__init__(*args, **kwargs)
        if batches is not None:
            self.fields["batch"].queryset = batches
        self.fields["batch"].required = not allow_all_batches
        if not allow_all_batches:
            self.fields["batch"].help_text = "The batch this work is set for."
        _style(self)


class ReviewAssignmentForm(forms.Form):
    DECISIONS = [
        ("", "Choose an outcome"),
        (SubmissionStatus.APPROVED, "Approve"),
        (SubmissionStatus.REVISION_REQUESTED, "Request revision — student resubmits"),
        (SubmissionStatus.REJECTED, "Reject — student resubmits"),
    ]

    decision = forms.ChoiceField(choices=DECISIONS)
    score = forms.DecimalField(required=False, min_value=0, max_digits=6, decimal_places=2)
    feedback = forms.CharField(
        required=False,
        label="Feedback to the student",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "What was good, what to change."}),
    )
    rejection_reason = forms.CharField(
        required=False,
        label="Reason (required if sending back)",
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, max_score=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_score = max_score
        if max_score is not None:
            self.fields["score"].validators.append(MaxValueValidator(max_score))
            self.fields["score"].label = f"Score (out of {max_score})"
        _style(self)

    def clean(self):
        data = super().clean()
        decision = data.get("decision")
        if decision and decision != SubmissionStatus.APPROVED and not (data.get("rejection_reason") or "").strip():
            self.add_error("rejection_reason", "Tell the student why the work is being sent back.")
        return data


class SubmitAssignmentForm(forms.Form):
    response_text = forms.CharField(
        required=False,
        label="Your response",
        widget=forms.Textarea(attrs={"rows": 8, "placeholder": "Write your answer, or summarise what the link contains."}),
    )
    submission_url = forms.URLField(
        required=False,
        label="Link to your work",
        assume_scheme="https",
        help_text="A Google Doc, Drive folder, spreadsheet or video — make sure it is shared with your instructor.",
        widget=forms.URLInput(attrs={"placeholder": "https://"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)

    def clean(self):
        data = super().clean()
        if not (data.get("response_text") or "").strip() and not data.get("submission_url"):
            raise forms.ValidationError("Write a response or add a link to your work.")
        return data

