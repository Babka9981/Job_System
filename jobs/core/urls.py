from django.contrib.auth.views import LogoutView
from django.urls import path
from .views import RateLimitedLoginView, section

urlpatterns = [
    path("accounts/login/", RateLimitedLoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("", lambda request: section(request, "vacancies"), name="vacancies"),
    path("profile/", lambda request: section(request, "profile"), name="profile"),
    path("sources/", lambda request: section(request, "sources"), name="sources"),
]
