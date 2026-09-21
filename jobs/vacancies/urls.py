from django.urls import include, path

from . import views


urlpatterns = [
    path("", include("jobs.drafts.urls")),
    path("", views.vacancy_list, name="vacancies"),
    path("vacancies/add/", views.vacancy_add, name="vacancy-add"),
    path("vacancies/<int:vacancy_id>/", views.vacancy_detail, name="vacancy-detail"),
    path("vacancies/<int:vacancy_id>/status/", views.vacancy_status, name="vacancy-status"),
    path("vacancies/<int:vacancy_id>/visibility/", views.vacancy_visibility, name="vacancy-visibility"),
    path("vacancies/<int:vacancy_id>/availability/", views.vacancy_availability, name="vacancy-availability"),
    path("vacancies/<int:vacancy_id>/note/", views.vacancy_note, name="vacancy-note"),
    path("vacancies/<int:vacancy_id>/research/", views.vacancy_research, name="vacancy-research"),
]
