from django.contrib import admin

from .models import CallLog, ChannelSettings, Conversation, Message, MessageTemplate


@admin.register(ChannelSettings)
class ChannelSettingsAdmin(admin.ModelAdmin):
    list_display = ["org", "smtp_host", "imap_enabled", "sms_provider", "imap_last_checked_at"]
    # Secrets stay out of the admin; workspace admins manage them in Settings → Email & SMS.
    exclude = ["smtp_password", "imap_password", "twilio_auth_token", "http_api_key"]
    readonly_fields = ["imap_last_uid", "imap_last_checked_at", "imap_last_error", "webhook_token"]


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "channel", "org"]
    list_filter = ["org", "channel"]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["__str__", "channel", "contact", "last_message_at", "is_unread", "org"]
    list_filter = ["org", "channel", "is_unread"]
    raw_id_fields = ["contact"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["__str__", "status", "subject", "sent_at", "open_count", "click_count", "org"]
    list_filter = ["org", "channel", "direction", "status"]
    raw_id_fields = ["conversation", "contact", "deal", "sent_by", "template"]


@admin.register(CallLog)
class CallLogAdmin(admin.ModelAdmin):
    list_display = ["contact", "outcome", "duration_minutes", "called_at", "user", "org"]
    raw_id_fields = ["contact", "deal", "user"]
