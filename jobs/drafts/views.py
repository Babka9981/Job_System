from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from jobs.core.access import owner_required
from jobs.models.models import Draft, Vacancy

from .services import (
    COVER_LETTER,
    DRAFT_KINDS,
    RECRUITER_MESSAGE,
    DraftConflict,
    DraftGenerationError,
    ProfileRequired,
    generate,
    save_edit,
)


def _latest(vacancy, kind):
    return vacancy.drafts.filter(kind=kind).order_by("-updated_at", "-pk").first()


def _render(request, vacancy, *, status=200, error="", notice="", error_kind=""):
    profile = getattr(request.user, "job_profile", None)
    profile_ready = bool(profile and profile.confirmed_version > 0 and profile.confirmed_version == profile.version)
    return render(request, "vacancies/drafts/index.html", {
        "section": "vacancies",
        "title": f"Отклики · {vacancy.title}",
        "vacancy": vacancy,
        "profile_ready": profile_ready,
        "cover_draft": _latest(vacancy, COVER_LETTER),
        "recruiter_draft": _latest(vacancy, RECRUITER_MESSAGE),
        "cover_kind": COVER_LETTER,
        "recruiter_kind": RECRUITER_MESSAGE,
        "error": error,
        "error_kind": error_kind,
        "notice": notice,
    }, status=status)


@owner_required
def draft_page(request, vacancy_id):
    vacancy = get_object_or_404(Vacancy, pk=vacancy_id, owner=request.user)
    return _render(request, vacancy)


@owner_required
@require_POST
def draft_generate(request, vacancy_id, kind):
    vacancy = get_object_or_404(Vacancy, pk=vacancy_id, owner=request.user)
    if kind not in DRAFT_KINDS:
        return _render(request, vacancy, status=404, error="Неизвестный тип отклика.")
    profile = getattr(request.user, "job_profile", None)
    try:
        generate(
            vacancy, profile, kind,
            language=request.POST.get("language", "ru"),
            tone=request.POST.get("tone", "professional"),
            length=request.POST.get("length", "medium"),
            accent=request.POST.get("accent", "experience"),
            user_note=request.POST.get("user_note", ""),
            refresh=request.POST.get("refresh") == "1",
        )
    except (ProfileRequired, DraftConflict) as exc:
        return _render(request, vacancy, status=409, error=str(exc))
    except DraftGenerationError as exc:
        return _render(request, vacancy, status=503, error=str(exc), error_kind=kind)
    return redirect("vacancy-drafts", vacancy_id=vacancy.pk)


@owner_required
@require_POST
def draft_save(request, vacancy_id, kind):
    vacancy = get_object_or_404(Vacancy, pk=vacancy_id, owner=request.user)
    if kind not in DRAFT_KINDS:
        return _render(request, vacancy, status=404, error="Неизвестный тип отклика.")
    try:
        save_edit(
            vacancy, getattr(request.user, "job_profile", None), kind,
            request.POST.get("text", ""),
            expected_version=request.POST.get("expected_version"),
        )
    except (ProfileRequired, DraftConflict) as exc:
        return _render(request, vacancy, status=409, error=str(exc))
    return redirect("vacancy-drafts", vacancy_id=vacancy.pk)
