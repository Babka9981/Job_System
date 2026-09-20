import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from django.db import transaction
from django.utils import timezone

from jobs.models.models import Source, SourceRecord, Vacancy
from jobs.sources.telegram.links import post_url
from jobs.sources.telegram.parser import parse_post
from jobs.sources.telegram.permissions import permission_for_message
from jobs.vacancies.services import sanitize_source_text, upsert_record


class HistoryAccessError(Exception):
    def __init__(self, code, safe_message, *, retryable=False, retry_after=None):
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.retry_after = retry_after
        super().__init__(safe_message)


@dataclass(frozen=True)
class HistoryMessage:
    message_id: int
    text: str
    published_at: datetime | None
    edited_at: datetime | None = None
    peer_id: int | None = None
    public_handle: str | None = None
    origin: str = "api"


@dataclass(frozen=True)
class HistoryPage:
    messages: tuple[HistoryMessage, ...]
    next_offset_id: int | None = None
    raw_count: int = 0
    raw_max_id: int = 0


@dataclass(frozen=True)
class TelegramUpdate:
    kind: str
    message_id: int
    message: HistoryMessage | None = None
    peer_id: int | None = None


@dataclass(frozen=True)
class SyncReport:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    review: int = 0
    skipped: int = 0
    error_code: str = ""


def _checkpoint(source):
    config = source.config if isinstance(source.config, dict) else {}
    return config.get("telegram_checkpoint", {})


def _validated_checkpoint(source):
    checkpoint = _checkpoint(source)
    if not isinstance(checkpoint, dict):
        raise HistoryAccessError("invalid_checkpoint", "Checkpoint Telegram повреждён; источник остановлен безопасно.")
    if set(checkpoint) - {"message_id", "offset_id", "window_max_id"}:
        raise HistoryAccessError("invalid_checkpoint", "Checkpoint Telegram повреждён; источник остановлен безопасно.")
    values = {}
    for field in ("message_id", "offset_id", "window_max_id"):
        value = checkpoint.get(field, 0)
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            raise HistoryAccessError("invalid_checkpoint", "Checkpoint Telegram повреждён; источник остановлен безопасно.")
        values[field] = value
    if values["window_max_id"] == 0:
        values["window_max_id"] = values["message_id"]
    if values["window_max_id"] < values["message_id"]:
        raise HistoryAccessError("invalid_checkpoint", "Checkpoint Telegram повреждён; источник остановлен безопасно.")
    return values


def _safe_config(source):
    return dict(source.config) if isinstance(source.config, dict) else {}


def _is_active_evidence(record):
    attribution = record.attribution if isinstance(record.attribution, dict) else {}
    return attribution.get("deleted") is not True


def _recompute_availability(vacancy_ids):
    changed = 0
    for vacancy in Vacancy.objects.filter(pk__in=set(vacancy_ids)):
        active = any(_is_active_evidence(record) for record in vacancy.source_records.all())
        availability = Vacancy.Availability.ACTIVE if active else Vacancy.Availability.REMOVED
        if vacancy.availability != availability:
            vacancy.availability = availability
            vacancy.version += 1
            vacancy.save(update_fields=["availability", "version", "updated_at"])
            changed += 1
    return changed


def _mark_records_deleted(records):
    vacancy_ids = []
    for record in records:
        attribution = dict(record.attribution) if isinstance(record.attribution, dict) else {}
        attribution["deleted"] = True
        record.attribution = attribution
        record.save(update_fields=["attribution", "updated_at"])
        vacancy_ids.append(record.vacancy_id)
    return _recompute_availability(vacancy_ids)


def _mark_post_removed(source, message_id):
    records = list(
        SourceRecord.objects.select_related("vacancy")
        .filter(source=source, external_id__startswith=f"{message_id}:")
    )
    return _mark_records_deleted(records)


def _normal(value):
    return " ".join((value or "").casefold().split())


def _part_key(vacancy):
    material = "\n".join((_normal(vacancy.title), _normal(vacancy.company), _normal(vacancy.description)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _title_similarity(left, right):
    left_tokens = set(_normal(left).split())
    right_tokens = set(_normal(right).split())
    if not left_tokens or not right_tokens:
        return 0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


_COMPANY_SUFFIXES = {
    "co", "company", "corp", "corporation", "gmbh", "group", "holding", "holdings",
    "inc", "incorporated", "llc", "ltd", "limited", "ооо", "ао", "пао",
}


def _company_identity_tokens(value):
    return {
        cleaned
        for token in _normal(value).split()
        if (cleaned := token.strip(".,()[]{}\"'")) and cleaned not in _COMPANY_SUFFIXES
    }


def _conservative_continuity(record, vacancy):
    old_company = _normal(record.vacancy.company)
    new_company = _normal(vacancy.company)
    if old_company and new_company:
        if old_company == new_company:
            return True
        old_tokens = _company_identity_tokens(old_company)
        new_tokens = _company_identity_tokens(new_company)
        if old_tokens and new_tokens:
            overlap = len(old_tokens & new_tokens) / min(len(old_tokens), len(new_tokens))
            if overlap >= 0.75:
                return True
        if SequenceMatcher(None, old_company, new_company).ratio() >= 0.8:
            return True
        # Two explicit, unrelated company identities outweigh shared vacancy
        # boilerplate. Description similarity is evidence only when company
        # identity is absent, never a way to cross this contradiction.
        return False
    old_description = _normal(record.vacancy.description)
    new_description = _normal(vacancy.description)
    return bool(
        old_description
        and new_description
        and SequenceMatcher(None, old_description, new_description).ratio() >= 0.82
    )


def _existing_part_record(existing, vacancy, key, used_ids):
    exact = [
        record for record in existing
        if record.pk not in used_ids and (record.attribution or {}).get("part_key") == key
    ]
    if len(exact) == 1:
        return exact[0]
    available = [record for record in existing if record.pk not in used_ids]
    same_title = [record for record in available if _normal(record.vacancy.title) == _normal(vacancy.title)]
    if len(same_title) == 1:
        return same_title[0] if _conservative_continuity(same_title[0], vacancy) else None
    if len(same_title) > 1:
        scored = sorted((
            (
                SequenceMatcher(None, _normal(record.vacancy.company), _normal(vacancy.company)).ratio(),
                record,
            )
            for record in same_title
        ), key=lambda item: item[0])
        best_score, best = scored[-1]
        runner_up = scored[-2][0]
        if best_score >= 0.6 and best_score - runner_up >= 0.15:
            return best
    similar = [
        record for record in available
        if _normal(record.vacancy.company) == _normal(vacancy.company)
        and _title_similarity(record.vacancy.title, vacancy.title) >= 0.5
    ]
    return similar[0] if len(similar) == 1 else None


def _protected_manual_description(vacancy):
    description = vacancy.description
    content_hash = hashlib.sha256(description.encode("utf-8")).hexdigest()
    permitted = vacancy.source_records.filter(
        source__kind="manual",
        description_permission=True,
        raw_hash=content_hash,
    ).exists()
    return description if permitted else None


class TelegramReader:
    """Deterministic Telegram history reader; no bot token and no LLM calls."""

    def __init__(self, client, *, page_size=100, max_pages=20, now=None):
        self.client = client
        self.page_size = page_size
        self.max_pages = max_pages
        self.now = now or timezone.now

    def _failure(self, source, error):
        config = _safe_config(source)
        if error.code == "invalid_checkpoint":
            config["telegram_checkpoint"] = {"message_id": 0, "offset_id": 0, "window_max_id": 0}
        detail = {"code": error.code, "message": error.safe_message, "retryable": error.retryable is True}
        if isinstance(error.retry_after, int) and error.retry_after >= 0:
            detail["retry_after"] = error.retry_after
        config["last_error"] = detail
        config["last_check"] = self.now().isoformat()
        source.config = config
        source.status = Source.Status.NEEDS_ACCESS if error.code in {"session_unauthorized", "access_denied"} else Source.Status.ERROR
        source.save(update_fields=["config", "status", "updated_at"])
        return SyncReport(error_code=error.code)

    def _record_review(self, source, message, reason):
        config = _safe_config(source)
        reviews = [item for item in config.get("telegram_review", []) if item.get("message_id") != message.message_id]
        reviews.append({"message_id": message.message_id, "post_url": post_url(message), "reason": reason})
        config["telegram_review"] = reviews[-100:]
        source.config = config

    def _save_checkpoint(self, source, message_id, *, offset_id=0, window_max_id=0):
        config = _safe_config(source)
        config["telegram_checkpoint"] = {
            "message_id": int(message_id or 0),
            "offset_id": int(offset_id or 0),
            "window_max_id": int(window_max_id or message_id or 0),
        }
        config["last_check"] = self.now().isoformat()
        config["last_error"] = None
        source.config = config
        source.save(update_fields=["config", "updated_at"])

    def _process_message(self, source, message):
        parsed = parse_post(message.text)
        if parsed.needs_review:
            with transaction.atomic():
                self._record_review(source, message, parsed.reason)
                source.save(update_fields=["config", "updated_at"])
            return 0, 0, 1

        created = updated = 0
        exact_url = post_url(message)
        private = not bool(message.public_handle)
        existing = list(
            SourceRecord.objects.select_related("vacancy")
            .filter(source=source, external_id__startswith=f"{message.message_id}:")
        )
        current_ids = []
        used_ids = set()
        with transaction.atomic():
            for vacancy in parsed.vacancies:
                description = sanitize_source_text(vacancy.description)
                part_key = _part_key(vacancy)
                prior = _existing_part_record(existing, vacancy, part_key, used_ids)
                external_id = prior.external_id if prior else f"{message.message_id}:{part_key[:24]}"
                if prior:
                    used_ids.add(prior.pk)
                protected_description = _protected_manual_description(prior.vacancy) if prior else None
                current_ids.append(external_id)
                record = {
                    "source_slug": source.slug,
                    "external_id": external_id,
                    "canonical_url": exact_url,
                    "title": vacancy.title,
                    "company": vacancy.company,
                    "description": description,
                    "description_permission": permission_for_message(source, origin=message.origin),
                    "published_at": message.published_at,
                    "salary": {},
                    "raw_hash": hashlib.sha256(description.encode("utf-8")).hexdigest(),
                    "adapter_confirmed_permalink": not private,
                    "attribution": {
                        "label": source.name,
                        "url": source.config.get("url", "") if isinstance(source.config, dict) else "",
                        "post_url": exact_url,
                        "message_id": message.message_id,
                        "part_key": part_key,
                        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
                        "access_hint": "Требуется доступ аккаунта к приватному каналу" if private else "Публичная ссылка Telegram",
                        "parser": "deterministic-v1",
                        "deleted": False,
                    },
                }
                result = upsert_record(record, owner=source.owner)
                if protected_description is not None and result.vacancy.description != protected_description:
                    result.vacancy.description = protected_description
                    result.vacancy.save(update_fields=["description", "updated_at"])
                if result.created:
                    created += 1
                else:
                    updated += 1
                _recompute_availability([result.vacancy.pk])

            stale = SourceRecord.objects.filter(
                source=source, external_id__startswith=f"{message.message_id}:"
            ).exclude(external_id__in=current_ids)
            _mark_records_deleted(list(stale.select_related("vacancy")))
            config = _safe_config(source)
            config["telegram_review"] = [
                item for item in config.get("telegram_review", []) if item.get("message_id") != message.message_id
            ]
            source.config = config
            source.save(update_fields=["config", "updated_at"])
        return created, updated, 0

    def sync_source(self, source):
        if source.kind != "telegram" or source.adapter != "telegram":
            return self._failure(source, HistoryAccessError("invalid_source", "Источник не является Telegram-каналом."))
        try:
            checkpoint = _validated_checkpoint(source)
        except HistoryAccessError as error:
            return self._failure(source, error)
        try:
            authorized = self.client.is_authorized()
        except Exception:
            return self._failure(
                source,
                HistoryAccessError("session_check_failed", "Не удалось проверить пользовательскую Telegram-сессию.", retryable=True),
            )
        if not authorized:
            return self._failure(
                source,
                HistoryAccessError("session_unauthorized", "Пользовательская Telegram-сессия требует повторного подключения."),
            )

        after_id = int(checkpoint.get("message_id") or 0)
        initial = after_id == 0
        offset_id = int(checkpoint.get("offset_id") or 0)
        window_max_id = max(int(checkpoint.get("window_max_id") or 0), after_id)
        created = updated = deleted = review = skipped = 0
        try:
            for event in self.client.fetch_updates(source.config.get("url", ""), checkpoint=checkpoint):
                if event.kind == "deleted":
                    with transaction.atomic():
                        deleted += _mark_post_removed(source, event.message_id)
                elif event.kind == "edited" and event.message is not None:
                    c, u, r = self._process_message(source, event.message)
                    created += c
                    updated += u
                    review += r

            pages = 0
            while pages < self.max_pages:
                page = self.client.fetch_history(
                    source.config.get("url", ""), after_id=after_id, offset_id=offset_id, limit=self.page_size
                )
                pages += 1
                page_max_id = max(
                    [int(page.raw_max_id or 0), *(message.message_id for message in page.messages)],
                    default=0,
                )
                window_max_id = max(window_max_id, page_max_id)
                for message in sorted(page.messages, key=lambda item: item.message_id):
                    if initial and message.published_at and message.published_at < self.now() - timedelta(days=7):
                        skipped += 1
                        continue
                    if message.message_id <= after_id and message.edited_at is None:
                        skipped += 1
                        continue
                    c, u, r = self._process_message(source, message)
                    created += c
                    updated += u
                    review += r
                if not page.next_offset_id:
                    self._save_checkpoint(
                        source, max(after_id, window_max_id), offset_id=0, window_max_id=max(after_id, window_max_id)
                    )
                    break
                if int(page.next_offset_id) == offset_id:
                    raise HistoryAccessError("cursor_stalled", "Telegram вернул повторяющуюся позицию истории.", retryable=True)
                offset_id = page.next_offset_id
                self._save_checkpoint(source, after_id, offset_id=offset_id, window_max_id=window_max_id)
        except HistoryAccessError as error:
            return self._failure(source, error)
        except Exception:
            return self._failure(source, HistoryAccessError("history_failure", "Не удалось безопасно прочитать историю Telegram.", retryable=True))

        config = _safe_config(source)
        config["last_error"] = None
        config["last_success"] = self.now().isoformat()
        if pages >= self.max_pages and page.next_offset_id:
            config["coverage_reason"] = "Достигнут безопасный предел страниц; чтение продолжится с checkpoint."
            source.status = Source.Status.LIMITED
        else:
            config.pop("coverage_reason", None)
            source.status = Source.Status.LIMITED if config.get("telegram_review") else Source.Status.READY
        source.config = config
        source.last_success = self.now()
        source.save(update_fields=["config", "status", "last_success", "updated_at"])
        return SyncReport(created, updated, deleted, review, skipped)


def sync_sources(sources, client_factory):
    """Run channels independently so access/flood/history failures do not stop peers."""
    reports = {}
    for source in sources:
        clients = []
        try:
            client = client_factory(source)
            clients.append(client)
            connect = getattr(client, "connect", None)
            if callable(connect):
                connected = connect()
                if connected is not None and hasattr(connected, "is_authorized"):
                    client = connected
                    if all(client is not item for item in clients):
                        clients.append(client)
        except Exception:
            reports[source.slug] = TelegramReader(None)._failure(
                source,
                HistoryAccessError("client_unavailable", "Не удалось подготовить Telegram-клиент.", retryable=True),
            )
        else:
            try:
                reports[source.slug] = TelegramReader(client).sync_source(source)
            except Exception:
                reports[source.slug] = TelegramReader(None)._failure(
                    source,
                    HistoryAccessError("source_sync_failed", "Не удалось безопасно обработать Telegram-источник.", retryable=True),
                )
        finally:
            for owned_client in reversed(clients):
                cleanup = getattr(owned_client, "disconnect", None) or getattr(owned_client, "close", None)
                if callable(cleanup):
                    try:
                        cleanup()
                    except Exception:
                        pass
    return reports
