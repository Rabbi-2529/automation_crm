from django.urls import reverse
from django.utils.html import format_html

from apps.core import cells
from apps.core.datatables import Column, DataTableView
from apps.orgs.mixins import OrgMemberRequiredMixin

from .filters import filter_companies, filter_contacts
from .models import Company, Contact


def _company_link(company):
    return cells.link(reverse("contacts:company_detail", args=[company.pk]), company.name) if company else cells.DASH


def _email_phone(contact):
    return format_html('{}<span class="block text-slate-500">{}</span>', contact.email, contact.phone)


class ContactTableView(OrgMemberRequiredMixin, DataTableView):
    columns = [
        Column("", lambda c: cells.checkbox(c, c.display_name)),
        Column("Name", cells.contact_name, ("first_name", "last_name", "email")),
        Column("Company", lambda c: _company_link(c.company), ("company__name",)),
        Column("Email / phone", _email_phone, ("email",)),
        Column("Stage", lambda c: cells.badge(c.get_lifecycle_stage_display(), cells.STAGE_COLORS.get(c.lifecycle_stage, "slate")),
               ("lifecycle_stage",)),
        Column("Tags", lambda c: cells.tags(c.tags.all())),
        Column("Owner", lambda c: cells.person(c.owner), ("owner__first_name", "owner__email")),
        Column("Created", lambda c: cells.date(c.created_at), ("created_at",)),
    ]

    def base_queryset(self):
        return Contact.objects.for_org(self.request.org)

    def filter_queryset(self, queryset, params, search):
        queryset = queryset.select_related("company", "owner").prefetch_related("tags")
        return filter_contacts(queryset, {**params.dict(), "q": search})


class CompanyTableView(OrgMemberRequiredMixin, DataTableView):
    columns = [
        Column("Company", lambda c: format_html(
            '<a href="{}" class="font-medium text-slate-900 hover:text-violet-700">{}</a><span class="block text-xs text-slate-500">{}</span>',
            reverse("contacts:company_detail", args=[c.pk]), c.name, c.domain), ("name",)),
        Column("Industry", lambda c: c.industry or cells.DASH, ("industry",)),
        Column("Location", lambda c: ", ".join(filter(None, [c.city, c.country])) or cells.DASH, ("city", "country")),
        Column("Contacts", lambda c: c.contact_count, ("contact_count",)),
        Column("Tags", lambda c: cells.tags(c.tags.all())),
        Column("Owner", lambda c: cells.person(c.owner), ("owner__first_name", "owner__email")),
        Column("Created", lambda c: cells.date(c.created_at), ("created_at",)),
    ]

    def base_queryset(self):
        return Company.objects.for_org(self.request.org)

    def filter_queryset(self, queryset, params, search):
        queryset = queryset.select_related("owner").prefetch_related("tags")
        return filter_companies(queryset, {**params.dict(), "q": search})
