from django.contrib import admin

from .models import Workflow, WorkflowAction, WorkflowRun, WorkflowStepLog


class WorkflowActionInline(admin.TabularInline):
    model = WorkflowAction
    extra = 0
    fields = ["position", "action_type", "config"]


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ["name", "trigger_type", "is_active", "run_count", "last_run_at", "org"]
    list_filter = ["org", "is_active", "trigger_type"]
    inlines = [WorkflowActionInline]
    readonly_fields = ["webhook_token", "run_count", "last_run_at"]


class StepInline(admin.TabularInline):
    model = WorkflowStepLog
    extra = 0
    readonly_fields = ["position", "action_type", "status", "message", "created_at"]


@admin.register(WorkflowRun)
class WorkflowRunAdmin(admin.ModelAdmin):
    list_display = ["workflow", "contact", "deal", "status", "current_step", "resume_at", "created_at"]
    list_filter = ["org", "status"]
    raw_id_fields = ["contact", "deal", "trigger_activity"]
    inlines = [StepInline]
