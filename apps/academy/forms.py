from datetime import timedelta

from django import forms
from django.utils import timezone

from apps.academy.models import Batch, Student
from apps.accounts.models import User
from apps.core.enums import MANUAL_ACTIVITY_KINDS, BatchStatus, StudentStatus, UserRole


class StudentForm(forms.ModelForm):
    """
    The Enrollment ID is not editable — it is generated once and is the stable
    identifier the whole system keys on.
    """

    class Meta:
        model = Student
        fields = [
            "full_name", "guardian_name", "email", "phone", "city", "country",
            "enrollment_date", "batch", "instructor", "course_start_date",
            "expected_completion_date", "research_start_date", "research_deadline",
            "lms_student_id", "profile_image_url", "notes",
        ]
        widgets = {
            "enrollment_date": forms.DateInput(attrs={"type": "date"}),
            "course_start_date": forms.DateInput(attrs={"type": "date"}),
            "expected_completion_date": forms.DateInput(attrs={"type": "date"}),
            "research_start_date": forms.DateInput(attrs={"type": "date"}),
            "research_deadline": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "research_start_date": "Setting this starts the research countdown.",
            "research_deadline": "Leave blank to calculate it from the configured research window.",
            "instructor": "Leave blank to use the batch instructor.",
            "lms_student_id": "Used to match assignments when the LMS is connected.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["batch"].queryset = Batch.objects.all()
        self.fields["instructor"].queryset = User.objects.filter(
            role__in=[UserRole.INSTRUCTOR, UserRole.SUPER_ADMIN], is_active=True, deleted_at__isnull=True
        )
        self.fields["instructor"].required = False
        for name, field in self.fields.items():
            css = "input"
            if isinstance(field.widget, forms.CheckboxInput):
                css = ""
            field.widget.attrs.setdefault("class", css)


class StatusChangeForm(forms.Form):
    to_status = forms.ChoiceField(choices=[], widget=forms.Select(attrs={"class": "input"}))
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "input", "rows": 2, "placeholder": "Recorded in the student's timeline."}),
    )
    override = forms.BooleanField(required=False, label="Override the normal progression")

    def __init__(self, *args, allowed=None, can_override=False, **kwargs):
        super().__init__(*args, **kwargs)
        # When overriding, every status is offered; otherwise only the ones the
        # state machine permits from here.
        choices = StudentStatus.choices if can_override else [(s, StudentStatus(s).label) for s in (allowed or [])]
        self.fields["to_status"].choices = [("", "Select a status"), *choices]
        if not can_override:
            self.fields.pop("override")


class BatchForm(forms.ModelForm):
    class Meta:
        model = Batch
        fields = ["code", "name", "instructor", "start_date", "end_date", "status", "research_days", "description", "criteria_set"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "research_days": "Leave blank to use the academy-wide window. Changing this re-dates deadlines for this batch on the next scheduled run.",
            "code": "Letters, numbers and hyphens. Must be unique.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["instructor"].queryset = User.objects.filter(
            role__in=[UserRole.INSTRUCTOR, UserRole.SUPER_ADMIN], is_active=True, deleted_at__isnull=True
        )
        self.fields["instructor"].required = False
        self.fields["status"].choices = BatchStatus.choices
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")

    def clean_code(self):
        return self.cleaned_data["code"].upper()


class ActivityLogForm(forms.Form):
    """Attendance, mentoring, contact and notes — the things no service can see."""

    kind = forms.ChoiceField(label="What happened", choices=[])
    summary = forms.CharField(
        max_length=300,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Live class: thumbnails and titles"}),
    )
    occurred_at = forms.DateTimeField(
        label="When",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        help_text="Leave blank for now.",
    )
    detail = forms.CharField(required=False, label="Details", widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [(k.value, k.label) for k in MANUAL_ACTIVITY_KINDS]
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")

    def clean_occurred_at(self):
        value = self.cleaned_data.get("occurred_at")
        if value and value > timezone.now() + timedelta(minutes=5):
            raise forms.ValidationError("An activity cannot be logged in the future.")
        return value
