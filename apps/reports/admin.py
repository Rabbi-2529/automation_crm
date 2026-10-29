from django.contrib import admin

from .models import ReportSubscription, SalesTarget


@admin.register(SalesTarget)
class SalesTargetAdmin(admin.ModelAdmin):
    list_display = ["__str__", "metric", "period", "period_start", "value", "org"]
    list_filter = ["org", "metric", "period"]


@admin.register(ReportSubscription)
class ReportSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["user", "kind", "frequency", "hour", "is_active", "last_sent_at", "org"]
    list_filter = ["org", "kind", "frequency"]
