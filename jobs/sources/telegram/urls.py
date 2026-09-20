from django.urls import path

from jobs.sources.telegram.views import telegram_sources_view


urlpatterns = [
    path("", telegram_sources_view, name="manage"),
]

