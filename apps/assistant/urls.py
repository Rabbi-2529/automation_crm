from django.db import transaction
from django.urls import path

from . import views

app_name = "assistant"

# AI calls can take a while; don't hold a database transaction open while waiting for the model.
slow = transaction.non_atomic_requests

urlpatterns = [
    path("help/", views.HelpCenterView.as_view(), name="help_center"),
    path("help/ask/", slow(views.HelpAskView.as_view()), name="help_ask"),
    path("help/feedback/", views.HelpFeedbackView.as_view(), name="help_feedback"),
    path("help/<slug:slug>/", views.HelpArticleView.as_view(), name="help_article"),
    path("inbox/<int:pk>/ai-draft/", slow(views.ReplyDraftView.as_view()), name="reply_draft"),
    path("settings/ai-assistant/", views.AssistantSettingsView.as_view(), name="ai_settings"),
]
