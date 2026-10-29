from django import forms
from django.conf import settings


class NoteForm(forms.Form):
    body = forms.CharField(
        label="Note", max_length=10_000, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Add a note…"})
    )


class AttachmentForm(forms.Form):
    file = forms.FileField()

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > settings.ATTACHMENT_MAX_MB * 1024 * 1024:
            raise forms.ValidationError(f"Files must be under {settings.ATTACHMENT_MAX_MB} MB.")
        return upload
