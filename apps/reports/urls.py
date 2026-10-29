from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("reports/", views.ReportsDashboardView.as_view(), name="overview"),
    path("reports/leaderboard/", views.LeaderboardView.as_view(), name="leaderboard"),
    path("reports/leaderboard.csv", views.LeaderboardExportView.as_view(), name="leaderboard_export"),
    path("reports/closed-deals.csv", views.ClosedDealsExportView.as_view(), name="closed_deals_export"),
    path("reports/targets/", views.TargetListView.as_view(), name="targets"),
    path("reports/targets/new/", views.TargetEditView.as_view(), name="target_create"),
    path("reports/targets/<int:pk>/", views.TargetEditView.as_view(), name="target_edit"),
    path("reports/targets/<int:pk>/delete/", views.TargetDeleteView.as_view(), name="target_delete"),
    path("reports/emails/", views.MyReportsView.as_view(), name="my_reports"),
]
