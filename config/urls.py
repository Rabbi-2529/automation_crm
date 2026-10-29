from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("org/", include("apps.orgs.urls")),
    path("", include("apps.core.urls")),
]
