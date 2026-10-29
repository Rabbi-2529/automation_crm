"""Turns segment conditions into a database query so lists of thousands of contacts stay fast."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.contacts.models import Contact

TEXT_FIELDS = {
    "contact.first_name": "first_name", "contact.last_name": "last_name", "contact.email": "email",
    "contact.phone": "phone", "contact.job_title": "job_title", "contact.company": "company__name",
}
CHOICE_FIELDS = {"contact.lifecycle_stage": "lifecycle_stage", "contact.source": "source", "contact.owner": "owner_id",
                 "contact.grade": "grade"}
BOOL_FIELDS = {"contact.email_opt_out": "email_opt_out", "contact.sms_opt_out": "sms_opt_out"}
NUMBER_OPS = {"eq": "exact", "gt": "gt", "gte": "gte", "lt": "lt", "lte": "lte"}


def _number(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _empty(path):
    return Q(**{f"{path}__isnull": True}) | Q(**{path: ""}) if not path.endswith("_id") else Q(**{f"{path}__isnull": True})


def rule_q(rule):
    """Q for one rule. Rules about deals don't apply to contact segments and match nobody."""
    field, op, value = rule["field"], rule["op"], rule.get("value", "")
    if field in TEXT_FIELDS:
        path = TEXT_FIELDS[field]
        return {
            "equals": Q(**{f"{path}__iexact": value}), "not_equals": ~Q(**{f"{path}__iexact": value}),
            "contains": Q(**{f"{path}__icontains": value}), "not_contains": ~Q(**{f"{path}__icontains": value}),
            "starts_with": Q(**{f"{path}__istartswith": value}),
            "is_empty": _empty(path), "is_not_empty": ~_empty(path),
        }[op]
    if field in CHOICE_FIELDS or field in BOOL_FIELDS:
        path = CHOICE_FIELDS.get(field) or BOOL_FIELDS[field]
        if field in BOOL_FIELDS:
            value = value == "yes"
        if op == "is":
            return Q(**{path: value})
        if op == "is_not":
            return ~Q(**{path: value})
        return _empty(path) if op == "is_empty" else ~_empty(path)
    if field == "contact.tags":
        return Q(tags__pk=value) if op == "has" else ~Q(tags__pk=value)
    if field == "contact.segment":
        from .models import Segment

        segment = Segment.objects.filter(pk=value).first()
        inner = Q(pk__in=segment_queryset(segment, _depth=1).values("pk")) if segment else Q(pk__in=[])
        return inner if op == "is" else ~inner
    if field in ("contact.score", "contact.days_since_created", "contact.days_since_activity"):
        number = _number(value)
        if number is None:
            return Q(pk__in=[])
        if field == "contact.score":
            q = Q(**{f"score__{NUMBER_OPS[op]}": number}) if op in NUMBER_OPS else None
            return q if q is not None else ~Q(score=number)
        # "days since X > 7" == "X before now - 7 days" (comparisons flip).
        moment = timezone.now() - timedelta(days=float(number))
        path = "created_at" if field == "contact.days_since_created" else "last_touch"
        flipped = {"gt": "lt", "gte": "lte", "lt": "gt", "lte": "gte"}
        if op in flipped:
            return Q(**{f"{path}__{flipped[op]}": moment})
        day_start, day_end = moment - timedelta(hours=12), moment + timedelta(hours=12)
        same = Q(**{f"{path}__gte": day_start, f"{path}__lt": day_end})
        return same if op == "eq" else ~same
    if field.startswith("contact.cf."):
        key = field[len("contact.cf."):]
        path = f"custom_fields__{key}"
        if op in ("is_empty", "is_not_empty"):
            has = Q(**{f"custom_fields__has_key": key}) & ~Q(**{path: ""})
            return ~has if op == "is_empty" else has
        if op in NUMBER_OPS or op == "neq":
            number = _number(value)
            if number is None:
                return Q(pk__in=[])
            q = Q(**{f"{path}__{NUMBER_OPS.get(op, 'exact')}": float(number)})
            return ~q if op == "neq" else q
        text = {"equals": "iexact", "not_equals": "iexact", "contains": "icontains", "not_contains": "icontains",
                "starts_with": "istartswith"}[op]
        q = Q(**{f"{path}__{text}": value})
        return ~q if op.startswith("not_") else q
    return Q(pk__in=[])


def segment_queryset(segment, _depth=0):
    contacts = Contact.objects.filter(org_id=segment.org_id).annotate(last_touch=Coalesce("last_activity_at", "created_at"))
    rules = (segment.conditions or {}).get("rules") or []
    if _depth > 2:  # segments referencing segments referencing segments...
        return contacts.none()
    if not rules:
        return contacts
    parts = [rule_q(rule) for rule in rules]
    combined = parts[0]
    for part in parts[1:]:
        combined = (combined | part) if segment.conditions.get("match") == "any" else (combined & part)
    return contacts.filter(combined).distinct()


def contains(segment, contact):
    return segment_queryset(segment).filter(pk=contact.pk).exists()
