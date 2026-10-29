from django.contrib import admin

from .models import ApiKey, FacebookLead, FacebookPage, WebhookDelivery, WebhookEndpoint

# Secrets (key hashes, signing secrets, access tokens) stay out of the admin; workspace admins use Settings → Integrations.


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "prefix", "org", "scope", "created_by", "last_used_at", "revoked_at"]
    list_filter = ["scope", "org"]
    fields = ["org", "name", "prefix", "scope", "created_by", "last_used_at", "revoked_at"]
    readonly_fields = fields


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ["url", "org", "is_active", "consecutive_failures", "last_delivery_at"]
    list_filter = ["is_active", "org"]
    exclude = ["secret"]


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ["event", "endpoint", "status", "attempts", "response_status", "created_at"]
    list_filter = ["status", "event"]
    readonly_fields = ["endpoint", "event", "event_id", "payload", "status", "attempts", "response_status", "response_body", "error",
                       "next_attempt_at", "delivered_at"]


@admin.register(FacebookPage)
class FacebookPageAdmin(admin.ModelAdmin):
    list_display = ["name", "page_id", "org", "is_active", "lead_count", "last_lead_at"]
    exclude = ["access_token"]


@admin.register(FacebookLead)
class FacebookLeadAdmin(admin.ModelAdmin):
    list_display = ["leadgen_id", "page", "status", "contact", "created_at"]
    list_filter = ["status"]
    readonly_fields = ["field_data"]
