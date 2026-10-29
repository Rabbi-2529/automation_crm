from django.contrib import admin

from .models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "assignee", "due_at", "completed_at", "org"]
    list_filter = ["org", "kind", "priority"]
    search_fields = ["title"]
    raw_id_fields = ["assignee", "created_by", "completed_by", "contact", "company", "deal"]
