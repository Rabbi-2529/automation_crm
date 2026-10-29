from django.db.models import Q

from .models import DealStatus

DEAL_SORTS = {
    "-created": ("-created_at",),
    "-value": ("-value", "-created_at"),
    "close": ("expected_close_date", "-created_at"),
    "title": ("title",),
}


def filter_deals(queryset, params, user=None):
    for term in (params.get("q") or "").split()[:5]:
        queryset = queryset.filter(
            Q(title__icontains=term) | Q(contact__first_name__icontains=term) | Q(contact__last_name__icontains=term)
            | Q(contact__email__icontains=term) | Q(company__name__icontains=term)
        )
    status = params.get("status", "open")
    if status in DealStatus.values:
        queryset = queryset.filter(status=status)
    if pipeline := params.get("pipeline"):
        queryset = queryset.filter(pipeline__pk=pipeline)
    if stage := params.get("stage"):
        queryset = queryset.filter(stage__pk=stage)
    owner = params.get("owner")
    if owner == "me" and user is not None:
        queryset = queryset.filter(owner=user)
    elif owner == "none":
        queryset = queryset.filter(owner__isnull=True)
    elif owner:
        queryset = queryset.filter(owner__pk=owner)
    sort = DEAL_SORTS.get(params.get("sort"), DEAL_SORTS["-created"])
    return queryset.order_by(*sort, "pk").distinct()
