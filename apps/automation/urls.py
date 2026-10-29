from django.urls import path

from . import views

app_name = "automation"

urlpatterns = [
    path("automations/", views.WorkflowListView.as_view(), name="workflow_list"),
    path("automations/new/", views.WorkflowBuilderView.as_view(), name="workflow_create"),
    path("automations/<int:pk>/", views.WorkflowDetailView.as_view(), name="workflow_detail"),
    path("automations/<int:pk>/edit/", views.WorkflowBuilderView.as_view(), name="workflow_edit"),
    path("automations/<int:pk>/toggle/", views.WorkflowToggleView.as_view(), name="workflow_toggle"),
    path("automations/<int:pk>/test/", views.WorkflowTestRunView.as_view(), name="workflow_test"),
    path("automations/<int:pk>/delete/", views.WorkflowDeleteView.as_view(), name="workflow_delete"),
    path("automations/runs/<int:pk>/", views.RunDetailView.as_view(), name="run_detail"),
    path("automations/runs/<int:pk>/cancel/", views.RunCancelView.as_view(), name="run_cancel"),
    path("hooks/workflows/<str:token>/", views.WebhookTriggerView.as_view(), name="webhook"),
]
