"""Single entry point for writing timeline entries.

Phase 4's automation engine will hook in here: every Activity is an event a workflow trigger can match.
"""

from django.db.models import Model

from .models import Activity, ActivityKind


def record(*, org, kind, summary, actor=None, contact=None, company=None, data=None, note=None, attachment=None):
    activity = Activity.objects.create(
        org=org,
        kind=kind,
        summary=summary[:255],
        actor=actor,
        contact=contact,
        company=company,
        data=data or {},
        note=note,
        attachment=attachment,
    )
    for target in (contact, company):
        if target is not None:
            type(target).objects.filter(pk=target.pk).update(last_activity_at=activity.created_at)
    return activity


def snapshot(instance, fields):
    """Values to diff later. FKs are kept as objects so summaries can show names."""
    return {
        "fields": {name: getattr(instance, name) for name in fields},
        "tags": set(instance.tags.all()) if instance.pk else set(),
        "custom_fields": dict(instance.custom_fields or {}),
    }


def _label(instance, name):
    return str(instance._meta.get_field(name).verbose_name).capitalize()


def _show(value):
    if value in (None, ""):
        return "—"
    return str(value.display_name if hasattr(value, "display_name") else value)


def record_changes(*, instance, before, actor, custom_field_defs=(), target_kw):
    """Diffs `instance` against a `snapshot()` and writes one activity per meaningful change.

    `target_kw` is {"contact": obj} or {"company": obj}.
    """
    org = instance.org
    after = snapshot(instance, before["fields"].keys())
    changed_labels = []

    for name, old in before["fields"].items():
        new = after["fields"][name]
        if old == new:
            continue
        if name == "owner":
            record(org=org, kind=ActivityKind.OWNER_CHANGED, actor=actor, summary=f"Owner changed to {_show(new)}",
                   data={"from": getattr(old, "pk", None), "to": getattr(new, "pk", None)}, **target_kw)
        elif name == "lifecycle_stage":
            choices = dict(instance._meta.get_field(name).choices)
            record(org=org, kind=ActivityKind.STAGE_CHANGED, actor=actor,
                   summary=f"Lifecycle stage: {choices.get(old, old)} → {choices.get(new, new)}",
                   data={"from": old, "to": new}, **target_kw)
        else:
            old_value = old.pk if isinstance(old, Model) else old
            new_value = new.pk if isinstance(new, Model) else new
            changed_labels.append((name, _label(instance, name), old_value, new_value))

    labels = {d.key: d.label for d in custom_field_defs}
    for key in set(before["custom_fields"]) | set(after["custom_fields"]):
        old, new = before["custom_fields"].get(key), after["custom_fields"].get(key)
        if old != new:
            changed_labels.append((f"custom_fields.{key}", labels.get(key, key), old, new))

    if changed_labels:
        record(org=org, kind=ActivityKind.UPDATED, actor=actor,
               summary="Updated " + ", ".join(label for _, label, *_ in changed_labels),
               data={"changes": {field: {"from": old, "to": new} for field, _, old, new in changed_labels}},
               **target_kw)

    for tag in after["tags"] - before["tags"]:
        record(org=org, kind=ActivityKind.TAG_ADDED, actor=actor, summary=f"Tagged “{tag.name}”", data={"tag": tag.pk}, **target_kw)
    for tag in before["tags"] - after["tags"]:
        record(org=org, kind=ActivityKind.TAG_REMOVED, actor=actor, summary=f"Removed tag “{tag.name}”", data={"tag": tag.pk}, **target_kw)
