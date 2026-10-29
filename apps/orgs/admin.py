from django.contrib import admin

from .models import Membership, Organization, Team


class MembershipInline(admin.TabularInline):
    model = Membership
    fk_name = "org"
    extra = 0
    autocomplete_fields = ["user"]
    fields = ["user", "role", "is_active"]


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "currency", "is_active", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug"]
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "org", "role", "is_active", "created_at"]
    list_filter = ["role", "is_active", "org"]
    search_fields = ["user__email", "org__name"]
    autocomplete_fields = ["user", "org", "invited_by"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "org", "lead"]
    list_filter = ["org"]
    search_fields = ["name"]
    filter_horizontal = ["members"]
