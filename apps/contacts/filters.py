"""Query-string filtering shared by the list views, CSV export and bulk actions."""

from django.db.models import Count, Q

from .models import LifecycleStage, phone_digits

CONTACT_SORTS = {
    "name": ("first_name", "last_name"),
    "-created": ("-created_at",),
    "created": ("created_at",),
    "-activity": ("-last_activity_at",),
    "company": ("company__name", "first_name"),
}
COMPANY_SORTS = {"name": ("name",), "-created": ("-created_at",), "-contacts": ("-contact_count", "name")}


def _search_terms(q):
    return [term for term in (q or "").split() if term][:5]


def filter_contacts(queryset, params):
    for term in _search_terms(params.get("q")):
        match = (
            Q(first_name__icontains=term) | Q(last_name__icontains=term) | Q(email__icontains=term)
            | Q(company__name__icontains=term) | Q(job_title__icontains=term)
        )
        digits = phone_digits(term)
        if len(digits) >= 4:
            match |= Q(phone_digits__contains=digits)
        queryset = queryset.filter(match)

    if tag := params.get("tag"):
        queryset = queryset.filter(tags__pk=tag)
    if stage := params.get("stage"):
        if stage in LifecycleStage.values:
            queryset = queryset.filter(lifecycle_stage=stage)
    if owner := params.get("owner"):
        queryset = queryset.filter(owner__isnull=True) if owner == "none" else queryset.filter(owner__pk=owner)
    if company := params.get("company"):
        queryset = queryset.filter(company__pk=company)

    sort = CONTACT_SORTS.get(params.get("sort"), CONTACT_SORTS["-created"])
    return queryset.order_by(*sort, "pk").distinct()


def filter_companies(queryset, params):
    queryset = queryset.annotate(contact_count=Count("contacts", distinct=True))
    for term in _search_terms(params.get("q")):
        queryset = queryset.filter(
            Q(name__icontains=term) | Q(domain__icontains=term) | Q(industry__icontains=term) | Q(city__icontains=term)
        )
    if tag := params.get("tag"):
        queryset = queryset.filter(tags__pk=tag)
    if owner := params.get("owner"):
        queryset = queryset.filter(owner__isnull=True) if owner == "none" else queryset.filter(owner__pk=owner)
    sort = COMPANY_SORTS.get(params.get("sort"), COMPANY_SORTS["name"])
    return queryset.order_by(*sort, "pk").distinct()
