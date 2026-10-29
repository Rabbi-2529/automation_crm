from functools import lru_cache

from django import template
from django.conf import settings
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from ..assets import LIBRARIES, TAILWIND_BROWSER, TAILWIND_OUTPUT, TAILWIND_SOURCE

register = template.Library()


@register.simple_tag
def asset(name):
    """URL of a front-end library: the CDN in development, the vendored copy in production builds."""
    library = LIBRARIES[name]
    return static(f"vendor/{library['vendor']}") if settings.COMPILED_ASSETS else library["cdn"]


@lru_cache(maxsize=1)
def _tailwind_source():
    lines = TAILWIND_SOURCE.read_text(encoding="utf-8").splitlines()
    # @import / @source are for the CLI build; the browser compiler already has Tailwind and scans the page.
    return "\n".join(line for line in lines if not line.lstrip().startswith(("@import", "@source")))


@register.simple_tag
def tailwind():
    """Compiled stylesheet in production; the in-browser compiler plus our theme/components in development."""
    if settings.COMPILED_ASSETS:
        return format_html('<link rel="stylesheet" href="{}">', static(TAILWIND_OUTPUT))
    source = _tailwind_source() if not settings.DEBUG else _tailwind_source.__wrapped__()
    return mark_safe(f'<script src="{TAILWIND_BROWSER}"></script>\n<style type="text/tailwindcss">\n{source}\n</style>')  # noqa: S308 - our own file
