from django.urls import path

from . import views

app_name = "integrations"

urlpatterns = [
    path("settings/integrations/", views.IntegrationsView.as_view(), name="overview"),
    path("settings/integrations/api-keys/", views.ApiKeyCreateView.as_view(), name="api_key_create"),
    path("settings/integrations/api-keys/<int:pk>/revoke/", views.ApiKeyRevokeView.as_view(), name="api_key_revoke"),
    path("settings/integrations/webhooks/new/", views.WebhookEditView.as_view(), name="webhook_create"),
    path("settings/integrations/webhooks/<int:pk>/", views.WebhookDetailView.as_view(), name="webhook_detail"),
    path("settings/integrations/webhooks/<int:pk>/edit/", views.WebhookEditView.as_view(), name="webhook_edit"),
    path("settings/integrations/webhooks/<int:pk>/action/", views.WebhookActionView.as_view(), name="webhook_action"),
    path("settings/integrations/facebook/", views.FacebookSettingsView.as_view(), name="facebook_settings"),
    path("settings/integrations/facebook/pages/new/", views.FacebookPageEditView.as_view(), name="facebook_page_create"),
    path("settings/integrations/facebook/pages/<int:pk>/", views.FacebookPageEditView.as_view(), name="facebook_page_edit"),
    path("settings/integrations/facebook/leads/<int:pk>/retry/", views.FacebookLeadRetryView.as_view(), name="facebook_lead_retry"),
    # Public: called by Meta, not by logged-in users.
    path("webhooks/facebook/<str:token>/", views.FacebookWebhook.as_view(), name="facebook_webhook"),
]
