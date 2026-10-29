"""Merge variables for templates: {{ contact.first_name }} or {{ contact.first_name|default:"there" }}.

Deliberately not the Django template engine: only the whitelisted values below are reachable, so a template
author can't walk into model internals. Unknown variables render as empty (or their default).
"""

import re

from django.contrib.humanize.templatetags.humanize import intcomma

VARIABLE = re.compile(r"{{\s*([a-z_]+\.[a-z_]+)\s*(?:\|\s*default\s*:\s*\"([^\"]*)\"\s*)?}}")

VARIABLES = [
    ("contact.first_name", "Contact's first name"),
    ("contact.last_name", "Contact's last name"),
    ("contact.full_name", "Contact's full name"),
    ("contact.email", "Contact's email"),
    ("contact.phone", "Contact's phone"),
    ("contact.job_title", "Contact's job title"),
    ("company.name", "Company name"),
    ("deal.title", "Deal title"),
    ("deal.value", "Deal value with currency"),
    ("deal.stage", "Deal stage"),
    ("deal.expected_close_date", "Deal expected close date"),
    ("user.first_name", "Your first name"),
    ("user.full_name", "Your full name"),
    ("user.email", "Your email"),
    ("user.phone", "Your phone"),
    ("org.name", "Workspace / company name"),
]


def build_context(*, org=None, user=None, contact=None, deal=None):
    company = (contact.company if contact else None) or (deal.company if deal else None)
    values = {}
    if contact:
        values.update({
            "contact.first_name": contact.first_name, "contact.last_name": contact.last_name,
            "contact.full_name": contact.full_name, "contact.email": contact.email, "contact.phone": contact.phone,
            "contact.job_title": contact.job_title,
        })
    if company:
        values["company.name"] = company.name
    if deal:
        values.update({
            "deal.title": deal.title, "deal.value": f"{deal.currency_code} {intcomma(int(deal.value))}",
            "deal.stage": deal.stage.name,
            "deal.expected_close_date": deal.expected_close_date.strftime("%d %b %Y") if deal.expected_close_date else "",
        })
    if user:
        values.update({
            "user.first_name": user.first_name, "user.full_name": user.get_full_name() or user.email,
            "user.email": user.email, "user.phone": user.phone,
        })
    if org:
        values["org.name"] = org.name
    return values


def render(text, values):
    return VARIABLE.sub(lambda m: values.get(m.group(1)) or (m.group(2) or ""), text or "")
