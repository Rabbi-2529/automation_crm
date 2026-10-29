"""Server-side processing for DataTables (https://datatables.net/manual/server-side).

Subclass DataTableView, list `columns`, and implement `base_queryset()` (org-scoped) and `filter_queryset()`.
The page's <table data-dt-source="..."> is wired up by static/js/ui.js.
"""

from dataclasses import dataclass, field
from typing import Callable

from django.db.models import F
from django.http import JsonResponse
from django.views import View

MAX_PAGE_LENGTH = 100


@dataclass
class Column:
    title: str
    render: Callable  # obj -> safe HTML string (use format_html)
    order_by: tuple = field(default_factory=tuple)  # empty = not sortable
    css: str = ""


class DataTableView(View):
    columns: list = []

    def base_queryset(self):
        raise NotImplementedError

    def filter_queryset(self, queryset, params, search):
        return queryset

    def extra_payload(self, queryset):
        """Extra keys for the JSON response (e.g. totals for a footer), computed on the filtered queryset."""
        return {}

    def ordering(self, params):
        try:
            index = int(params.get("order[0][column]", ""))
            column = self.columns[index]
        except (ValueError, IndexError):
            return None
        if not column.order_by:
            return None
        descending = params.get("order[0][dir]") == "desc"
        return [
            F(name).desc(nulls_last=True) if descending else F(name).asc(nulls_last=True) for name in column.order_by
        ] + ["-pk" if descending else "pk"]

    def get(self, request, *args, **kwargs):
        params = request.GET
        try:
            draw = int(params.get("draw", 0))
            start = max(int(params.get("start", 0)), 0)
            length = min(max(int(params.get("length", 25)), 1), MAX_PAGE_LENGTH)
        except ValueError:
            draw, start, length = 0, 0, 25
        search = params.get("search[value]", "").strip()

        base = self.base_queryset()
        queryset = self.filter_queryset(base, params, search)
        if order := self.ordering(params):
            queryset = queryset.order_by(*order)
        page = list(queryset[start:start + length])
        return JsonResponse({
            "draw": draw,
            "recordsTotal": base.count(),
            "recordsFiltered": queryset.count(),
            "data": [[column.render(obj) for column in self.columns] for obj in page],
            **self.extra_payload(queryset),
        })
