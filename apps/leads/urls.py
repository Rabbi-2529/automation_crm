from django.urls import path

from . import views

app_name = "leads"

urlpatterns = [
    path("forms/", views.WebFormListView.as_view(), name="form_list"),
    path("forms/new/", views.WebFormEditView.as_view(), name="form_create"),
    path("forms/<int:pk>/", views.WebFormDetailView.as_view(), name="form_detail"),
    path("forms/<int:pk>/edit/", views.WebFormEditView.as_view(), name="form_edit"),
    path("forms/<int:pk>/delete/", views.WebFormDeleteView.as_view(), name="form_delete"),
    path("lead-scoring/", views.ScoringView.as_view(), name="scoring"),
    path("lead-scoring/rules/new/", views.ScoringRuleEditView.as_view(), name="rule_create"),
    path("lead-scoring/rules/<int:pk>/", views.ScoringRuleEditView.as_view(), name="rule_edit"),
    path("lead-scoring/rules/<int:pk>/delete/", views.ScoringRuleDeleteView.as_view(), name="rule_delete"),
    # Public, no login.
    path("f/<str:token>/", views.PublicFormView.as_view(), name="public_form"),
]
