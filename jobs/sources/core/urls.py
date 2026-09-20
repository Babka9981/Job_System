from django.urls import include, path

from jobs.sources.core.views import sources_view


urlpatterns = [
    path("telegram/", include("jobs.sources.telegram.urls")),
    path("", sources_view, name="sources"),
]
