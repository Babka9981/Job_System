import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction

from jobs.models.models import Profile, ProfileFact, Resume
from .extractors import ResumeExtractionError, extract_resume_text


class ProfileVersionConflict(Exception):
    """Raised when a stale editor tries to overwrite a newer profile."""


class ResumeValidationError(Exception):
    pass


@dataclass(frozen=True)
class ProfileDraft:
    profile_id: int
    proposed_version: int
    token: str
    about: str
    facts: tuple
    questions: tuple


def upload_resume(profile, uploaded_file, *, max_bytes=None):
    limit = max_bytes or int(os.environ.get("JOB_CV_MAX_BYTES", 10 * 1024 * 1024))
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        raise ResumeValidationError("Поддерживаются только PDF и DOCX.")
    if uploaded_file.size > limit:
        raise ResumeValidationError(f"Файл превышает лимит {limit} байт.")

    head = uploaded_file.read(4)
    uploaded_file.seek(0)
    if (suffix == ".pdf" and head != b"%PDF") or (suffix == ".docx" and head[:2] != b"PK"):
        raise ResumeValidationError("Содержимое файла не соответствует его формату.")

    directory = Path(settings.PRIVATE_ROOT) / str(profile.owner_id) / "resumes"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{uuid.uuid4().hex}{suffix}"
    with destination.open("xb") as target:
        for chunk in uploaded_file.chunks():
            target.write(chunk)
    try:
        destination.chmod(0o600)
    except OSError:
        pass

    try:
        text = extract_resume_text(destination)
        state = Resume.ExtractionState.COMPLETE
    except ResumeExtractionError:
        text = ""
        state = Resume.ExtractionState.ERROR
    try:
        return Resume.objects.create(
            profile=profile,
            private_path=str(destination),
            text=text,
            extraction_state=state,
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise


@transaction.atomic
def set_manual_resume_text(resume, text):
    text = text.strip()
    limit = int(os.environ.get("JOB_CV_MANUAL_MAX_CHARS", "200000"))
    if not text:
        raise ResumeValidationError("Вставьте текст резюме.")
    if len(text) > limit:
        raise ResumeValidationError(f"Текст превышает лимит {limit} символов.")
    blocks = [block.strip() for block in text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    marked = "\n".join(f"[Блок {number}] {block}" for number, block in enumerate(blocks, start=1))
    current = Resume.objects.select_for_update().get(pk=resume.pk)
    current.text = marked
    current.extraction_state = Resume.ExtractionState.COMPLETE
    current.save(update_fields=["text", "extraction_state", "updated_at"])
    return current


PROFILE_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "about": {"type": "string", "description": "Краткое резюме только подтверждаемых текстом профессиональных фактов."},
        "facts": {
            "type": "array",
            "maxItems": 50,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["experience", "achievement", "case", "language"]},
                    "text": {"type": "string", "description": "Факт без догадок и превосходных степеней от модели."},
                    "source_reference": {"type": "string", "description": "Точный маркер Страница N или Блок N из входа."},
                    "page": {"type": "integer", "description": "Номер страницы PDF начиная с 1; для DOCX-блока — 0."},
                },
                "required": ["kind", "text", "source_reference", "page"],
                "additionalProperties": False,
            },
        },
        "questions": {"type": "array", "maxItems": 20, "items": {"type": "string"}, "description": "Противоречивые даты, текущая роль и иные вопросы, которые нельзя угадывать."},
    },
    "required": ["about", "facts", "questions"],
    "additionalProperties": False,
}


def _verified_reference(resume_text, source_reference, page):
    markers = {(kind, int(number)) for kind, number in re.findall(r"\[(Страница|Блок)\s+(\d+)\]", resume_text)}
    match = re.fullmatch(r"\[?(Страница|Блок)\s+(\d+)\]?", source_reference.strip())
    if not match:
        return False
    kind, number_text = match.groups()
    number = int(number_text)
    expected_page = number if kind == "Страница" else 0
    return (kind, number) in markers and page == expected_page


def extract_profile(resume, *, gateway, daily_limit):
    resume = Resume.objects.select_related("profile__owner").get(pk=resume.pk)
    if resume.extraction_state != Resume.ExtractionState.COMPLETE or not resume.text.strip():
        raise ResumeExtractionError("Нет извлечённого текста; вставьте профиль вручную.")
    profile = resume.profile
    result = gateway.structured(
        owner=profile.owner,
        operation="profile_extract",
        input_text=resume.text,
        schema=PROFILE_EXTRACTION_SCHEMA,
        daily_limit=daily_limit,
        max_input_tokens=int(os.environ.get("JOB_PROFILE_MAX_INPUT_TOKENS", "30000")),
        max_output_tokens=int(os.environ.get("JOB_PROFILE_MAX_OUTPUT_TOKENS", "8000")),
    )
    with transaction.atomic():
        profile = Profile.objects.select_for_update().get(pk=resume.profile_id)
        proposed_version = profile.version + 1
        draft_token = uuid.uuid4().hex
        ProfileFact.objects.filter(
            profile=profile,
            confirmed=False,
            source__startswith="draft:",
        ).delete()
        ProfileFact.objects.create(
            profile=profile,
            text=result["about"].strip(),
            kind="about",
            source=f"draft:{draft_token}|resume:{resume.pk}",
            profile_version=proposed_version,
            confirmed=False,
        )
        draft_facts = []
        questions = list(result["questions"])
        for item in result["facts"]:
            if not _verified_reference(resume.text, item["source_reference"], item["page"]):
                questions.append(f"Факт «{item['text']}» отклонён: не подтверждена ссылка {item['source_reference']}.")
                continue
            fact = ProfileFact.objects.create(
                profile=profile,
                text=item["text"].strip(),
                kind=item["kind"],
                source=f"draft:{draft_token}|resume:{resume.pk}:{item['source_reference']}",
                page=item["page"] or None,
                profile_version=proposed_version,
                confirmed=False,
            )
            draft_facts.append(fact)
        for question in questions:
            if question.strip():
                ProfileFact.objects.create(
                    profile=profile,
                    text=question.strip(),
                    kind="question",
                    source=f"draft:{draft_token}|resume:{resume.pk}",
                    profile_version=proposed_version,
                    confirmed=False,
                )
    return ProfileDraft(
        profile_id=profile.pk,
        proposed_version=proposed_version,
        token=draft_token,
        about=result["about"].strip(),
        facts=tuple(draft_facts),
        questions=tuple(questions),
    )


@transaction.atomic
def confirm_profile(draft, *, expected_version, about=None, fact_edits=None):
    profile = Profile.objects.select_for_update().get(pk=draft.profile_id)
    if profile.version != expected_version or draft.proposed_version != expected_version + 1:
        raise ProfileVersionConflict("Профиль уже изменён. Обновите страницу.")
    draft_rows = list(ProfileFact.objects.filter(
        profile=profile,
        profile_version=draft.proposed_version,
        confirmed=False,
        source__startswith=f"draft:{draft.token}|",
    ).order_by("id"))
    if not draft_rows:
        raise ProfileVersionConflict("Черновик уже заменён или подтверждён. Обновите страницу.")
    draft_about = next((row for row in draft_rows if row.kind == "about"), None)
    draft_facts = [row for row in draft_rows if row.kind not in {"question", "about"}]
    about = draft.about if about is None else about.strip()
    fact_edits = fact_edits or {}
    for fact in draft_facts:
        if fact.pk in fact_edits:
            fact.text = fact_edits[fact.pk].strip()
            fact.save(update_fields=["text", "updated_at"])
    accepted_facts = [fact for fact in draft_facts if fact.text.strip()]
    cleared_facts = [fact for fact in draft_facts if not fact.text.strip()]
    if not accepted_facts and not about:
        raise ValueError("Черновик профиля пуст.")
    if cleared_facts:
        ProfileFact.objects.filter(pk__in=[fact.pk for fact in cleared_facts]).delete()
    for fact in accepted_facts:
        suffix = fact.source.split("|", 1)[1] if "|" in fact.source else ""
        fact.source = f"confirmed:{draft.token}|{suffix}"
        fact.confirmed = True
        fact.save(update_fields=["source", "confirmed", "updated_at"])
    if draft_about:
        original_about = draft_about.text.strip()
        if about and about == original_about:
            suffix = draft_about.source.split("|", 1)[1] if "|" in draft_about.source else ""
            draft_about.source = f"confirmed:{draft.token}|{suffix}"
            draft_about.confirmed = True
            draft_about.save(update_fields=["source", "confirmed", "updated_at"])
        else:
            if original_about:
                suffix = draft_about.source.split("|", 1)[1] if "|" in draft_about.source else ""
                draft_about.source = f"review:{draft.token}|{suffix}"
                draft_about.save(update_fields=["source", "updated_at"])
            else:
                draft_about.delete()
            if about:
                ProfileFact.objects.create(
                    profile=profile,
                    text=about,
                    kind="about",
                    source=f"user-edit:draft:{draft.token}",
                    profile_version=draft.proposed_version,
                    confirmed=True,
                )
    for question in (row for row in draft_rows if row.kind == "question"):
        suffix = question.source.split("|", 1)[1] if "|" in question.source else ""
        question.source = f"review:{draft.token}|{suffix}"
        question.save(update_fields=["source", "updated_at"])
    profile.about = about
    profile.version = draft.proposed_version
    profile.confirmed_version = draft.proposed_version
    profile.save(update_fields=["about", "version", "confirmed_version", "updated_at"])
    return profile


def has_confirmed_profile(profile):
    return profile.confirmed_version > 0


def load_profile_draft(profile, proposed_version, token):
    rows = list(ProfileFact.objects.filter(
        profile=profile,
        profile_version=proposed_version,
        confirmed=False,
        source__startswith=f"draft:{token}|",
    ).order_by("id"))
    about = next((row.text for row in rows if row.kind == "about"), "")
    facts = tuple(row for row in rows if row.kind not in {"about", "question"})
    questions = tuple(row.text for row in rows if row.kind == "question")
    if not rows:
        raise ValueError("Черновик не найден.")
    return ProfileDraft(profile.pk, proposed_version, token, about, facts, questions)


@transaction.atomic
def update_profile_settings(profile, *, criteria, preferences, expected_version):
    current = Profile.objects.select_for_update().get(pk=profile.pk)
    if current.version != expected_version:
        raise ProfileVersionConflict("Профиль уже изменён. Обновите страницу.")
    current.criteria = criteria
    current.preferences = preferences
    current.version += 1
    current.save(update_fields=["criteria", "preferences", "version", "updated_at"])
    return current


@transaction.atomic
def confirm_manual_profile(profile, *, about, facts, expected_version):
    current = Profile.objects.select_for_update().get(pk=profile.pk)
    if current.version != expected_version:
        raise ProfileVersionConflict("Профиль уже изменён. Обновите страницу.")

    next_version = current.version + 1
    current.about = about.strip()
    current.version = next_version
    current.confirmed_version = next_version
    current.save(update_fields=["about", "version", "confirmed_version", "updated_at"])
    ProfileFact.objects.filter(profile=current, confirmed=False).delete()
    ProfileFact.objects.bulk_create([
        ProfileFact(
            profile=current,
            text=item["text"].strip(),
            kind=item["kind"],
            source=item.get("source", "manual"),
            page=item.get("page"),
            profile_version=next_version,
            confirmed=True,
        )
        for item in facts
        if item.get("text", "").strip()
    ])
    return current
