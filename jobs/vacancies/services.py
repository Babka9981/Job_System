"""Public vacancy service boundaries."""

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from jobs.models.models import Source, SourceRecord, Vacancy

UNKNOWN_TITLE = "Название не указано"
UNKNOWN_COMPANY = "Компания не указана"
NO_CONTENT_HASH = "no-content"


@dataclass(frozen=True)
class UpsertResult:
    vacancy: Vacancy
    source_record: SourceRecord
    created: bool
    linked: bool = False


class VersionConflict(Exception):
    def __init__(self, vacancy):
        self.vacancy = vacancy
        super().__init__("vacancy version conflict")


class _TextExtractor(HTMLParser):
    block_tags = {"br", "div", "li", "p", "tr"}
    ignored_tags = {"script", "style", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.ignored_tags:
            self.ignored_depth += 1
        elif not self.ignored_depth and tag in self.block_tags and self.parts:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.ignored_tags and self.ignored_depth:
            self.ignored_depth -= 1
        elif not self.ignored_depth and tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored_depth:
            self.parts.append(data)


def sanitize_source_text(value):
    parser = _TextExtractor()
    parser.feed(value or "")
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _owner(owner):
    if owner is not None:
        return owner
    return get_user_model().objects.get(username=settings.OWNER_USERNAME)


def _priority(record):
    explicit = record.get("priority")
    if explicit in Vacancy.Priority.values:
        return explicit
    arrangement = record.get("work_arrangement", "").lower()
    if "remote" in arrangement or "удален" in arrangement:
        return Vacancy.Priority.REMOTE
    if "relocat" in arrangement or "релока" in arrangement:
        return Vacancy.Priority.RELOCATION
    return Vacancy.Priority.OTHER


def _vacancy_values(record):
    salary = record.get("salary") or {}
    return {
        "title": (record.get("title") or "").strip() or UNKNOWN_TITLE,
        "company": (record.get("company") or "").strip() or UNKNOWN_COMPANY,
        "company_domain": record.get("company_domain") or "",
        "description": sanitize_source_text(record.get("description") or ""),
        "role": record.get("role") or "",
        "industry": record.get("industry") or "",
        "work_arrangement": record.get("work_arrangement") or "",
        "country_restrictions": record.get("country_restrictions") or [],
        "timezone_restrictions": record.get("timezone_restrictions") or [],
        "salary_min": salary.get("min"),
        "salary_max": salary.get("max"),
        "salary_currency": salary.get("currency") or "",
        "salary_period": salary.get("period") or "",
        "salary_basis": salary.get("basis") or "",
        "salary_component": salary.get("component") or "",
        "salary_fixed": salary.get("fixed") or {},
        "salary_bonus": salary.get("bonus") or {},
        "salary_equity_tokens": salary.get("equity_tokens") or {},
        "salary_original": salary.get("original") or "",
        "language": record.get("language") or "",
        "contact": record.get("contact") or "",
        "published_at": record.get("published_at"),
        "last_checked_at": timezone.now(),
        "priority": _priority(record),
    }


def _validate_public_url(value, field):
    parsed = urlsplit(value or "")
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValidationError({field: "Допустима только абсолютная HTTP(S)-ссылка."})


def _stored_raw_hash(value):
    return value.strip() if value and value.strip() else NO_CONTENT_HASH


def _is_content_hash(value):
    return bool(value and value.strip() and value.strip() != NO_CONTENT_HASH)


def _normalized_identity(value):
    return " ".join((value or "").casefold().split())


def _compatible_optional(left, right):
    if not left or not right:
        return True
    return _normalized_identity(left) == _normalized_identity(right)


def _compatible_sequence(left, right):
    if not left or not right:
        return True
    normalize = lambda values: sorted(_normalized_identity(value) for value in values)
    return normalize(left) == normalize(right)


def _known_identity(value, placeholder):
    normalized = _normalized_identity(value)
    return normalized not in {"", _normalized_identity(placeholder)}


def _identity_compatible(vacancy, values, *, allow_unknown=False):
    pairs = (
        (vacancy.title, values["title"], UNKNOWN_TITLE),
        (vacancy.company, values["company"], UNKNOWN_COMPANY),
    )
    for existing, incoming, placeholder in pairs:
        existing_known = _known_identity(existing, placeholder)
        incoming_known = _known_identity(incoming, placeholder)
        if not allow_unknown and not (existing_known and incoming_known):
            return False
        if existing_known and incoming_known and _normalized_identity(existing) != _normalized_identity(incoming):
            return False
    if not allow_unknown and not all(_known_identity(incoming, placeholder) for _, incoming, placeholder in pairs):
        return False
    return (
        _compatible_optional(vacancy.company_domain, values["company_domain"])
        and _compatible_optional(vacancy.role, values["role"])
        and _compatible_sequence(vacancy.country_restrictions, values["country_restrictions"])
        and _compatible_sequence(vacancy.timezone_restrictions, values["timezone_restrictions"])
    )


def _record_updates(vacancy, values, record):
    updates = {}
    for field, value in values.items():
        if field == "last_checked_at":
            updates[field] = value
            continue
        if field == "title" and value == UNKNOWN_TITLE and vacancy.title != UNKNOWN_TITLE:
            continue
        if field == "company" and value == UNKNOWN_COMPANY and vacancy.company != UNKNOWN_COMPANY:
            continue
        if field == "priority" and value == Vacancy.Priority.OTHER and not record.get("priority") and not record.get("work_arrangement"):
            continue
        if value in (None, "", [], {}):
            continue
        updates[field] = value
    return updates


def _update_source_record(source_record, record, *, content_changed=False):
    canonical_url_changed = source_record.canonical_url != record["canonical_url"]
    fields = ["canonical_url"]
    source_record.canonical_url = record["canonical_url"]
    if record.get("apply_url"):
        source_record.apply_url = record["apply_url"]
        fields.append("apply_url")
    if _is_content_hash(record.get("raw_hash")):
        source_record.raw_hash = _stored_raw_hash(record["raw_hash"])
        fields.append("raw_hash")
    if canonical_url_changed or "adapter_confirmed_permalink" in record:
        source_record.adapter_confirmed_permalink = record.get("adapter_confirmed_permalink") is True
        fields.append("adapter_confirmed_permalink")
    if content_changed or "description_permission" in record:
        source_record.description_permission = record.get("description_permission") is True
        fields.append("description_permission")
    if record.get("attribution"):
        source_record.attribution = record["attribution"]
        fields.append("attribution")
    if record.get("expires_at") is not None:
        source_record.expires_at = record["expires_at"]
        fields.append("expires_at")
    source_record.full_clean()
    source_record.save(update_fields=[*fields, "updated_at"])


def _cross_source_match(owner, source, record, values):
    candidates = SourceRecord.objects.select_related("vacancy").filter(source__owner=owner).exclude(source=source)
    evidence_ids = set()
    raw_hash = (record.get("raw_hash") or "").strip()
    if _is_content_hash(raw_hash):
        evidence_ids.update(candidates.filter(raw_hash=raw_hash).values_list("id", flat=True))
    if record.get("adapter_confirmed_permalink") is True:
        evidence_ids.update(
            candidates.filter(
                canonical_url=record["canonical_url"],
                adapter_confirmed_permalink=True,
            ).values_list("id", flat=True)
        )
    compatible = [
        candidate
        for candidate in candidates.filter(id__in=evidence_ids).order_by("id")
        if _identity_compatible(candidate.vacancy, values)
    ]
    vacancy_ids = {candidate.vacancy_id for candidate in compatible}
    if len(vacancy_ids) != 1:
        return None
    return compatible[0]


@transaction.atomic
def upsert_record(record, *, owner=None):
    owner = _owner(owner)
    _validate_public_url(record.get("canonical_url"), "canonical_url")
    if record.get("apply_url"):
        _validate_public_url(record["apply_url"], "apply_url")
    source = Source.objects.get(owner=owner, slug=record["source_slug"])
    external_id = str(record.get("external_id") or "").strip()
    identity = {"source": source, "external_id": external_id}
    if not external_id:
        identity["canonical_url"] = record["canonical_url"]
    existing = SourceRecord.objects.select_related("vacancy").filter(**identity).first()
    values = _vacancy_values(record)
    if existing:
        vacancy = existing.vacancy
        content_changed = (
            _is_content_hash(record.get("raw_hash"))
            and existing.raw_hash != record["raw_hash"].strip()
        ) or (
            "description" in record
            and bool(values["description"])
            and values["description"] != vacancy.description
        )
        updates = _record_updates(vacancy, values, record)
        for field, value in updates.items():
            setattr(vacancy, field, value)
        vacancy.version += 1
        vacancy.save(update_fields=[*updates, "version", "updated_at"])
        _update_source_record(existing, record, content_changed=content_changed)
        return UpsertResult(vacancy, existing, False)

    apply_url = record.get("apply_url") or ""
    matched = _cross_source_match(owner, source, record, values)
    if matched:
        vacancy = matched.vacancy
        updates = _record_updates(vacancy, values, record)
        for field, value in updates.items():
            setattr(vacancy, field, value)
        vacancy.version += 1
        vacancy.save(update_fields=[*updates, "version", "updated_at"])
    else:
        vacancy = Vacancy(owner=owner, **values)
        vacancy.full_clean()
        vacancy.save()
    source_record = SourceRecord(
        source=source,
        vacancy=vacancy,
        external_id=external_id,
        canonical_url=record["canonical_url"],
        apply_url=apply_url,
        raw_hash=_stored_raw_hash(record.get("raw_hash")),
        adapter_confirmed_permalink=record.get("adapter_confirmed_permalink") is True,
        description_permission=record.get("description_permission") is True,
        expires_at=record.get("expires_at"),
        attribution=record.get("attribution") or {},
    )
    source_record.full_clean()
    source_record.save()
    return UpsertResult(vacancy, source_record, not bool(matched), linked=bool(matched))


def change_status(vacancy_id, version, status, note="", *, owner=None):
    owner = _owner(owner)
    if status not in Vacancy.UserStatus.values:
        raise ValidationError({"status": "Неизвестный статус вакансии."})
    note = note.strip()
    if status == Vacancy.UserStatus.CLOSED and not note:
        raise ValidationError({"note": "Укажите причину закрытия вакансии."})
    updated = Vacancy.objects.filter(pk=vacancy_id, owner=owner, version=version).update(
        user_status=status,
        closing_note=note if status == Vacancy.UserStatus.CLOSED else "",
        version=version + 1,
        updated_at=timezone.now(),
    )
    if not updated:
        current = Vacancy.objects.get(pk=vacancy_id, owner=owner)
        raise VersionConflict(current)
    return Vacancy.objects.get(pk=vacancy_id, owner=owner)


def _versioned_update(vacancy_id, version, owner, **changes):
    updated = Vacancy.objects.filter(pk=vacancy_id, owner=owner, version=version).update(
        **changes,
        version=F("version") + 1,
        updated_at=timezone.now(),
    )
    if not updated:
        current = Vacancy.objects.get(pk=vacancy_id, owner=owner)
        raise VersionConflict(current)
    return Vacancy.objects.get(pk=vacancy_id, owner=owner)


def change_visibility(vacancy_id, version, hidden, *, owner=None):
    return _versioned_update(vacancy_id, version, _owner(owner), hidden=bool(hidden))


def change_availability(vacancy_id, version, availability, *, owner=None):
    if availability not in Vacancy.Availability.values:
        raise ValidationError({"availability": "Неизвестная актуальность вакансии."})
    return _versioned_update(vacancy_id, version, _owner(owner), availability=availability)


def change_note(vacancy_id, version, note, *, owner=None):
    return _versioned_update(vacancy_id, version, _owner(owner), user_note=note.strip())
