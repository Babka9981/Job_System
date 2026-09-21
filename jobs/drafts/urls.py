from django.urls import path

from . import views

urlpatterns = [
    path("vacancies/<int:vacancy_id>/drafts/", views.draft_page, name="vacancy-drafts"),
    path("vacancies/<int:vacancy_id>/drafts/<str:kind>/generate/", views.draft_generate, name="draft-generate"),
    path("vacancies/<int:vacancy_id>/drafts/<str:kind>/save/", views.draft_save, name="draft-save"),
]
