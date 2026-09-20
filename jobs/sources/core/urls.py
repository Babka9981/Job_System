from django.urls import path

from jobs.sources.core.views import sources_view


urlpatterns = [
    path("", sources_view, name="sources"),
]
