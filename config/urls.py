from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("org/", include("apps.orgs.urls")),
    path("activity/", include("apps.activities.urls")),
    path("tasks/", include("apps.tasks.urls")),
    path("", include("apps.automation.urls")),
    path("", include("apps.leads.urls")),
    path("", include("apps.campaigns.urls")),
    path("", include("apps.messaging.urls")),
    path("", include("apps.pipeline.urls")),
    path("", include("apps.contacts.urls")),
    path("", include("apps.core.urls")),
]
