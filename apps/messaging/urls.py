from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("inbox/", views.InboxView.as_view(), name="inbox"),
    path("inbox/<int:pk>/reply/", views.ConversationReplyView.as_view(), name="reply"),
    path("inbox/<int:pk>/archive/", views.ConversationArchiveView.as_view(), name="archive"),
    path("inbox/<int:pk>/save-contact/", views.ConversationCreateContactView.as_view(), name="create_contact"),
    path("messages/new/", views.ComposeView.as_view(), name="compose"),
    path("messages/templates/", views.TemplateListView.as_view(), name="template_list"),
    path("messages/templates/new/", views.TemplateCreateView.as_view(), name="template_create"),
    path("messages/templates/<int:pk>/", views.TemplateUpdateView.as_view(), name="template_edit"),
    path("messages/templates/<int:pk>/delete/", views.TemplateDeleteView.as_view(), name="template_delete"),
    path("messages/templates/<int:pk>/preview/", views.TemplatePreviewView.as_view(), name="template_preview"),
    path("calls/new/", views.CallLogCreateView.as_view(), name="call_create"),
    path("settings/channels/", views.ChannelSettingsView.as_view(), name="channel_settings"),
    path("settings/channels/test/", views.ChannelTestView.as_view(), name="channel_test"),
    # Public: hit by email clients and Twilio, not by logged-in users.
    path("t/o/<uuid:tracking_id>.gif", views.track_open, name="track_open"),
    path("t/c/<str:token>/", views.track_click, name="track_click"),
    path("u/<str:token>/", views.UnsubscribeView.as_view(), name="unsubscribe"),
    path("webhooks/twilio/<str:token>/sms/", views.TwilioSmsWebhook.as_view(), name="twilio_sms"),
    path("webhooks/whatsapp/<str:token>/", views.WhatsAppWebhook.as_view(), name="whatsapp_webhook"),
]
