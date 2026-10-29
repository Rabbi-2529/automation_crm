"""Public web forms: field catalogue, the dynamic Django form, spam checks and turning a submission into a contact."""

from django import forms
from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.activities.models import ActivityKind, Note
from apps.activities.services import record
from apps.contacts.models import Company, Contact, CustomFieldDefinition, Entity, FieldType, Source

STANDARD_FIELDS = [
    ("first_name", "First name", "text"),
    ("last_name", "Last name", "text"),
    ("email", "Email", "email"),
    ("phone", "Phone", "tel"),
    ("company", "Company", "text"),
    ("job_title", "Job title", "text"),
    ("message", "Message", "textarea"),
]
MIN_SECONDS = 2  # faster than this is a bot
TIMESTAMP_SALT = "crm.webform.ts"


def available_fields(org):
    fields = list(STANDARD_FIELDS)
    for d in CustomFieldDefinition.objects.for_org(org).filter(entity=Entity.CONTACT):
        kind = {FieldType.NUMBER: "number", FieldType.DATE: "date", FieldType.SELECT: "select", FieldType.CHECKBOX: "checkbox",
                FieldType.URL: "url", FieldType.EMAIL: "email"}.get(d.field_type, "text")
        fields.append((f"cf:{d.key}", d.label, kind))
    return fields


def form_class(webform):
    """Builds the public Django form for this WebForm's chosen fields."""
    catalogue = {key: (label, kind) for key, label, kind in available_fields(webform.org)}
    definitions = {d.key: d for d in CustomFieldDefinition.objects.for_org(webform.org).filter(entity=Entity.CONTACT)}
    attrs = {}
    for spec in webform.fields:
        key = spec.get("key")
        if key not in catalogue:
            continue
        default_label, kind = catalogue[key]
        options = {"label": spec.get("label") or default_label, "required": bool(spec.get("required"))}
        widget_attrs = {"class": "field", "autocomplete": {"first_name": "given-name", "last_name": "family-name", "email": "email",
                                                            "phone": "tel", "company": "organization"}.get(key, "off")}
        if kind == "email":
            field = forms.EmailField(widget=forms.EmailInput(attrs=widget_attrs), max_length=254, **options)
        elif kind == "textarea":
            field = forms.CharField(widget=forms.Textarea(attrs={**widget_attrs, "rows": 4}), max_length=5000, **options)
        elif kind == "number":
            field = forms.DecimalField(widget=forms.NumberInput(attrs=widget_attrs), **options)
        elif kind == "date":
            field = forms.DateField(widget=forms.DateInput(attrs={**widget_attrs, "type": "date"}), **options)
        elif kind == "checkbox":
            field = forms.BooleanField(**options)
        elif kind == "select":
            choices = [("", "—")] + [(o, o) for o in definitions[key[3:]].options]
            field = forms.ChoiceField(choices=choices, widget=forms.Select(attrs=widget_attrs), **options)
        elif kind == "url":
            field = forms.URLField(widget=forms.URLInput(attrs=widget_attrs), **options)
        else:
            field = forms.CharField(widget=forms.TextInput(attrs={**widget_attrs, "type": kind if kind == "tel" else "text"}),
                                    max_length=200, **options)
        attrs[key.replace(":", "__")] = field
    if webform.consent_text:
        attrs["consent"] = forms.BooleanField(label=webform.consent_text, required=True)
    # Spam traps: a field humans never see, and a signed render time.
    attrs["website"] = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))
    attrs["ts"] = forms.CharField(widget=forms.HiddenInput)
    return type("PublicWebForm", (forms.Form,), attrs)


def render_timestamp():
    return signing.dumps(timezone.now().timestamp(), salt=TIMESTAMP_SALT)


def looks_like_spam(cleaned):
    if cleaned.get("website"):
        return True
    try:
        rendered = signing.loads(cleaned.get("ts", ""), salt=TIMESTAMP_SALT, max_age=60 * 60 * 24)
    except signing.BadSignature:
        return True
    return timezone.now().timestamp() - rendered < MIN_SECONDS


def _values(webform, cleaned):
    values = {}
    for spec in webform.fields:
        key = spec.get("key", "")
        value = cleaned.get(key.replace(":", "__"))
        if value in (None, "", False):
            continue
        values[key] = value.isoformat() if hasattr(value, "isoformat") else (float(value) if hasattr(value, "as_tuple") else value)
    return values


@transaction.atomic
def submit(webform, cleaned, *, utm=None, referrer="", ip=None):
    """Creates or updates the contact for a valid, non-spam submission. Returns (submission, contact)."""
    from .models import FormSubmission

    values = _values(webform, cleaned)
    org = webform.org
    email, phone = str(values.get("email", "")).strip().lower(), str(values.get("phone", "")).strip()
    contacts = Contact.objects.for_org(org)
    contact = (contacts.filter(email=email).first() if email else None) or (contacts.filter(phone=phone).first() if phone else None)
    created = contact is None
    if created:
        contact = Contact(org=org, source=Source.WEB_FORM, lifecycle_stage=webform.lifecycle_stage or "lead",
                          owner=webform.default_owner, created_by=None)

    # New contacts take everything; existing ones only get blanks filled, so a form can't overwrite CRM data.
    for field in ("first_name", "last_name", "email", "phone", "job_title"):
        if values.get(field) and (created or not getattr(contact, field)):
            setattr(contact, field, str(values[field])[:contact._meta.get_field(field).max_length])
    if values.get("company") and not contact.company_id:
        name = str(values["company"])[:200]
        contact.company = Company.objects.for_org(org).filter(name__iexact=name).first() or Company.objects.create(org=org, name=name)
    custom = dict(contact.custom_fields or {})
    for key, value in values.items():
        if key.startswith("cf:"):
            custom[key[3:]] = value
    contact.custom_fields = custom
    contact.save()
    if webform.add_tags.exists():
        contact.tags.add(*webform.add_tags.all())

    submission = FormSubmission.objects.create(
        org=org, form=webform, contact=contact, data={**values, **({"consent": True} if cleaned.get("consent") else {})},
        created_contact=created, utm=utm or {}, referrer=(referrer or "")[:500], ip_address=ip,
    )
    type(webform).objects.filter(pk=webform.pk).update(submission_count=webform.submission_count + 1)
    if created:
        record(org=org, kind=ActivityKind.CREATED, contact=contact, summary=f"Contact created from form “{webform.name}”")
    if values.get("message"):
        note = Note.objects.create(org=org, contact=contact, body=f"Form “{webform.name}”:\n{values['message']}")
        record(org=org, kind=ActivityKind.NOTE_ADDED, contact=contact, note=note, summary="Message from web form")
    source = " · ".join(filter(None, [(utm or {}).get("utm_source"), (utm or {}).get("utm_campaign")]))
    record(org=org, kind=ActivityKind.FORM_SUBMITTED, contact=contact,
           summary=f"Submitted form “{webform.name}”" + (f" ({source})" if source else ""),
           data={"form": webform.pk, "submission": submission.pk, "utm": utm or {}})
    return submission, contact
