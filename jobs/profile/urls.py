from django.urls import path

from . import views

urlpatterns = [
    path("", views.profile_page, name="profile"),
    path("settings/", views.save_settings, name="profile-settings"),
    path("manual/", views.save_manual_profile, name="profile-manual"),
    path("upload/", views.upload, name="profile-upload"),
    path("resume/<int:resume_id>/text/", views.manual_resume_text, name="profile-resume-text"),
    path("extract/<int:resume_id>/", views.extract, name="profile-extract"),
    path("confirm/", views.confirm_draft, name="profile-confirm"),
]
