"""Forms for the staff screens.

Every form is limited to the signed-in user's workspace: related-record
dropdowns only list that workspace's records, and files uploaded with the form
are stored as RecordFile attachments on the saved record.
"""

from django import forms
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from apps.records.models import RecordFile


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"multiple": True}))
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data if d]
        return [single(data, initial)] if data else []


DATE_FIELDS = (forms.DateField,)


class ScopedModelForm(forms.ModelForm):
    """ModelForm that scopes foreign keys to a workspace and can take file attachments."""

    attachments = MultipleFileField(label=_("Attach files"), help_text=_("Receipts, screenshots, photos, letters"))
    attachment_kind = forms.ChoiceField(
        label=_("File type"), choices=RecordFile.Kind.choices, required=False, initial=RecordFile.Kind.OTHER
    )

    default_attachment_kind = RecordFile.Kind.OTHER
    allow_attachments = True

    def __init__(self, *args, workspace=None, user=None, **kwargs):
        self.workspace = workspace
        self.user = user
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.ModelChoiceField):
                model = field.queryset.model
                if any(f.name == "workspace" for f in model._meta.fields):
                    field.queryset = model._default_manager.filter(workspace=workspace, archived_at__isnull=True)
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
            if isinstance(field, forms.DateTimeField):
                field.widget = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
        if self.allow_attachments:
            self.fields["attachment_kind"].initial = self.default_attachment_kind
        else:
            del self.fields["attachments"]
            del self.fields["attachment_kind"]

    def _post_clean(self):
        if self.workspace is not None and not self.instance.workspace_id:
            self.instance.workspace = self.workspace
        super()._post_clean()

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if commit:
            self.save_attachments(obj)
        return obj

    def save_attachments(self, obj):
        files = self.cleaned_data.get("attachments") or []
        kind = self.cleaned_data.get("attachment_kind") or self.default_attachment_kind
        ct = ContentType.objects.get_for_model(obj)
        for f in files:
            RecordFile.objects.create(
                workspace_id=obj.workspace_id,
                content_type=ct,
                object_id=obj.pk,
                kind=kind,
                file=f,
                title=f.name,
                uploaded_by=self.user,
            )


def scoped_form(model_cls, fields, *, attachment_kind=RecordFile.Kind.OTHER, attachments=True, labels=None):
    meta = type("Meta", (), {"model": model_cls, "fields": fields, "labels": labels or {}})
    return type(
        f"{model_cls.__name__}Form",
        (ScopedModelForm,),
        {"Meta": meta, "default_attachment_kind": attachment_kind, "allow_attachments": attachments},
    )


class FileUploadForm(forms.Form):
    attachments = MultipleFileField(label=_("Files"), required=True)
    attachment_kind = forms.ChoiceField(label=_("File type"), choices=RecordFile.Kind.choices)
    title = forms.CharField(label=_("Description"), required=False, max_length=200)
