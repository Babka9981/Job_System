from django.urls import path

from .views import health

urlpatterns = [path("healthz/", health, name="health")]
