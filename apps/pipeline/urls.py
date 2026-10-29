from django.urls import path

from . import views

app_name = "pipeline"

urlpatterns = [
    path("deals/", views.DealBoardView.as_view(), name="deal_board"),
    path("deals/list/", views.DealListView.as_view(), name="deal_list"),
    path("deals/table/", views.DealTableView.as_view(), name="deal_table"),
    path("deals/new/", views.DealCreateView.as_view(), name="deal_create"),
    path("deals/<int:pk>/", views.DealDetailView.as_view(), name="deal_detail"),
    path("deals/<int:pk>/edit/", views.DealUpdateView.as_view(), name="deal_edit"),
    path("deals/<int:pk>/delete/", views.DealDeleteView.as_view(), name="deal_delete"),
    path("deals/<int:pk>/move/", views.DealMoveView.as_view(), name="deal_move"),
    path("deals/<int:pk>/won/", views.DealWonView.as_view(), name="deal_won"),
    path("deals/<int:pk>/lost/", views.DealLostView.as_view(), name="deal_lost"),
    path("deals/<int:pk>/reopen/", views.DealReopenView.as_view(), name="deal_reopen"),
    path("settings/pipelines/", views.PipelineListView.as_view(), name="pipeline_list"),
    path("settings/pipelines/new/", views.PipelineCreateView.as_view(), name="pipeline_create"),
    path("settings/pipelines/<int:pk>/", views.PipelineUpdateView.as_view(), name="pipeline_edit"),
    path("settings/pipelines/<int:pk>/delete/", views.PipelineDeleteView.as_view(), name="pipeline_delete"),
    path("settings/lost-reasons/new/", views.LostReasonCreateView.as_view(), name="lost_reason_create"),
    path("settings/lost-reasons/<int:pk>/", views.LostReasonUpdateView.as_view(), name="lost_reason_edit"),
    path("settings/lost-reasons/<int:pk>/delete/", views.LostReasonDeleteView.as_view(), name="lost_reason_delete"),
]
