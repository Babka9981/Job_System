from django.contrib import admin
from django.urls import include, path

handler403 = "jobs.core.views.permission_denied"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("jobs.core.urls")),
]
