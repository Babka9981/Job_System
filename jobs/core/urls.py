from django.contrib.auth.views import LogoutView
from django.urls import include, path
from .views import RateLimitedLoginView, section

urlpatterns = [
    path("accounts/login/", RateLimitedLoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("profile/", include("jobs.profile.urls")),
    path("sources/", lambda request: section(request, "sources"), name="sources"),
    path("", include("jobs.vacancies.urls")),
]
