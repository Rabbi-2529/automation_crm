"""Evaluates condition rules ({field, op, value}) against a run's contact/deal."""

from decimal import Decimal, InvalidOperation

from django.utils import timezone


def _days_since(moment):
    return (timezone.now() - moment).days if moment else None


def resolve(key, contact=None, deal=None):
    """Value of a condition field for this contact/deal. Missing records resolve to None."""
    entity, _, name = key.partition(".")
    obj = contact if entity == "contact" else deal if entity == "deal" else None
    if obj is None:
        return None
    if name.startswith("cf."):
        return (obj.custom_fields or {}).get(name[3:])
    if name == "tags":
        return {str(pk) for pk in obj.tags.values_list("pk", flat=True)}
    if name == "company":
        return obj.company.name if obj.company_id else ""
    if name in ("owner", "stage"):
        return str(getattr(obj, f"{name}_id") or "")
    if name in ("email_opt_out", "sms_opt_out"):
        return "yes" if getattr(obj, name) else "no"
    if name == "days_since_created":
        return _days_since(obj.created_at)
    if name == "days_since_activity":
        return _days_since(obj.last_activity_at or obj.created_at)
    return getattr(obj, name, None)


def _number(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def check(rule, contact=None, deal=None):
    op, expected = rule["op"], rule.get("value", "")
    if rule["field"] == "contact.segment":
        from apps.campaigns.models import Segment
        from apps.campaigns.segments import contains

        segment = Segment.objects.filter(pk=expected or 0).first()
        inside = bool(contact and segment and segment.org_id == contact.org_id and contains(segment, contact))
        return inside if op == "is" else not inside
    actual = resolve(rule["field"], contact, deal)

    if op == "has":
        return expected in (actual or set())
    if op == "not_has":
        return expected not in (actual or set())
    empty = actual in (None, "", set())
    if op == "is_empty":
        return empty
    if op == "is_not_empty":
        return not empty

    if op in ("eq", "neq", "gt", "gte", "lt", "lte"):
        left, right = _number(actual), _number(expected)
        if left is None or right is None:
            return op == "neq"
        return {"eq": left == right, "neq": left != right, "gt": left > right, "gte": left >= right,
                "lt": left < right, "lte": left <= right}[op]

    left, right = str(actual if actual is not None else "").strip().lower(), str(expected).strip().lower()
    return {
        "equals": left == right, "is": left == right,
        "not_equals": left != right, "is_not": left != right,
        "contains": right in left, "not_contains": right not in left,
        "starts_with": left.startswith(right),
    }.get(op, False)


def matches(conditions, contact=None, deal=None):
    rules = (conditions or {}).get("rules") or []
    if not rules:
        return True
    results = (check(rule, contact, deal) for rule in rules)
    return any(results) if conditions.get("match") == "any" else all(results)
