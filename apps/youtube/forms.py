import re

from django import forms

from apps.academy.models import Student
from apps.youtube.models import YoutubeChannel

# A YouTube channel ID looks like UC followed by 22 characters.
CHANNEL_ID_PATTERN = re.compile(r"^UC[\w-]{22}$")


class ChannelForm(forms.ModelForm):
    link_override_reason = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "placeholder": "Only needed when this channel is already linked to another student.",
            }
        ),
        help_text="Linking one channel to a second student is recorded as an override in the audit log.",
    )

    class Meta:
        model = YoutubeChannel
        fields = [
            "student", "channel_name", "channel_url", "youtube_channel_id", "channel_creation_date",
            "niche", "sub_niche", "content_format", "content_type", "target_audience", "target_country",
            "primary_language", "upload_schedule", "status", "monetization_status",
            "monetization_approved_at", "ownership_verified", "is_brand_account", "notes",
            "link_override_reason",
        ]
        widgets = {
            "channel_creation_date": forms.DateInput(attrs={"type": "date"}),
            "monetization_approved_at": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "youtube_channel_id": "Required before any YouTube data can be fetched. Must be unique across students.",
        }

    def __init__(self, *args, actor=None, can_override=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        if actor is not None:
            self.fields["student"].queryset = Student.objects.for_actor(actor)
        if not can_override:
            self.fields.pop("link_override_reason", None)
        for name, field in self.fields.items():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "input")

    def clean_youtube_channel_id(self):
        value = (self.cleaned_data.get("youtube_channel_id") or "").strip()
        if not value:
            return None
        if not CHANNEL_ID_PATTERN.match(value):
            raise forms.ValidationError("A YouTube channel ID looks like UC followed by 22 characters.")
        return value

    def clean_channel_url(self):
        value = (self.cleaned_data.get("channel_url") or "").strip()
        if value and not re.match(r"^https?://(www\.)?(youtube\.com|youtu\.be)/", value, re.I):
            raise forms.ValidationError("Enter a youtube.com or youtu.be URL.")
        return value or None

    def clean(self):
        """
        Enforces specification 23.6: one channel belongs to one student unless a
        Super Admin supplies a reason, which is then audited as an override.
        """
        cleaned = super().clean()
        channel_id = cleaned.get("youtube_channel_id")
        if not channel_id:
            return cleaned

        clash = YoutubeChannel.objects.filter(youtube_channel_id=channel_id)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        existing = clash.select_related("student").first()

        if existing and not cleaned.get("link_override_reason"):
            raise forms.ValidationError(
                f"That YouTube channel is already linked to {existing.student.full_name} "
                f"({existing.student.enrollment_id}). A Super Admin can override this with a recorded reason."
            )
        return cleaned
