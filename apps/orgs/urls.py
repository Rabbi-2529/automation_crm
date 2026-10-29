from django.urls import path

from . import views

app_name = "orgs"

urlpatterns = [
    path("new/", views.OrganizationCreateView.as_view(), name="create"),
    path("settings/", views.OrganizationSettingsView.as_view(), name="settings"),
    path("switch/<int:org_id>/", views.SwitchOrganizationView.as_view(), name="switch"),
    path("members/", views.MemberListView.as_view(), name="members"),
    path("members/invite/", views.MemberInviteView.as_view(), name="member_invite"),
    path("members/<int:pk>/", views.MemberUpdateView.as_view(), name="member_edit"),
    path("teams/", views.TeamListView.as_view(), name="teams"),
    path("teams/new/", views.TeamCreateView.as_view(), name="team_create"),
    path("teams/<int:pk>/", views.TeamUpdateView.as_view(), name="team_edit"),
    path("teams/<int:pk>/delete/", views.TeamDeleteView.as_view(), name="team_delete"),
]
