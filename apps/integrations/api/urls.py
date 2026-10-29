from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter(trailing_slash=True)
router.include_root_view = False
router.register("contacts", views.ContactViewSet, basename="contact")
router.register("companies", views.CompanyViewSet, basename="company")
router.register("deals", views.DealViewSet, basename="deal")
router.register("tasks", views.TaskViewSet, basename="task")
router.register("notes", views.NoteViewSet, basename="note")
router.register("activities", views.ActivityViewSet, basename="activity")
router.register("pipelines", views.PipelineViewSet, basename="pipeline")
router.register("tags", views.TagViewSet, basename="tag")
router.register("members", views.MemberViewSet, basename="member")

app_name = "api"

urlpatterns = [
    path("v1/me/", views.MeView.as_view(), name="me"),
    path("v1/", include(router.urls)),
    path("v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api:schema"), name="docs"),
]
