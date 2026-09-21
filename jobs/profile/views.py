import json
import os

from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from jobs.core.access import owner_required
from jobs.intelligence.gateway import GatewayError, GatewayUnavailable, OpenAIGateway
from jobs.models.models import Profile, Resume
from .extractors import ResumeExtractionError
from .forms import ManualProfileForm, ManualResumeTextForm, ProfileSettingsForm, ResumeUploadForm, split_values
from .services import (
    ProfileVersionConflict,
    ResumeValidationError,
    confirm_manual_profile,
    confirm_profile,
    extract_profile,
    load_profile_draft,
    set_manual_resume_text,
    update_profile_settings,
    upload_resume,
)


DEFAULT_CRITERIA = {
    "roles": [
        "Product Manager", "Продакт-менеджер", "Менеджер продукта", "Product Owner", "Product Lead", "Head of Product",
        "Project Manager", "Проджект-менеджер", "Руководитель проектов", "IT Project Manager", "Technical Project Manager",
        "Руководитель службы поддержки", "Head of Support", "Customer Support Manager", "Support Team Lead",
        "Customer Service Manager", "Customer Service Director",
    ],
    "industries": ["fintech", "payments", "banking", "crypto", "DeFi", "Web3", "wallets", "exchanges", "crypto infrastructure"],
    "salary": {"target": "5000.00", "currency": "USD", "period": "month", "basis": "unknown"},
    "work_modes": ["remote", "relocation"],
    "residence_country": "",
    "hiring_countries": [],
    "timezone": "",
    "languages": [],
}
DEFAULT_PREFERENCES = {
    "response_language": "ru", "tone": "professional", "length": "short", "emphasis": "",
    "schedule": ["09:00", "13:00", "17:00", "21:00"], "daily_budget_usd": "0.0000",
    "openai_model": "gpt-5.6-luna", "search_provider": "auto",
}


def _profile(owner):
    return Profile.objects.get_or_create(owner=owner, defaults={"criteria": DEFAULT_CRITERIA, "preferences": DEFAULT_PREFERENCES})[0]


def _settings_initial(profile):
    criteria = {**DEFAULT_CRITERIA, **profile.criteria}
    preferences = {**DEFAULT_PREFERENCES, **profile.preferences}
    salary = {**DEFAULT_CRITERIA["salary"], **criteria.get("salary", {})}
    return {
        "version": profile.version,
        "roles": "\n".join(criteria.get("roles", [])),
        "industries": ", ".join(criteria.get("industries", [])),
        "salary_target": salary["target"], "salary_currency": salary["currency"],
        "salary_period": salary["period"], "salary_basis": salary["basis"],
        "work_modes": criteria.get("work_modes", []), "residence_country": criteria.get("residence_country", ""),
        "hiring_countries": ", ".join(criteria.get("hiring_countries", [])),
        "timezone": criteria.get("timezone", ""), "languages": ", ".join(criteria.get("languages", [])),
        "response_language": preferences["response_language"], "tone": preferences["tone"],
        "length": preferences["length"], "emphasis": preferences["emphasis"],
        "schedule": ", ".join(preferences["schedule"]), "daily_budget_usd": preferences["daily_budget_usd"],
        "openai_model": preferences["openai_model"], "search_provider": preferences["search_provider"],
    }


def _manual_initial(profile):
    facts = profile.facts.filter(confirmed=True, profile_version=profile.confirmed_version)
    by_kind = {
        kind: "\n".join(facts.filter(kind=kind).values_list("text", flat=True))
        for kind in ["experience", "achievement", "case", "language"]
    }
    return {
        "version": profile.version, "about": profile.about,
        "experience": by_kind["experience"], "achievements": by_kind["achievement"],
        "cases": by_kind["case"], "languages": by_kind["language"],
    }


def _render(request, *, settings_form=None, manual_form=None, upload_form=None, status=200):
    profile = _profile(request.user)
    latest_draft = None
    unconfirmed = profile.facts.filter(confirmed=False, kind="about").order_by("-profile_version").first()
    if unconfirmed:
        try:
            token = unconfirmed.source.split("|", 1)[0].removeprefix("draft:")
            latest_draft = load_profile_draft(profile, unconfirmed.profile_version, token)
        except ValueError:
            pass
    context = {
        "section": "profile", "title": "Мой профиль", "profile": profile,
        "settings_form": settings_form or ProfileSettingsForm(initial=_settings_initial(profile)),
        "manual_form": manual_form or ManualProfileForm(initial=_manual_initial(profile)),
        "upload_form": upload_form or ResumeUploadForm(), "resumes": profile.resumes.order_by("-created_at")[:10],
        "draft": latest_draft,
        "unresolved_questions": profile.facts.filter(
            confirmed=False, kind="question", source__startswith="review:"
        ).order_by("created_at", "id"),
    }
    return render(request, "profile/profile.html", context, status=status)


@owner_required
def profile_page(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return _render(request)


@owner_required
def save_settings(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    profile = _profile(request.user)
    form = ProfileSettingsForm(request.POST)
    if not form.is_valid():
        return _render(request, settings_form=form, status=400)
    data = form.cleaned_data
    criteria = {
        "roles": split_values(data["roles"]), "industries": split_values(data["industries"]),
        "salary": {"target": str(data["salary_target"]), "currency": data["salary_currency"].upper(), "period": data["salary_period"], "basis": data["salary_basis"]},
        "work_modes": data["work_modes"], "residence_country": data["residence_country"].strip(),
        "hiring_countries": split_values(data["hiring_countries"]), "timezone": data["timezone"].strip(),
        "languages": split_values(data["languages"]),
    }
    preferences = {
        "response_language": data["response_language"], "tone": data["tone"], "length": data["length"],
        "emphasis": data["emphasis"].strip(), "schedule": data["schedule"],
        "schedule_enabled": bool(data["timezone"].strip() and data["schedule"]),
        "daily_budget_usd": str(data["daily_budget_usd"]),
        "openai_model": data["openai_model"], "search_provider": data["search_provider"],
    }
    try:
        update_profile_settings(profile, criteria=criteria, preferences=preferences, expected_version=data["version"])
    except ProfileVersionConflict as exc:
        form.add_error(None, str(exc))
        return _render(request, settings_form=form, status=409)
    messages.success(request, "Настройки сохранены.")
    return redirect("profile")


@owner_required
def save_manual_profile(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    profile = _profile(request.user)
    form = ManualProfileForm(request.POST)
    if not form.is_valid():
        return _render(request, manual_form=form, status=400)
    facts = []
    for field, kind in [("experience", "experience"), ("achievements", "achievement"), ("cases", "case"), ("languages", "language")]:
        facts.extend({"kind": kind, "text": line, "source": "manual"} for line in form.cleaned_data[field].splitlines() if line.strip())
    try:
        confirm_manual_profile(profile, about=form.cleaned_data["about"], facts=facts, expected_version=form.cleaned_data["version"])
    except ProfileVersionConflict as exc:
        form.add_error(None, str(exc))
        return _render(request, manual_form=form, status=409)
    messages.success(request, "Проверенный профиль сохранён.")
    return redirect("profile")


@owner_required
def upload(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    profile = _profile(request.user)
    form = ResumeUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render(request, upload_form=form, status=400)
    try:
        resume = upload_resume(profile, form.cleaned_data["resume"])
    except ResumeValidationError as exc:
        form.add_error("resume", str(exc))
        return _render(request, upload_form=form, status=400)
    if resume.extraction_state == Resume.ExtractionState.ERROR:
        messages.warning(request, "Текст не извлечён. Оригинал сохранён; заполните профиль вручную.")
    else:
        messages.success(request, "Резюме загружено. Проверьте извлечённый текст перед LLM-анализом.")
    return redirect("profile")


@owner_required
def manual_resume_text(request, resume_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    profile = _profile(request.user)
    resume = get_object_or_404(Resume, pk=resume_id, profile=profile)
    form = ManualResumeTextForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Вставьте непустой текст резюме допустимой длины.")
        return redirect("profile")
    try:
        set_manual_resume_text(resume, form.cleaned_data["text"])
    except ResumeValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Ручной текст сохранён. Теперь можно создать LLM-черновик.")
    return redirect("profile")


def _gateway(*, model):
    try:
        prices = json.loads(os.environ.get("OPENAI_PRICES_JSON", "{}"))
    except ValueError:
        prices = {}
    return OpenAIGateway(model=model, prices=prices)


@owner_required
def extract(request, resume_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    profile = _profile(request.user)
    resume = get_object_or_404(Resume, pk=resume_id, profile=profile)
    try:
        extract_profile(
            resume,
            gateway=_gateway(model=profile.preferences.get("openai_model", "gpt-5.6-luna")),
            daily_limit=profile.preferences.get("daily_budget_usd"),
        )
    except GatewayUnavailable as exc:
        if exc.code == "budget_pending":
            messages.warning(request, str(exc))
        else:
            messages.error(request, str(exc))
    except (GatewayError, ResumeExtractionError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Черновик извлечён. Проверьте каждый факт перед подтверждением.")
    return redirect("profile")


@owner_required
def confirm_draft(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    profile = _profile(request.user)
    try:
        proposed = int(request.POST["proposed_version"])
        expected = int(request.POST["version"])
        token = request.POST["draft_token"]
        draft = load_profile_draft(profile, proposed, token)
        edits = {fact.pk: request.POST.get(f"fact_{fact.pk}", fact.text) for fact in draft.facts}
        confirm_profile(draft, expected_version=expected, about=request.POST.get("about", draft.about), fact_edits=edits)
    except (KeyError, ValueError, ProfileVersionConflict) as exc:
        messages.error(request, str(exc))
        return _render(request, status=409)
    messages.success(request, "Черновик проверен и подтверждён.")
    return redirect("profile")
