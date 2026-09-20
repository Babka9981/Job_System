from dataclasses import asdict, dataclass
import hashlib
from urllib.parse import urlsplit

from django.db import transaction

from jobs.models.models import Source, SourceRecord, Vacancy
from jobs.sources.telegram.links import normalize_source_link
from jobs.vacancies.services import sanitize_source_text


SOURCE_PERMISSION_KINDS = {"channel_owner_consent", "author_permission"}
INDEPENDENT_PERMISSION_KINDS = {"direct_employer_jd", "licensed_jd"}


@dataclass(frozen=True)
class PermissionBasis:
    confirmed: object
    kind: str
    reference: str
    scope: str


def _valid_basis(basis, *, kinds, expected_scope=None):
    if not isinstance(basis, PermissionBasis) or basis.confirmed is not True:
        return False
    if basis.kind not in kinds or not basis.reference.strip() or not basis.scope.strip():
        return False
    return expected_scope is None or basis.scope == expected_scope


def apply_source_permission(source, basis):
    config = dict(source.config) if isinstance(source.config, dict) else {}
    try:
        expected_scope = normalize_source_link(config.get("url", ""))
    except ValueError:
        expected_scope = ""
    valid = _valid_basis(basis, kinds=SOURCE_PERMISSION_KINDS, expected_scope=expected_scope)
    source.llm_permission = valid
    if valid:
        config["llm_permission_basis"] = asdict(basis)
    else:
        config.pop("llm_permission_basis", None)
    source.config = config
    source.save(update_fields=["llm_permission", "config", "updated_at"])
    return valid


def permission_for_message(source, *, origin):
    if origin != "api" or source.llm_permission is not True or not isinstance(source.config, dict):
        return False
    stored = source.config.get("llm_permission_basis")
    if not isinstance(stored, dict):
        return False
    try:
        basis = PermissionBasis(**stored)
        expected = normalize_source_link(source.config.get("url", ""))
    except (TypeError, ValueError):
        return False
    return _valid_basis(basis, kinds=SOURCE_PERMISSION_KINDS, expected_scope=expected)


def with_independent_jd(record, text, basis):
    if not _valid_basis(basis, kinds=INDEPENDENT_PERMISSION_KINDS):
        raise ValueError("Для независимого JD требуется явное документированное разрешение.")
    description = sanitize_source_text(text)
    if not description:
        raise ValueError("Разрешённый JD не должен быть пустым.")
    parsed_scope = urlsplit(basis.scope)
    if parsed_scope.scheme.lower() not in {"http", "https"} or not parsed_scope.netloc:
        raise ValueError("Scope независимого JD должен быть абсолютной HTTP(S)-ссылкой.")

    supplemented = dict(record)
    supplemented["source_slug"] = "independent-permitted-jd"
    supplemented["external_id"] = hashlib.sha256(
        f"{basis.reference}\n{basis.scope}".encode("utf-8")
    ).hexdigest()
    supplemented["canonical_url"] = basis.scope
    supplemented["description"] = description
    supplemented["raw_hash"] = hashlib.sha256(description.encode("utf-8")).hexdigest()
    supplemented["description_permission"] = True
    attribution = dict(record.get("attribution") or {})
    attribution.update({
        "description_origin": "independent_jd",
        "permission_kind": basis.kind,
        "permission_reference": basis.reference,
        "permission_scope": basis.scope,
    })
    supplemented["attribution"] = attribution
    return supplemented


@transaction.atomic
def store_independent_jd(telegram_record, text, basis):
    """Attach permitted evidence without mutating permission on the Telegram record."""
    payload = with_independent_jd({}, text, basis)
    owner = telegram_record.source.owner
    source, _ = Source.objects.get_or_create(
        owner=owner,
        slug="independent-permitted-jd",
        defaults={
            "name": "Independent permitted JD",
            "kind": "manual",
            "adapter": "independent_jd",
            "status": Source.Status.READY,
            "enabled": True,
            "config": {
                "access_mode": "manual_permitted_text",
                "reason": "Независимый JD с отдельным документированным разрешением.",
            },
        },
    )
    source.name = "Independent permitted JD"
    source.kind = "manual"
    source.adapter = "independent_jd"
    source.status = Source.Status.READY
    source.enabled = True
    source.llm_permission = False
    source.config = {
        "access_mode": "manual_permitted_text",
        "reason": "Независимый JD с отдельным документированным разрешением.",
    }
    source.save(update_fields=[
        "name", "kind", "adapter", "status", "enabled", "llm_permission", "config", "updated_at"
    ])
    external_id = hashlib.sha256(
        f"{telegram_record.vacancy_id}\n{payload['external_id']}".encode("utf-8")
    ).hexdigest()
    vacancy = Vacancy.objects.select_for_update().get(pk=telegram_record.vacancy_id)
    vacancy.description = payload["description"]
    vacancy.availability = Vacancy.Availability.ACTIVE
    vacancy.version += 1
    vacancy.save(update_fields=["description", "availability", "version", "updated_at"])
    attribution = {
        **payload["attribution"],
        "telegram_source_record_id": telegram_record.pk,
        "deleted": False,
    }
    record = SourceRecord.objects.filter(source=source, external_id=external_id).first()
    if record is None:
        record = SourceRecord(source=source, external_id=external_id)
    record.vacancy = vacancy
    record.canonical_url = payload["canonical_url"]
    record.apply_url = ""
    record.raw_hash = payload["raw_hash"]
    record.adapter_confirmed_permalink = False
    record.description_permission = True
    record.attribution = attribution
    record.full_clean()
    record.save()
    return record
