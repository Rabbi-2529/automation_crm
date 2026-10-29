"""Small HTML snippets for DataTables cells. Everything goes through format_html, so values are escaped."""

from django.contrib.humanize.templatetags.humanize import intcomma
from django.urls import reverse
from django.utils.html import format_html, format_html_join

STAGE_COLORS = {
    "lead": "sky", "qualified": "violet", "opportunity": "amber", "customer": "emerald", "churned": "red",
}
STATUS_COLORS = {"open": "sky", "won": "emerald", "lost": "red"}
DASH = format_html('<span class="text-slate-400">—</span>')


def badge(label, color="slate"):
    return format_html('<span class="badge bg-{0}-100 text-{0}-700">{1}</span>', color, label)


def link(url, text, extra_class=""):
    return format_html('<a href="{}" class="hover:text-violet-700 {}">{}</a>', url, extra_class, text)


def tags(objects):
    chips = format_html_join("", '<span class="badge bg-{}-100 text-{}-700">{}</span>',
                             ((t.color, t.color, t.name) for t in objects))
    return format_html('<div class="flex flex-wrap gap-1">{}</div>', chips)


def person(user):
    return user.display_name if user else DASH


def date(value, fmt="%d %b %Y"):
    return value.strftime(fmt) if value else DASH


def money(currency, value):
    return format_html('<span class="whitespace-nowrap font-medium">{} {}</span>', currency, intcomma(int(value or 0)))


def checkbox(obj, label):
    return format_html('<input type="checkbox" name="ids" value="{}" form="bulk-form" aria-label="Select {}" class="h-4 w-4">',
                       obj.pk, label)


def contact_name(contact):
    return format_html(
        '<a href="{}" class="flex items-center gap-3 font-medium text-slate-900 hover:text-violet-700">'
        '<span class="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-violet-100 text-xs font-semibold text-violet-700">{}</span>'
        '<span>{}<span class="block text-xs font-normal text-slate-500">{}</span></span></a>',
        reverse("contacts:contact_detail", args=[contact.pk]), contact.initials, contact.display_name, contact.job_title,
    )
