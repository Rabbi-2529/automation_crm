from django.contrib import admin

from .models import Deal, LostReason, Pipeline, Stage


class StageInline(admin.TabularInline):
    model = Stage
    extra = 0
    fields = ["name", "position", "probability", "rot_days"]


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    list_display = ["name", "org", "is_default", "position"]
    list_filter = ["org"]
    inlines = [StageInline]


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ["title", "value", "status", "stage", "owner", "org", "created_at"]
    list_filter = ["org", "status", "pipeline"]
    search_fields = ["title"]
    raw_id_fields = ["contact", "company", "owner", "created_by", "stage", "pipeline", "lost_reason"]


@admin.register(LostReason)
class LostReasonAdmin(admin.ModelAdmin):
    list_display = ["name", "org", "position"]
    list_filter = ["org"]
