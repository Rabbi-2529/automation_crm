"""The automation vocabulary: triggers, condition fields/operators and actions, plus config validation.

The builder UI is generated from `builder_schema()`, and saved configs are re-validated here, so the
browser can never smuggle in another workspace's tag/stage/user ids.
"""

from dataclasses import dataclass, field

from django.contrib.auth import get_user_model

from apps.contacts.models import CustomFieldDefinition, Entity, LifecycleStage, Source, Tag
from apps.integrations.models import FacebookPage
from apps.messaging.models import Channel, MessageTemplate
from apps.pipeline.models import DealStatus, Pipeline, Stage
from apps.tasks.models import Priority, TaskKind

User = get_user_model()

OWNER = "__owner__"  # "the record's owner" in user pickers
TIME_UNITS = [("minutes", "minutes"), ("hours", "hours"), ("days", "days")]


@dataclass
class Field:
    name: str
    label: str
    type: str  # text|textarea|number|checkbox|select|user|users|tag|lifecycle|stage|pipeline|email_template|sms_template|whatsapp_template|facebook_page|url|conditions
    required: bool = True
    default: object = None
    options: list = field(default_factory=list)  # for type=select: [(value, label)]
    help: str = ""
    allow_owner: bool = False  # user pickers: offer "Record owner"

    def as_dict(self):
        return {"name": self.name, "label": self.label, "type": self.type, "required": self.required, "default": self.default,
                "options": [{"id": v, "text": t} for v, t in self.options], "help": self.help, "allow_owner": self.allow_owner}


@dataclass
class Trigger:
    label: str
    group: str
    subject: str  # "contact" or "deal": what a run is enrolled with
    config: list = field(default_factory=list)
    scheduled: bool = False
    help: str = ""


@dataclass
class Action:
    label: str
    group: str
    config: list = field(default_factory=list)
    needs: str = "contact"  # contact|deal|any
    help: str = ""


CONTACT_TRACKED = [("first_name", "First name"), ("last_name", "Last name"), ("email", "Email"), ("phone", "Phone"),
                   ("job_title", "Job title"), ("company", "Company")]

TRIGGERS = {
    "contact_created": Trigger("Contact is created", "Contacts", "contact", [
        Field("include_imports", "Also for contacts created by CSV import", "checkbox", required=False, default=True)]),
    "contact_updated": Trigger("Contact field changes", "Contacts", "contact", [
        Field("field", "Field", "select", required=False, options=CONTACT_TRACKED, help="Leave empty for any field.")]),
    "lifecycle_changed": Trigger("Lifecycle stage changes", "Contacts", "contact", [
        Field("to_stage", "Changed to", "lifecycle", required=False, help="Leave empty for any stage.")]),
    "tag_added": Trigger("Tag is added to a contact", "Contacts", "contact", [Field("tag", "Tag", "tag", required=False)]),
    "owner_changed": Trigger("Contact owner changes", "Contacts", "contact"),
    "contact_inactive": Trigger("Contact has no activity for a while", "Contacts", "contact", [
        Field("days", "Days without activity", "number", default=14)], scheduled=True,
        help="Checked every 15 minutes. Each contact enters once per quiet period."),
    "deal_created": Trigger("Deal is created", "Deals", "deal", [Field("pipeline", "Pipeline", "pipeline", required=False)]),
    "deal_stage_changed": Trigger("Deal moves to a stage", "Deals", "deal", [
        Field("stage", "Stage", "stage", required=False, help="Leave empty for any move.")]),
    "deal_won": Trigger("Deal is won", "Deals", "deal"),
    "deal_lost": Trigger("Deal is lost", "Deals", "deal"),
    "deal_close_soon": Trigger("Deal's expected close date is near", "Deals", "deal", [
        Field("days", "Days before the close date", "number", default=3)], scheduled=True),
    "task_completed": Trigger("Task is completed", "Activity", "contact"),
    "email_opened": Trigger("Contact opens an email", "Activity", "contact"),
    "email_clicked": Trigger("Contact clicks a link in an email", "Activity", "contact"),
    "email_received": Trigger("Email received from contact", "Activity", "contact"),
    "sms_received": Trigger("SMS received from contact", "Activity", "contact"),
    "whatsapp_received": Trigger("WhatsApp message received from contact", "Activity", "contact"),
    "call_logged": Trigger("Call is logged", "Activity", "contact"),
    "form_submitted": Trigger("Web form is submitted", "Leads", "contact", [
        Field("form", "Form", "webform", required=False, help="Leave empty for any form.")]),
    "facebook_lead": Trigger("Facebook lead ad is submitted", "Leads", "contact", [
        Field("page", "Facebook page", "facebook_page", required=False, help="Leave empty for any connected page.")]),
    "lead_grade_changed": Trigger("Lead grade changes", "Leads", "contact", [
        Field("to_grade", "Becomes", "select", required=False, options=[("hot", "Hot"), ("warm", "Warm"), ("cold", "Cold")])]),
    "lead_not_contacted": Trigger("New lead not contacted in time (speed to lead)", "Leads", "contact", [
        Field("hours", "Hours without an email, SMS or call", "number", default=2)], scheduled=True,
        help="Checked every 15 minutes for contacts created in the last 30 days."),
    "date_field": Trigger("Date field comes round (birthday, renewal…)", "Contacts", "contact", [
        Field("field", "Date field", "date_field"),
        Field("days_before", "Days before (0 = on the day)", "number", default=0),
        Field("yearly", "Every year (birthdays, anniversaries)", "checkbox", required=False, default=True),
    ], scheduled=True),
    "deal_idle": Trigger("Deal has no activity for a while", "Deals", "deal", [
        Field("days", "Days without activity", "number", default=7)], scheduled=True),
    "webhook": Trigger("Incoming webhook", "Integrations", "contact",
                       help="POST JSON with at least an email or phone to the URL shown after saving. The contact is found or created."),
}

USER_PICK = dict(type="user", allow_owner=True)

ACTIONS = {
    "send_email": Action("Send email", "Messages", [
        Field("template", "Template", "email_template", required=False, help="Or write the email below."),
        Field("subject", "Subject", "text", required=False),
        Field("body", "Message", "textarea", required=False),
        Field("send_as", "Send as", "select", options=[("owner", "Record owner"), ("creator", "Workflow creator")], default="owner"),
    ], help="Skipped when the contact has no email."),
    "send_sms": Action("Send SMS", "Messages", [
        Field("template", "Template", "sms_template", required=False),
        Field("body", "Message", "textarea", required=False),
    ], help="Skipped when the contact has no phone."),
    "send_whatsapp": Action("Send WhatsApp message", "Messages", [
        Field("template", "Template", "whatsapp_template", required=False,
              help="Use a template with an approved WhatsApp template name to reach people who haven't written in 24 hours."),
        Field("body", "Message", "textarea", required=False),
    ], help="Skipped when the contact has no phone or opted out of WhatsApp."),
    "notify_user": Action("Email a teammate", "Messages", [
        Field("user", "Who", **USER_PICK),
        Field("message", "Message", "textarea"),
    ], needs="any"),
    "create_task": Action("Create a task", "Tasks", [
        Field("title", "Title", "text"),
        Field("kind", "Type", "select", options=TaskKind.choices, default=TaskKind.FOLLOW_UP),
        Field("priority", "Priority", "select", options=Priority.choices, default=Priority.NORMAL),
        Field("due_in", "Due in", "number", default=1),
        Field("due_unit", "Unit", "select", options=TIME_UNITS, default="days"),
        Field("assignee", "Assign to", **USER_PICK, default=OWNER),
    ], needs="any"),
    "assign_owner": Action("Assign owner", "Records", [
        Field("mode", "How", "select", options=[("user", "A specific person"), ("round_robin", "Round robin between people")], default="user"),
        Field("user", "Person", "user", required=False),
        Field("users", "People (round robin)", "users", required=False),
    ], needs="any", help="Applies to the deal when the run has one, otherwise the contact."),
    "set_lifecycle": Action("Set lifecycle stage", "Records", [Field("stage", "Stage", "lifecycle")]),
    "update_field": Action("Update a contact field", "Records", [
        Field("field", "Field", "select", options=[("job_title", "Job title"), ("source", "Source")]),
        Field("value", "New value", "text", required=False, help="Variables like {{ contact.first_name }} work here."),
    ]),
    "add_tag": Action("Add tag", "Records", [Field("tag", "Tag", "tag")], needs="any"),
    "remove_tag": Action("Remove tag", "Records", [Field("tag", "Tag", "tag")], needs="any"),
    "add_note": Action("Add a note", "Records", [Field("body", "Note", "textarea")], needs="any"),
    "create_deal": Action("Create a deal", "Deals", [
        Field("title", "Title", "text", default="{{ contact.full_name }} deal"),
        Field("stage", "Stage", "stage"),
        Field("value", "Value", "number", required=False, default=0),
    ]),
    "move_deal": Action("Move deal to stage", "Deals", [Field("stage", "Stage", "stage")], needs="deal"),
    "webhook": Action("Send webhook", "Integrations", [Field("url", "URL", "url")], needs="any",
                      help="POSTs JSON about the contact/deal, signed with X-ThingBD-Signature (HMAC-SHA256)."),
    "enroll_sequence": Action("Add to sequence", "Campaigns", [Field("sequence", "Sequence", "sequence")]),
    "remove_sequence": Action("Remove from sequence", "Campaigns", [Field("sequence", "Sequence", "sequence")]),
    "adjust_score": Action("Change lead score", "Leads", [
        Field("points", "Points (use a minus for fewer)", "integer", default=10),
        Field("reason", "Reason", "text", required=False)]),
    "wait": Action("Wait", "Flow", [
        Field("amount", "Wait", "number", default=1),
        Field("unit", "Unit", "select", options=TIME_UNITS, default="days"),
    ], needs="any"),
    "condition_gate": Action("Continue only if…", "Flow", [Field("conditions", "Conditions", "conditions")], needs="any",
                             help="Stops the run here when the conditions aren't met (handy after a wait)."),
}

# --- Conditions ---------------------------------------------------------------------------------------------------

OPERATORS = {
    "text": [("equals", "is"), ("not_equals", "is not"), ("contains", "contains"), ("not_contains", "doesn't contain"),
             ("starts_with", "starts with"), ("is_empty", "is empty"), ("is_not_empty", "is not empty")],
    "number": [("eq", "="), ("neq", "≠"), ("gt", ">"), ("gte", "≥"), ("lt", "<"), ("lte", "≤")],
    "choice": [("is", "is"), ("is_not", "is not"), ("is_empty", "is empty"), ("is_not_empty", "is not empty")],
    "tags": [("has", "has tag"), ("not_has", "doesn't have tag")],
}
NO_VALUE_OPS = {"is_empty", "is_not_empty"}


def condition_fields(org):
    """[(key, label, kind, value_type)] — value_type tells the builder which picker to show."""
    fields = [
        ("contact.first_name", "Contact · First name", "text", "text"),
        ("contact.last_name", "Contact · Last name", "text", "text"),
        ("contact.email", "Contact · Email", "text", "text"),
        ("contact.phone", "Contact · Phone", "text", "text"),
        ("contact.job_title", "Contact · Job title", "text", "text"),
        ("contact.company", "Contact · Company name", "text", "text"),
        ("contact.lifecycle_stage", "Contact · Lifecycle stage", "choice", "lifecycle"),
        ("contact.source", "Contact · Source", "choice", "source"),
        ("contact.owner", "Contact · Owner", "choice", "user"),
        ("contact.tags", "Contact · Tags", "tags", "tag"),
        ("contact.days_since_created", "Contact · Days since created", "number", "number"),
        ("contact.days_since_activity", "Contact · Days since last activity", "number", "number"),
        ("contact.score", "Contact · Lead score", "number", "number"),
        ("contact.grade", "Contact · Lead grade", "choice", "grade"),
        ("contact.email_opt_out", "Contact · Unsubscribed from email", "choice", "yesno"),
        ("contact.sms_opt_out", "Contact · Unsubscribed from SMS", "choice", "yesno"),
        ("contact.whatsapp_opt_out", "Contact · Unsubscribed from WhatsApp", "choice", "yesno"),
        ("contact.segment", "Contact · In segment", "choice", "segment"),
        ("deal.title", "Deal · Title", "text", "text"),
        ("deal.value", "Deal · Value", "number", "number"),
        ("deal.stage", "Deal · Stage", "choice", "stage"),
        ("deal.status", "Deal · Status", "choice", "deal_status"),
        ("deal.owner", "Deal · Owner", "choice", "user"),
        ("deal.tags", "Deal · Tags", "tags", "tag"),
    ]
    for definition in CustomFieldDefinition.objects.for_org(org).filter(entity__in=[Entity.CONTACT, Entity.DEAL]):
        kind = "number" if definition.field_type == "number" else "text"
        fields.append((f"{definition.entity}.cf.{definition.key}", f"{definition.get_entity_display()} · {definition.label}", kind, kind))
    return fields


# --- Builder schema & validation -------------------------------------------------------------------------------------


def choices(org):
    stages = Stage.objects.for_org(org).select_related("pipeline").order_by("pipeline__position", "pipeline__name", "position")
    grouped = {}
    for stage in stages:
        grouped.setdefault(stage.pipeline.name, []).append({"id": str(stage.pk), "text": stage.name})
    users = User.objects.filter(memberships__org=org, memberships__is_active=True).order_by("first_name", "email")
    templates = MessageTemplate.objects.for_org(org)
    from apps.campaigns.models import Segment, Sequence
    from apps.leads.models import WebForm

    date_fields = CustomFieldDefinition.objects.for_org(org).filter(entity=Entity.CONTACT, field_type="date")
    return {
        "webform": [{"id": str(f.pk), "text": f.name} for f in WebForm.objects.for_org(org)],
        "sequence": [{"id": str(s.pk), "text": s.name} for s in Sequence.objects.for_org(org)],
        "segment": [{"id": str(s.pk), "text": s.name} for s in Segment.objects.for_org(org)],
        "grade": [{"id": "hot", "text": "Hot"}, {"id": "warm", "text": "Warm"}, {"id": "cold", "text": "Cold"}],
        "yesno": [{"id": "yes", "text": "Yes"}, {"id": "no", "text": "No"}],
        "date_field": [{"id": d.key, "text": d.label} for d in date_fields],
        "user": [{"id": str(u.pk), "text": u.display_name} for u in users],
        "tag": [{"id": str(t.pk), "text": t.name} for t in Tag.objects.for_org(org)],
        "lifecycle": [{"id": v, "text": t} for v, t in LifecycleStage.choices],
        "source": [{"id": v, "text": t} for v, t in Source.choices],
        "deal_status": [{"id": v, "text": t} for v, t in DealStatus.choices],
        "stage": [{"text": name, "children": items} for name, items in grouped.items()],
        "pipeline": [{"id": str(p.pk), "text": p.name} for p in Pipeline.objects.for_org(org)],
        "email_template": [{"id": str(t.pk), "text": t.name} for t in templates.filter(channel=Channel.EMAIL)],
        "sms_template": [{"id": str(t.pk), "text": t.name} for t in templates.filter(channel=Channel.SMS)],
        "whatsapp_template": [{"id": str(t.pk), "text": t.name + (" (approved template)" if t.whatsapp_template else "")}
                              for t in templates.filter(channel=Channel.WHATSAPP)],
        "facebook_page": [{"id": str(p.pk), "text": p.name} for p in FacebookPage.objects.for_org(org)],
    }


def builder_schema(org):
    def group(items):
        return [{"key": key, "label": item.label, "group": item.group, "help": item.help,
                 "config": [f.as_dict() for f in item.config], **({"subject": item.subject} if isinstance(item, Trigger) else {"needs": item.needs})}
                for key, item in items.items()]
    return {
        "triggers": group(TRIGGERS), "actions": group(ACTIONS), "choices": choices(org),
        "conditionFields": [{"key": k, "label": label, "kind": kind, "valueType": vt} for k, label, kind, vt in condition_fields(org)],
        "operators": {kind: [{"id": v, "text": t} for v, t in ops] for kind, ops in OPERATORS.items()},
        "noValueOps": sorted(NO_VALUE_OPS),
    }


class ConfigError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def _ids_in_org(model, org, ids):
    ids = [str(i) for i in ids if str(i).isdigit()]
    return {str(pk) for pk in model.objects.filter(org=org, pk__in=ids).values_list("pk", flat=True)}


def _valid_users(org, ids):
    ids = [str(i) for i in ids if str(i).isdigit()]
    return {str(pk) for pk in User.objects.filter(pk__in=ids, memberships__org=org, memberships__is_active=True).values_list("pk", flat=True)}


def validate_config(fields, config, org, where):
    """Returns a cleaned config dict or raises ConfigError. Unknown keys are dropped."""
    config = config if isinstance(config, dict) else {}
    clean, errors = {}, []
    for f in fields:
        value = config.get(f.name, f.default)
        empty = value in (None, "", [])
        if empty:
            if f.required:
                errors.append(f"{where}: {f.label} is required.")
            elif f.default is not None:
                clean[f.name] = f.default
            continue
        if f.type == "checkbox":
            clean[f.name] = bool(value)
        elif f.type == "number":
            try:
                number = float(value)
            except (TypeError, ValueError):
                errors.append(f"{where}: {f.label} must be a number.")
                continue
            if number < 0:
                errors.append(f"{where}: {f.label} can't be negative.")
            clean[f.name] = int(number) if number == int(number) else number
        elif f.type == "integer":
            try:
                clean[f.name] = int(float(value))
            except (TypeError, ValueError):
                errors.append(f"{where}: {f.label} must be a whole number.")
        elif f.type in ("webform", "sequence"):
            from apps.campaigns.models import Sequence
            from apps.leads.models import WebForm

            if not _ids_in_org({"webform": WebForm, "sequence": Sequence}[f.type], org, [value]):
                errors.append(f"{where}: {f.label} no longer exists.")
            clean[f.name] = str(value)
        elif f.type == "date_field":
            if not CustomFieldDefinition.objects.for_org(org).filter(entity=Entity.CONTACT, field_type="date", key=value).exists():
                errors.append(f"{where}: pick a date custom field.")
            clean[f.name] = str(value)
        elif f.type == "select":
            if str(value) not in {str(v) for v, _ in f.options}:
                errors.append(f"{where}: pick a valid {f.label.lower()}.")
            clean[f.name] = str(value)
        elif f.type == "lifecycle":
            if value not in LifecycleStage.values:
                errors.append(f"{where}: pick a valid lifecycle stage.")
            clean[f.name] = value
        elif f.type in ("tag", "stage", "pipeline", "email_template", "sms_template", "whatsapp_template", "facebook_page"):
            model = {"tag": Tag, "stage": Stage, "pipeline": Pipeline, "email_template": MessageTemplate, "sms_template": MessageTemplate,
                     "whatsapp_template": MessageTemplate, "facebook_page": FacebookPage}[f.type]
            if not _ids_in_org(model, org, [value]):
                errors.append(f"{where}: {f.label} no longer exists.")
            clean[f.name] = str(value)
        elif f.type == "user":
            if not (f.allow_owner and value == OWNER) and not _valid_users(org, [value]):
                errors.append(f"{where}: {f.label} isn't an active member.")
            clean[f.name] = str(value)
        elif f.type == "users":
            values = [str(v) for v in (value if isinstance(value, list) else [value])]
            valid = _valid_users(org, values)
            if len(valid) != len(set(values)):
                errors.append(f"{where}: some people aren't active members.")
            clean[f.name] = [v for v in values if v in valid]
        elif f.type == "url":
            if not str(value).startswith(("https://", "http://")):
                errors.append(f"{where}: {f.label} must start with https://")
            clean[f.name] = str(value)[:500]
        elif f.type == "conditions":
            try:
                clean[f.name] = validate_conditions(value, org)
            except ConfigError as exc:
                errors.extend(f"{where}: {e}" for e in exc.errors)
        else:
            clean[f.name] = str(value)[:5000]
    if errors:
        raise ConfigError(errors)
    return clean


def validate_conditions(conditions, org):
    conditions = conditions if isinstance(conditions, dict) else {}
    fields = {key: (kind, value_type) for key, _, kind, value_type in condition_fields(org)}
    rules, errors = [], []
    for i, rule in enumerate(conditions.get("rules") or [], start=1):
        if not isinstance(rule, dict):
            continue
        key, op, value = rule.get("field"), rule.get("op"), rule.get("value", "")
        if key not in fields:
            errors.append(f"Condition {i}: pick a field.")
            continue
        kind, value_type = fields[key]
        if op not in {o for o, _ in OPERATORS[kind]}:
            errors.append(f"Condition {i}: pick a valid operator.")
            continue
        if op not in NO_VALUE_OPS:
            if value in (None, ""):
                errors.append(f"Condition {i}: enter a value.")
                continue
            if kind == "number":
                try:
                    float(value)
                except (TypeError, ValueError):
                    errors.append(f"Condition {i}: value must be a number.")
                    continue
            if value_type == "segment":
                from apps.campaigns.models import Segment

                if not _ids_in_org(Segment, org, [value]):
                    errors.append(f"Condition {i}: that segment no longer exists.")
                    continue
            if value_type in ("tag", "stage") and not _ids_in_org({"tag": Tag, "stage": Stage}[value_type], org, [value]):
                errors.append(f"Condition {i}: that {value_type} no longer exists.")
                continue
            if value_type == "user" and not _valid_users(org, [value]):
                errors.append(f"Condition {i}: that person isn't an active member.")
                continue
        rules.append({"field": key, "op": op, "value": "" if op in NO_VALUE_OPS else str(value)})
    if errors:
        raise ConfigError(errors)
    return {"match": "any" if conditions.get("match") == "any" else "all", "rules": rules}
