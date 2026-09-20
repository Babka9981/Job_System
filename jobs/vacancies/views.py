import hashlib

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from jobs.core.access import owner_required
from jobs.intelligence.research import research as research_company
from jobs.models.models import Source, Vacancy

from .forms import ManualVacancyForm
from .services import (
    VersionConflict,
    change_availability,
    change_note,
    change_status,
    change_visibility,
    sanitize_source_text,
    upsert_record,
)


@owner_required
def vacancy_list(request):
    vacancies = Vacancy.objects.filter(owner=request.user).prefetch_related("source_records__source")
    query = request.GET.get("q", "").strip()
    role = request.GET.get("role", "").strip()
    source = request.GET.get("source", "").strip()
    priority = request.GET.get("priority", "").strip()
    status = request.GET.get("status", "").strip()
    visibility = request.GET.get("visibility", "visible").strip()
    if query:
        vacancies = vacancies.filter(
            Q(title__icontains=query)
            | Q(company__icontains=query)
            | Q(role__icontains=query)
            | Q(description__icontains=query)
        )
    if role:
        vacancies = vacancies.filter(role=role)
    if source:
        vacancies = vacancies.filter(source_records__source__slug=source)
    if priority in Vacancy.Priority.values:
        vacancies = vacancies.filter(priority=priority)
    if status in Vacancy.UserStatus.values:
        vacancies = vacancies.filter(user_status=status)
    if visibility == "hidden":
        vacancies = vacancies.filter(hidden=True)
    elif visibility != "all":
        vacancies = vacancies.filter(hidden=False)
    vacancies = vacancies.order_by_priority().order_by(
        "_priority_rank",
        F("published_at").desc(nulls_last=True),
        "-first_seen_at",
    ).distinct()
    paginator = Paginator(vacancies, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)
    return render(
        request,
        "vacancies/list.html",
        {
            "section": "vacancies",
            "title": "Вакансии",
            "page_obj": page_obj,
            "manual_form": ManualVacancyForm(),
            "roles": Vacancy.objects.filter(owner=request.user).exclude(role="").values_list("role", flat=True).distinct().order_by("role"),
            "sources": Source.objects.filter(owner=request.user).order_by("name"),
            "priorities": Vacancy.Priority.choices,
            "statuses": Vacancy.UserStatus.choices,
            "filters": {"q": query, "role": role, "source": source, "priority": priority, "status": status, "visibility": visibility},
            "query_without_page": query_without_page.urlencode(),
            "user_time_zone": settings.USER_TIME_ZONE,
        },
    )


@owner_required
def vacancy_detail(request, vacancy_id):
    vacancy = get_object_or_404(
        Vacancy.objects.prefetch_related("source_records__source"),
        pk=vacancy_id,
        owner=request.user,
    )
    return _render_detail(request, vacancy)


def _render_detail(request, vacancy, *, status=200, state="", error="", submitted=None):
    submitted = submitted or {}
    latest_research = vacancy.research.order_by("-created_at").first()
    profile = getattr(request.user, "job_profile", None)
    if latest_research and profile:
        cached_role = latest_research.role or vacancy.role or vacancy.title
        covered_roles = {
            str(value).casefold()
            for value in latest_research.coverage.get("roles", [])
        }
        cache_is_reusable = (
            latest_research.status in (latest_research.Status.COMPLETE, latest_research.Status.PARTIAL)
            and latest_research.expires_at
            and latest_research.expires_at > timezone.now()
            and bool(latest_research.domain)
            and cached_role.casefold() in covered_roles
        )
        if cache_is_reusable and latest_research.coverage.get("profile_version") != profile.version:
            latest_research = research_company(
                vacancy,
                company=latest_research.company,
                domain=latest_research.domain,
                role=cached_role,
                refresh=False,
                profile=profile,
            )
    form_values = {
        "status": submitted.get("status", vacancy.user_status),
        "closing_note": submitted.get("note", vacancy.closing_note),
        "availability": submitted.get("availability", vacancy.availability),
        "user_note": submitted.get("user_note", vacancy.user_note),
    }
    return render(
        request,
        "vacancies/detail.html",
        {
            "section": "vacancies",
            "title": vacancy.title or "Вакансия",
            "vacancy": vacancy,
            "latest_research": latest_research,
            "statuses": Vacancy.UserStatus.choices,
            "availability_choices": Vacancy.Availability.choices,
            "state": state,
            "error": error,
            "form_values": form_values,
            "user_time_zone": settings.USER_TIME_ZONE,
        },
        status=status,
    )


def _posted_version(request):
    try:
        version = int(request.POST.get("version", ""))
    except (TypeError, ValueError):
        raise ValidationError("Некорректная версия вакансии.")
    if version < 1:
        raise ValidationError("Некорректная версия вакансии.")
    return version


def _apply_versioned_action(request, vacancy_id, action):
    get_object_or_404(Vacancy, pk=vacancy_id, owner=request.user)
    try:
        action(_posted_version(request))
    except VersionConflict as conflict:
        return _render_detail(request, conflict.vacancy, status=409, state="conflict", submitted=request.POST)
    except ValidationError as exc:
        vacancy = get_object_or_404(Vacancy, pk=vacancy_id, owner=request.user)
        return _render_detail(request, vacancy, status=400, error=" ".join(exc.messages), submitted=request.POST)
    return redirect("vacancy-detail", vacancy_id=vacancy_id)


@owner_required
@require_POST
def vacancy_status(request, vacancy_id):
    return _apply_versioned_action(
        request,
        vacancy_id,
        lambda version: change_status(
            vacancy_id,
            version,
            request.POST.get("status", ""),
            request.POST.get("note", ""),
            owner=request.user,
        ),
    )


@owner_required
@require_POST
def vacancy_visibility(request, vacancy_id):
    return _apply_versioned_action(
        request,
        vacancy_id,
        lambda version: change_visibility(
            vacancy_id,
            version,
            request.POST.get("hidden") == "1",
            owner=request.user,
        ),
    )


@owner_required
@require_POST
def vacancy_availability(request, vacancy_id):
    return _apply_versioned_action(
        request,
        vacancy_id,
        lambda version: change_availability(
            vacancy_id,
            version,
            request.POST.get("availability", ""),
            owner=request.user,
        ),
    )


@owner_required
@require_POST
def vacancy_note(request, vacancy_id):
    return _apply_versioned_action(
        request,
        vacancy_id,
        lambda version: change_note(
            vacancy_id,
            version,
            request.POST.get("user_note", ""),
            owner=request.user,
        ),
    )


@owner_required
@require_POST
def vacancy_research(request, vacancy_id):
    vacancy = get_object_or_404(Vacancy, pk=vacancy_id, owner=request.user)
    result = research_company(
        vacancy,
        company=vacancy.company,
        domain=request.POST.get("domain", ""),
        role=vacancy.role or vacancy.title,
        refresh=request.POST.get("refresh") == "1",
    )
    if result.domain and result.domain != vacancy.company_domain:
        Vacancy.objects.filter(pk=vacancy.pk, owner=request.user).update(
            company_domain=result.domain,
            version=F("version") + 1,
            updated_at=timezone.now(),
        )
    return redirect("vacancy-detail", vacancy_id=vacancy.id)


@owner_required
@require_http_methods(["POST"])
def vacancy_add(request):
    form = ManualVacancyForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "vacancies/list.html",
            {"section": "vacancies", "title": "Вакансии", "manual_form": form},
            status=400,
        )
    source, _ = Source.objects.get_or_create(
        owner=request.user,
        slug="manual",
        defaults={
            "name": "Ручное добавление",
            "kind": "manual",
            "adapter": "manual",
            "status": Source.Status.READY,
            "llm_permission": False,
        },
    )
    url = form.cleaned_data["url"]
    description = sanitize_source_text(form.cleaned_data.get("text", ""))
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    raw_hash = hashlib.sha256(description.encode("utf-8")).hexdigest() if description.strip() else ""
    result = upsert_record(
        {
            "source_slug": source.slug,
            "external_id": f"manual:{digest}",
            "canonical_url": url,
            "apply_url": url,
            "title": form.cleaned_data.get("title", ""),
            "company": form.cleaned_data.get("company", ""),
            "description": description,
            "description_permission": form.cleaned_data.get("description_permission", False),
            "salary": {},
            "raw_hash": raw_hash,
            "attribution": {"method": "manual", "label": "Добавлено вручную", "url": url},
        },
        owner=request.user,
    )
    return redirect("vacancy-detail", vacancy_id=result.vacancy.id)
