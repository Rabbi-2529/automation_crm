from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("health/", views.HealthView.as_view(), name="health"),
    path("system/", views.SystemStatusView.as_view(), name="system"),
    path("system/ping/", views.RunPingTaskView.as_view(), name="system_ping"),
]
