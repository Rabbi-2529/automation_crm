"""Turns org-defined CustomFieldDefinitions into form fields, stored JSON values and display values."""

import datetime
from decimal import Decimal, InvalidOperation

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email

from .models import CustomFieldDefinition, FieldType

FORM_PREFIX = "cf_"
TRUE_WORDS = {"1", "true", "yes", "y"}
FALSE_WORDS = {"0", "false", "no", "n", ""}


def definitions_for(org, entity):
    return list(CustomFieldDefinition.objects.for_org(org).filter(entity=entity))


def form_field(definition):
    common = {"label": definition.label, "required": definition.required}
    match definition.field_type:
        case FieldType.NUMBER:
            return forms.DecimalField(**common)
        case FieldType.DATE:
            return forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), **common)
        case FieldType.SELECT:
            return forms.ChoiceField(choices=[("", "—")] + [(o, o) for o in definition.options], **common)
        case FieldType.CHECKBOX:
            return forms.BooleanField(label=definition.label, required=False)
        case FieldType.URL:
            return forms.URLField(**common)
        case FieldType.EMAIL:
            return forms.EmailField(**common)
        case _:
            return forms.CharField(max_length=500, **common)


def to_json(definition, value):
    """Cleaned form value -> JSON-safe value. Empty values are dropped by the caller."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value


def from_json(definition, value):
    """Stored JSON value -> form initial value."""
    if definition.field_type == FieldType.DATE and value:
        return datetime.date.fromisoformat(value)
    return value


def is_empty(value):
    return value is None or value == "" or value is False


class CustomFieldFormMixin:
    """Adds `cf_<key>` fields to a ModelForm whose model has `custom_fields`. Pass `custom_field_defs`."""

    def add_custom_fields(self, definitions):
        self.custom_field_defs = definitions
        stored = self.instance.custom_fields or {}
        for definition in definitions:
            name = FORM_PREFIX + definition.key
            self.fields[name] = form_field(definition)
            if definition.key in stored:
                self.initial[name] = from_json(definition, stored[definition.key])

    def custom_field_items(self):
        return [self[FORM_PREFIX + d.key] for d in self.custom_field_defs]

    def custom_field_values(self):
        values = dict(self.instance.custom_fields or {})
        for definition in self.custom_field_defs:
            value = self.cleaned_data.get(FORM_PREFIX + definition.key)
            if is_empty(value):
                values.pop(definition.key, None)
            else:
                values[definition.key] = to_json(definition, value)
        return values


def coerce(definition, raw):
    """CSV cell -> JSON value. Raises ValueError with a readable message. Returns None for blank cells."""
    raw = (raw or "").strip()
    if not raw:
        if definition.required:
            raise ValueError(f"{definition.label} is required")
        return None
    match definition.field_type:
        case FieldType.NUMBER:
            try:
                return to_json(definition, Decimal(raw.replace(",", "")))
            except InvalidOperation:
                raise ValueError(f"{definition.label}: '{raw}' is not a number") from None
        case FieldType.DATE:
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
                try:
                    return datetime.datetime.strptime(raw, fmt).date().isoformat()
                except ValueError:
                    continue
            raise ValueError(f"{definition.label}: '{raw}' is not a date (use YYYY-MM-DD)")
        case FieldType.SELECT:
            match = next((o for o in definition.options if o.lower() == raw.lower()), None)
            if match is None:
                raise ValueError(f"{definition.label}: '{raw}' is not one of {', '.join(definition.options)}")
            return match
        case FieldType.CHECKBOX:
            if raw.lower() in TRUE_WORDS:
                return True
            if raw.lower() in FALSE_WORDS:
                return None
            raise ValueError(f"{definition.label}: '{raw}' should be yes or no")
        case FieldType.URL:
            try:
                URLValidator()(raw)
            except ValidationError:
                raise ValueError(f"{definition.label}: '{raw}' is not a valid URL") from None
            return raw
        case FieldType.EMAIL:
            try:
                validate_email(raw)
            except ValidationError:
                raise ValueError(f"{definition.label}: '{raw}' is not a valid email") from None
            return raw.lower()
        case _:
            return raw[:500]


def display(definitions, values):
    """[(label, display_value)] for every definition that has a value."""
    rows = []
    for definition in definitions:
        value = (values or {}).get(definition.key)
        if is_empty(value):
            continue
        if definition.field_type == FieldType.CHECKBOX:
            value = "Yes"
        elif definition.field_type == FieldType.DATE:
            value = datetime.date.fromisoformat(value).strftime("%d %b %Y")
        rows.append((definition, value))
    return rows
