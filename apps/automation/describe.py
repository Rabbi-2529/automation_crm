"""Plain-English summaries of a workflow's trigger, conditions and actions, and starter recipes."""

from .registry import ACTIONS, OWNER, TIME_UNITS, TRIGGERS, choices, condition_fields

OP_WORDS = {
    "equals": "is", "is": "is", "not_equals": "is not", "is_not": "is not", "contains": "contains",
    "not_contains": "doesn't contain", "starts_with": "starts with", "is_empty": "is empty", "is_not_empty": "is not empty",
    "eq": "=", "neq": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "has": "has tag", "not_has": "doesn't have tag",
}


class Describer:
    def __init__(self, org):
        self.choices = choices(org)
        self.fields = {key: (label, value_type) for key, label, _, value_type in condition_fields(org)}
        flat = {}
        for kind, items in self.choices.items():
            for item in items:
                for child in item.get("children", [item]):
                    flat[(kind, str(child["id"]))] = child["text"]
        self.names = flat

    def name(self, kind, value):
        if value == OWNER:
            return "the record owner"
        return self.names.get((kind, str(value)), "(deleted)")

    def field_value(self, field, value):
        if value in (None, "", []):
            return None
        if field.type in ("user", "tag", "stage", "pipeline", "lifecycle", "email_template", "sms_template", "webform", "sequence",
                          "date_field"):
            return self.name(field.type, value)
        if field.type == "users":
            return ", ".join(self.name("user", v) for v in value)
        if field.type == "select":
            return dict(field.options).get(value, value)
        if field.type == "checkbox":
            return "yes" if value else "no"
        if field.type == "conditions":
            return self.conditions(value) or "always"
        text = str(value)
        return text if len(text) <= 80 else text[:79] + "…"

    def trigger(self, workflow):
        spec = TRIGGERS.get(workflow.trigger_type)
        if spec is None:
            return "Unknown trigger"
        details = [f"{f.label.lower()}: {v}" for f in spec.config
                   if (v := self.field_value(f, (workflow.trigger_config or {}).get(f.name))) is not None]
        return spec.label + (f" ({'; '.join(details)})" if details else "")

    def conditions(self, conditions):
        rules = (conditions or {}).get("rules") or []
        parts = []
        for rule in rules:
            label, value_type = self.fields.get(rule["field"], (rule["field"], "text"))
            value = rule.get("value", "")
            if value_type in ("user", "tag", "stage", "lifecycle", "source", "deal_status", "grade", "yesno", "segment"):
                value = self.name(value_type, value)
            parts.append(f"{label} {OP_WORDS.get(rule['op'], rule['op'])}" + (f" “{value}”" if value != "" else ""))
        joiner = " or " if (conditions or {}).get("match") == "any" else " and "
        return joiner.join(parts)

    def action(self, action):
        spec = ACTIONS.get(action.action_type)
        if spec is None:
            return "Unknown action"
        cfg = action.config or {}
        if action.action_type == "wait":
            return f"Wait {cfg.get('amount')} {dict(TIME_UNITS).get(cfg.get('unit'), cfg.get('unit'))}"
        details = [f"{f.label.lower()}: {v}" for f in spec.config if (v := self.field_value(f, cfg.get(f.name))) is not None]
        return spec.label + (f" — {'; '.join(details)}" if details else "")


RECIPES = {
    "welcome_lead": {
        "name": "Welcome new leads",
        "description": "Email new leads right away and give their owner a call task for tomorrow.",
        "trigger_type": "contact_created", "trigger_config": {"include_imports": False},
        "conditions": {"match": "all", "rules": [{"field": "contact.lifecycle_stage", "op": "is", "value": "lead"},
                                                 {"field": "contact.email", "op": "is_not_empty", "value": ""}]},
        "actions": [
            {"type": "send_email", "config": {"subject": "Thanks for your interest, {{ contact.first_name|default:\"there\" }}",
                                              "body": "Hi {{ contact.first_name|default:\"there\" }},\n\nThanks for getting in touch "
                                                      "with {{ org.name }}. I'll call you shortly to understand what you need.\n\n"
                                                      "Best,\n{{ user.full_name }}", "send_as": "owner"}},
            {"type": "create_task", "config": {"title": "Call {{ contact.full_name }}", "kind": "call", "priority": "high",
                                               "due_in": 1, "due_unit": "days", "assignee": OWNER}},
        ],
    },
    "quiet_opportunity": {
        "name": "Nudge quiet opportunities",
        "description": "When an opportunity has had no activity for 14 days, create a follow-up task and tell the owner.",
        "trigger_type": "contact_inactive", "trigger_config": {"days": 14}, "allow_reenroll": True,
        "conditions": {"match": "all", "rules": [{"field": "contact.lifecycle_stage", "op": "is", "value": "opportunity"}]},
        "actions": [
            {"type": "create_task", "config": {"title": "Follow up with {{ contact.full_name }} (quiet for 14 days)",
                                               "kind": "follow_up", "priority": "normal", "due_in": 0, "due_unit": "days",
                                               "assignee": OWNER}},
            {"type": "notify_user", "config": {"user": OWNER, "message": "{{ contact.full_name }} has gone quiet for 14 days. "
                                                                           "A follow-up task is waiting for you."}},
        ],
    },
    "deal_won_thanks": {
        "name": "Thank customers after a win",
        "description": "Send a thank-you email when a deal is won, then ask for a referral a week later.",
        "trigger_type": "deal_won", "trigger_config": {},
        "conditions": {"match": "all", "rules": []},
        "actions": [
            {"type": "send_email", "config": {"subject": "Thank you, {{ contact.first_name|default:\"and welcome\" }}!",
                                              "body": "Hi {{ contact.first_name|default:\"there\" }},\n\nThank you for choosing "
                                                      "{{ org.name }} for {{ deal.title }}. We're excited to get started.\n\n"
                                                      "{{ user.full_name }}", "send_as": "owner"}},
            {"type": "wait", "config": {"amount": 7, "unit": "days"}},
            {"type": "create_task", "config": {"title": "Ask {{ contact.full_name }} for a referral", "kind": "call",
                                               "priority": "normal", "due_in": 0, "due_unit": "days", "assignee": OWNER}},
        ],
    },
    "round_robin": {
        "name": "Share new leads round robin",
        "description": "Give each new unassigned lead to the next person in turn.",
        "trigger_type": "contact_created", "trigger_config": {"include_imports": True},
        "conditions": {"match": "all", "rules": [{"field": "contact.owner", "op": "is_empty", "value": ""}]},
        "actions": [{"type": "assign_owner", "config": {"mode": "round_robin", "users": []}}],
    },
}
