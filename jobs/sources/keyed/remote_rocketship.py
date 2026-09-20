import hashlib
import json
import os
import secrets
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from jobs.sources.core.contracts import Batch, Coverage, SourceCollectionError
from jobs.sources.keyed.common import bounded_int, required_secret
from jobs.sources.keyed.durable import DurableUtcCounter, atomic_write_json, literal_true
from jobs.sources.keyed.http import KeyedJsonHttpClient
from jobs.sources.public.common import parse_datetime


class RocketshipDailyQuota:
    request_limit = 500
    job_limit = 3000

    def __init__(self, *, clock=timezone.now, namespace="remote-rocketship"):
        self.counter = DurableUtcCounter(namespace, clock=clock)

    def reserve_request(self):
        return self.counter.increment(
            "requests", 1, self.request_limit, "daily_request_quota",
            "Дневной лимит Remote Rocketship в 500 запросов исчерпан.",
        )

    def record_jobs(self, count):
        return self.counter.increment(
            "jobs", count, self.job_limit, "daily_job_quota",
            "Дневной лимит Remote Rocketship в 3000 вакансий исчерпан.",
        )

    def reserve_job_capacity(self, count):
        return self.record_jobs(count)


class RemoteRocketshipAdapter:
    endpoint = "https://www.remoterocketship.com/api/openclaw/jobs"
    timeout = 15

    def __init__(self, *, http=None, environ=None, clock=timezone.now, quota=None):
        self.http = http or KeyedJsonHttpClient()
        self.environ = environ
        self.clock = clock
        self.quota = quota or RocketshipDailyQuota(clock=clock)

    @staticmethod
    def live_check_status():
        return "credential-gated"

    def collect(self, source, cursor=None):
        environment = os.environ if self.environ is None else self.environ
        if not literal_true(environment.get("REMOTE_ROCKETSHIP_ENABLED")):
            raise SourceCollectionError("source_not_activated", "Remote Rocketship не включён локально.")
        if not literal_true(environment.get("REMOTE_ROCKETSHIP_ACTIVE_PLAN_CONFIRMED")):
            raise SourceCollectionError("active_plan_unconfirmed", "Активный план Remote Rocketship не подтверждён локально.")
        api_key = required_secret("REMOTE_ROCKETSHIP_API_KEY", environment)
        config = source.config if isinstance(source.config, dict) else {}
        page = bounded_int(cursor, default=1, minimum=1, maximum=10_000)
        filters = {
            "page": page,
            "itemsPerPage": bounded_int(config.get("items_per_page"), default=50, minimum=1, maximum=50),
            "jobTitleFilters": config.get("role_queries") or ["Product Manager"],
            "showRemoteJobs": True,
            "sortBy": "DateAdded",
        }
        reserved_job_capacity = filters["itemsPerPage"]
        self.quota.reserve_job_capacity(reserved_job_capacity)
        self.quota.reserve_request()
        initial_attempt_reserved = True

        def reserve_attempt():
            nonlocal initial_attempt_reserved
            if initial_attempt_reserved:
                initial_attempt_reserved = False
                return
            self.quota.reserve_job_capacity(reserved_job_capacity)
            self.quota.reserve_request()

        payload = self.http.post_json(
            self.endpoint,
            json_body={"filters": filters, "includeJobDescription": True},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=self.timeout,
            before_attempt=reserve_attempt,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("jobOpenings"), list):
            raise SourceCollectionError("invalid_payload", "Remote Rocketship вернул неожиданный формат.")
        if len(payload["jobOpenings"]) > reserved_job_capacity:
            raise SourceCollectionError("quota_payload_exceeded", "Remote Rocketship превысил запрошенный размер страницы.")
        return self._batch_from_payload(source, payload, self.clock(), page=page)

    @classmethod
    def _batch_from_payload(cls, source, payload, now, *, page):
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        total_pages = bounded_int(pagination.get("totalPages"), default=page, minimum=page, maximum=10_000)
        next_cursor = str(page + 1) if pagination.get("hasNextPage") is True and page < total_pages else None
        records = tuple(
            cls._normalize(source, item, now)
            for item in payload.get("jobOpenings", [])
            if isinstance(item, dict) and item.get("url")
        )
        return Batch(
            records=records,
            next_cursor=next_cursor,
            coverage=Coverage(truncated=bool(next_cursor), reason="Remote Rocketship: временная выдача, TTL не более 24 часов"),
        )

    @staticmethod
    def _normalize(source, item, now):
        company = item.get("company") if isinstance(item.get("company"), dict) else {}
        expires_at = now + timedelta(hours=24)
        normalized = {
            "title": item.get("roleTitle") or "",
            "company": company.get("name") or "",
            "company_domain": company.get("homePageURL") or "",
            "role": item.get("roleTitle") or "",
            "work_arrangement": "remote",
            "published_at": parse_datetime(item.get("created_at")),
        }
        return {
            "source_slug": source.slug,
            "external_id": str(item.get("id") or ""),
            "canonical_url": item["url"],
            "apply_url": "",
            **normalized,
            "description": "",
            "description_permission": False,
            "country_restrictions": [],
            "timezone_restrictions": [],
            "salary": {"original": item.get("salaryRange") or ""},
            "raw_hash": "",
            "adapter_confirmed_permalink": False,
            "expires_at": expires_at,
            "attribution": {"label": "Remote Rocketship (временно)", "temporary": True, "permanent_link_allowed": False},
            "_temporary_payload": {"raw": item, "normalized": normalized, "derived": {}, "cache": {}},
        }


@dataclass(frozen=True)
class PurgeReport:
    expired: int = 0
    orphans: int = 0


@dataclass(frozen=True)
class StoreReceipt:
    handles: tuple[str, ...]
    mutations: tuple["StoreMutation", ...] = ()

    @property
    def created(self):
        return tuple(
            (mutation.handle, mutation.filename, mutation.applied_item["expires_at"])
            for mutation in self.mutations
            if mutation.previous_item is None
        )


@dataclass(frozen=True)
class StoreMutation:
    handle: str
    filename: str
    applied_item: dict
    applied_digest: str
    previous_item: dict | None
    previous_bytes: bytes | None


class TemporaryRocketshipStore:
    """DB-less provider store physically segregated under TEMPORARY_ROOT."""

    def __init__(
        self,
        *,
        clock=timezone.now,
        replacer=os.replace,
        sleeper=time.sleep,
        monotonic=time.monotonic,
        lock_timeout=5,
    ):
        self.clock = clock
        self.replacer = replacer
        self.sleeper = sleeper
        self.monotonic = monotonic
        self.lock_timeout = lock_timeout

    def directory(self, owner, source):
        if not getattr(owner, "pk", None):
            raise SourceCollectionError("owner_required", "Для временной карточки нужен владелец.")
        slug = "".join(character for character in source.slug if character.isalnum() or character in "-_")
        return Path(settings.TEMPORARY_ROOT) / "rocketship" / str(owner.pk) / slug

    def store_batch(self, owner, source, batch):
        return self.store_page(owner, source, batch, page_key=None).handles

    def store_page(self, owner, source, batch, *, page_key):
        directory = self.directory(owner, source)
        self._prepare_directory(directory)
        with self._directory_lock(directory):
            self._purge_directory_locked(directory)
            return self._store_batch_locked(
                directory,
                batch,
                owner=owner,
                source=source,
                page_key=page_key,
            )

    def _store_batch_locked(self, directory, batch, *, owner, source, page_key):
        index = self._load_index(directory)
        now = self._utc(self.clock())
        handles = []
        mutations = []
        updated = {"version": 1, "items": dict(index["items"])}
        prepared = []
        identities = set()
        source_error = None
        store_failed = False
        try:
            for position, record in enumerate(batch.records):
                expires_at = self._utc(record.get("expires_at"))
                if expires_at is None or expires_at <= now or expires_at > now + timedelta(hours=24):
                    raise SourceCollectionError("invalid_ttl", "Временная карточка имеет недопустимый срок хранения.")
                payload = record.get("_temporary_payload")
                if not isinstance(payload, dict) or set(payload) != {"raw", "normalized", "derived", "cache"}:
                    raise SourceCollectionError("invalid_temporary_payload", "Временная карточка неполна.")
                json_payload = self._json_value(payload)
                identity = self._stable_identity(record) if page_key is not None else None
                if identity is not None and identity in identities:
                    raise SourceCollectionError(
                        "temporary_identity_ambiguous",
                        "Временная страница содержит неоднозначную identity вакансии.",
                    )
                if identity is not None:
                    identities.add(identity)
                handle = (
                    secrets.token_urlsafe(24)
                    if page_key is None else
                    self._stable_handle(owner, source, page_key, identity)
                )
                filename = f"{handle}.json"
                payload_bytes = self._json_bytes(json_payload)
                item = {"file": filename, "expires_at": expires_at.isoformat()}
                prepared.append((handle, filename, payload_bytes, item))

            for handle, filename, payload_bytes, item in prepared:
                target = self._safe_child(directory, filename)
                previous_item = updated["items"].get(handle)
                previous_bytes = None
                if previous_item is not None:
                    if previous_item.get("file") != filename:
                        raise SourceCollectionError("temporary_index_invalid", "Индекс временных карточек повреждён.")
                    read_failed = False
                    try:
                        previous_bytes = target.read_bytes()
                    except (FileNotFoundError, OSError):
                        read_failed = True
                    if read_failed:
                        raise SourceCollectionError(
                            "temporary_content_invalid",
                            "Временная карточка повреждена.",
                        ) from None
                    if previous_bytes == payload_bytes and previous_item == item:
                        handles.append(handle)
                        continue
                self._atomic_write_bytes(target, payload_bytes)
                updated["items"][handle] = item
                mutations.append(StoreMutation(
                    handle=handle,
                    filename=filename,
                    applied_item=item,
                    applied_digest=hashlib.sha256(payload_bytes).hexdigest(),
                    previous_item=dict(previous_item) if previous_item is not None else None,
                    previous_bytes=previous_bytes,
                ))
                handles.append(handle)
            atomic_write_json(self._safe_child(directory, "index.json"), updated, replacer=self.replacer)
        except SourceCollectionError as error:
            source_error = error
        except Exception:
            store_failed = True
        if source_error is not None:
            self._restore_uncommitted(directory, mutations)
            raise source_error
        if store_failed:
            self._restore_uncommitted(directory, mutations)
            raise SourceCollectionError(
                "temporary_store_failed",
                "Не удалось атомарно сохранить временные карточки.",
            ) from None
        return StoreReceipt(handles=tuple(handles), mutations=tuple(mutations))

    def rollback_page(self, owner, source, receipt):
        if not isinstance(receipt, StoreReceipt) or not receipt.mutations:
            return
        directory = self.directory(owner, source)
        self._prepare_directory(directory)
        with self._directory_lock(directory):
            index = self._load_index(directory)
            updated = {"version": 1, "items": dict(index["items"])}
            removable = []
            for mutation in receipt.mutations:
                if updated["items"].get(mutation.handle) != mutation.applied_item:
                    continue
                path = self._safe_child(directory, mutation.filename)
                read_failed = False
                try:
                    current_bytes = path.read_bytes()
                except FileNotFoundError:
                    current_bytes = b""
                except OSError:
                    read_failed = True
                if read_failed:
                    raise SourceCollectionError(
                        "temporary_rollback_failed",
                        "Не удалось отменить публикацию временных карточек.",
                    ) from None
                current_digest = hashlib.sha256(current_bytes).hexdigest()
                if mutation.previous_item is None:
                    if current_digest != mutation.applied_digest:
                        continue
                    updated["items"].pop(mutation.handle)
                    removable.append(path)
                else:
                    previous_digest = hashlib.sha256(mutation.previous_bytes).hexdigest()
                    if current_digest == mutation.applied_digest:
                        self._atomic_write_bytes(path, mutation.previous_bytes)
                    elif current_digest != previous_digest:
                        continue
                    updated["items"][mutation.handle] = mutation.previous_item
            if updated["items"] == index["items"]:
                return
            write_failed = False
            try:
                atomic_write_json(self._safe_child(directory, "index.json"), updated, replacer=self.replacer)
            except OSError:
                write_failed = True
            if write_failed:
                raise SourceCollectionError(
                    "temporary_rollback_failed",
                    "Не удалось отменить публикацию временных карточек.",
                ) from None
            self._unlink_all(removable)

    def list(self, owner, source):
        directory = self.directory(owner, source)
        self._prepare_directory(directory)
        with self._directory_lock(directory):
            self._purge_directory_locked(directory)
            return tuple(self._load_index(directory)["items"])

    def read(self, owner, source, handle):
        directory = self.directory(owner, source)
        self._prepare_directory(directory)
        with self._directory_lock(directory):
            index = self._load_index(directory)
            item = index["items"].get(str(handle))
            if not item:
                raise SourceCollectionError("temporary_content_missing", "Временная карточка недоступна.")
            expires_at = self._parse_expiry(item.get("expires_at"))
            if expires_at is None or expires_at <= self._utc(self.clock()):
                self._purge_directory_locked(directory)
                raise SourceCollectionError("temporary_content_expired", "Срок хранения временной карточки истёк.")
            path = self._safe_child(directory, str(item.get("file") or ""))
            failure = None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                failure = (
                    "temporary_content_missing",
                    "Временная карточка недоступна.",
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                failure = (
                    "temporary_content_invalid",
                    "Временная карточка повреждена.",
                )
            if failure is not None:
                if failure[0] == "temporary_content_missing":
                    self._remove_handle(directory, index, str(handle))
                raise SourceCollectionError(*failure) from None
            if not isinstance(payload, dict) or set(payload) != {"raw", "normalized", "derived", "cache"}:
                raise SourceCollectionError("temporary_content_invalid", "Временная карточка повреждена.")
            return payload

    def purge_expired(self, owner, source):
        directory = self.directory(owner, source)
        self._prepare_directory(directory)
        with self._directory_lock(directory):
            return self._purge_directory_locked(directory)

    def purge_all(self):
        root = Path(settings.TEMPORARY_ROOT) / "rocketship"
        total = PurgeReport()
        path_failed = False
        try:
            root_exists = root.exists()
        except OSError:
            path_failed = True
        if path_failed:
            self._unsafe_path()
        if not root_exists:
            return total
        root = self._validate_root(root)
        for owner_directory in self._safe_directories(root):
            for source_directory in self._safe_directories(owner_directory):
                with self._directory_lock(source_directory):
                    report = self._purge_directory_locked(source_directory)
                total = PurgeReport(
                    expired=total.expired + report.expired,
                    orphans=total.orphans + report.orphans,
                )
        return total

    def _purge_directory_locked(self, directory):
        directory = self._validate_directory(directory)
        index = self._load_index(directory)
        now = self._utc(self.clock())
        kept = {}
        expired = 0
        referenced = set()
        for handle, item in index["items"].items():
            filename = str(item.get("file") or "")
            expiry = self._parse_expiry(item.get("expires_at"))
            path = self._safe_child(directory, filename)
            path_failed = False
            try:
                is_file = path.is_file()
            except OSError:
                path_failed = True
            if path_failed:
                self._unsafe_path()
            if expiry is None or expiry <= now or not is_file:
                expired += 1
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            kept[handle] = item
            referenced.add(filename)
        changed = kept != index["items"]
        if changed:
            write_failed = False
            try:
                atomic_write_json(
                    self._safe_child(directory, "index.json"),
                    {"version": 1, "items": kept},
                    replacer=self.replacer,
                )
            except OSError:
                write_failed = True
            if write_failed:
                raise SourceCollectionError(
                    "temporary_index_write_failed",
                    "Не удалось обновить индекс временных карточек.",
                ) from None
        orphans = 0
        for path in self._safe_entries(directory):
            if path.name == "index.json" or (path.name in referenced and path.suffix == ".json"):
                continue
            if path.is_file() and (path.suffix in {".json", ".tmp"} or path.name.startswith(".stage-")):
                try:
                    path.unlink()
                    orphans += 1
                except FileNotFoundError:
                    pass
        return PurgeReport(expired=expired, orphans=orphans)

    @contextmanager
    def _directory_lock(self, directory):
        directory = self._validate_directory(directory)
        lock = self._safe_child(directory, ".store.lock")
        deadline = self.monotonic() + max(float(self.lock_timeout), 0)
        while True:
            busy = False
            try:
                lock.mkdir()
                break
            except FileExistsError:
                if not self._validate_lock(lock, directory):
                    if self.monotonic() >= deadline:
                        busy = True
                    else:
                        continue
                elif self.monotonic() >= deadline:
                    busy = True
                else:
                    self.sleeper(0.01)
            if busy:
                raise SourceCollectionError(
                    "temporary_store_busy", "Временное хранилище занято.", retryable=True
                ) from None
        try:
            yield
        finally:
            try:
                lock.rmdir()
            except FileNotFoundError:
                pass

    def save_user_state(self, owner, handle, *, status, note):
        if status not in {"new", "saved", "applied", "interview", "closed"}:
            raise SourceCollectionError("invalid_user_state", "Статус временной карточки неизвестен.")
        path = self._user_state_path(owner, handle)
        failure = None
        try:
            atomic_write_json(path, {"status": status, "note": str(note or "")})
        except OSError:
            failure = SourceCollectionError(
                "user_state_write_failed",
                "Не удалось сохранить заметку временной карточки.",
            )
        if failure is not None:
            raise failure from None

    def read_user_state(self, owner, handle):
        path = self._user_state_path(owner, handle)
        failure = None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            failure = SourceCollectionError(
                "user_state_missing",
                "Состояние временной карточки недоступно.",
            )
        if failure is not None:
            raise failure from None
        if not isinstance(value, dict) or set(value) != {"status", "note"}:
            raise SourceCollectionError("user_state_invalid", "Состояние временной карточки повреждено.")
        return value

    @classmethod
    def _user_state_path(cls, owner, handle):
        safe_handle = "".join(character for character in str(handle) if character.isalnum() or character in "-_")
        if not getattr(owner, "pk", None) or safe_handle != str(handle) or not safe_handle:
            raise SourceCollectionError("invalid_handle", "Идентификатор временной карточки некорректен.")
        private_root = Path(settings.PRIVATE_ROOT)
        cls._prepare_private_directory(private_root, boundary=None)
        state_root = private_root / "rocketship-user-state"
        cls._prepare_private_directory(state_root, boundary=private_root)
        owner_root = state_root / str(owner.pk)
        cls._prepare_private_directory(owner_root, boundary=state_root)
        path = owner_root / f"{safe_handle}.json"
        if cls._is_reparse(path):
            cls._unsafe_private_path()
        path_failed = False
        try:
            if path.resolve(strict=False).parent != owner_root.resolve(strict=True):
                cls._unsafe_private_path()
        except (OSError, RuntimeError):
            path_failed = True
        if path_failed:
            cls._unsafe_private_path()
        return path

    def _remove_handle(self, directory, index, handle):
        updated = {"version": 1, "items": dict(index["items"])}
        updated["items"].pop(handle, None)
        failure = False
        try:
            atomic_write_json(self._safe_child(directory, "index.json"), updated, replacer=self.replacer)
        except OSError:
            failure = True
        if failure:
            raise SourceCollectionError(
                "temporary_index_write_failed",
                "Не удалось обновить индекс временных карточек.",
            ) from None

    def _load_index(self, directory):
        path = self._safe_child(directory, "index.json")
        failure = None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "items": {}}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            failure = SourceCollectionError(
                "temporary_index_invalid",
                "Индекс временных карточек повреждён.",
            )
        if failure is not None:
            raise failure from None
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("items"), dict):
            raise SourceCollectionError("temporary_index_invalid", "Индекс временных карточек повреждён.")
        for handle, item in value["items"].items():
            safe_handle = "".join(character for character in str(handle) if character.isalnum() or character in "-_")
            if (
                not safe_handle
                or safe_handle != handle
                or not isinstance(item, dict)
                or item.get("file") != f"{handle}.json"
                or TemporaryRocketshipStore._parse_expiry(item.get("expires_at")) is None
            ):
                raise SourceCollectionError("temporary_index_invalid", "Индекс временных карточек повреждён.")
        return value

    def _prepare_directory(self, directory):
        root = Path(settings.TEMPORARY_ROOT) / "rocketship"
        path_failed = False
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            path_failed = True
        if path_failed:
            self._unsafe_path()
        root = self._validate_root(root)
        directory = Path(directory)
        if directory.parent.parent != root:
            self._unsafe_path()
        for candidate in (directory.parent, directory):
            path_failed = False
            try:
                candidate.mkdir()
            except FileExistsError:
                pass
            except OSError:
                path_failed = True
            if path_failed:
                self._unsafe_path()
            self._validate_directory(candidate, root=root)
        return directory

    def _validate_root(self, root):
        root = Path(root)
        if self._is_reparse(root):
            self._unsafe_path()
        path_failed = False
        try:
            if not root.is_dir():
                self._unsafe_path()
            resolved = root.resolve(strict=True)
        except (OSError, RuntimeError):
            path_failed = True
        if path_failed:
            self._unsafe_path()
        return resolved

    def _validate_directory(self, directory, *, root=None):
        directory = Path(directory)
        lexical_root = Path(settings.TEMPORARY_ROOT) / "rocketship"
        resolved_root = self._validate_root(lexical_root) if root is None else Path(root).resolve(strict=True)
        if self._is_reparse(directory):
            self._unsafe_path()
        path_failed = False
        try:
            resolved = directory.resolve(strict=True)
            if not directory.is_dir() or not resolved.is_relative_to(resolved_root):
                self._unsafe_path()
        except (OSError, RuntimeError):
            path_failed = True
        if path_failed:
            self._unsafe_path()
        return directory

    def _safe_child(self, directory, name):
        directory = self._validate_directory(directory)
        if not name or Path(name).name != name or name in {".", ".."}:
            self._unsafe_path()
        child = directory / name
        if self._is_reparse(child):
            self._unsafe_path()
        path_failed = False
        try:
            resolved_directory = directory.resolve(strict=True)
            if child.resolve(strict=False).parent != resolved_directory:
                self._unsafe_path()
        except (OSError, RuntimeError):
            path_failed = True
        if path_failed:
            self._unsafe_path()
        return child

    def _safe_directories(self, directory):
        safe = []
        for path in self._safe_entries(directory):
            path_failed = False
            try:
                if path.is_dir():
                    safe.append(self._validate_directory(path))
            except OSError:
                path_failed = True
            if path_failed:
                self._unsafe_path()
        return tuple(safe)

    def _safe_entries(self, directory):
        directory = self._validate_directory(directory)
        entries = []
        path_failed = False
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    path = Path(entry.path)
                    if self._is_reparse(path):
                        self._unsafe_path()
                    path.resolve(strict=True).relative_to(directory.resolve(strict=True))
                    entries.append(path)
        except SourceCollectionError:
            raise
        except (OSError, RuntimeError, ValueError):
            path_failed = True
        if path_failed:
            self._unsafe_path()
        return tuple(entries)

    def _restore_uncommitted(self, directory, mutations):
        failure = False
        try:
            for mutation in reversed(mutations):
                path = self._safe_child(directory, mutation.filename)
                if mutation.previous_bytes is None:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    self._atomic_write_bytes(path, mutation.previous_bytes)
        except Exception:
            failure = True
        if failure:
            raise SourceCollectionError(
                "temporary_store_failed",
                "Не удалось атомарно сохранить временные карточки.",
            ) from None

    def _atomic_write_bytes(self, path, value):
        descriptor, staged_name = tempfile.mkstemp(prefix=".stage-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            self.replacer(staged_name, path)
        except Exception:
            try:
                os.unlink(staged_name)
            except FileNotFoundError:
                pass
            raise

    def _validate_lock(self, lock, directory):
        path_failed = False
        try:
            value = lock.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            path_failed = True
        if path_failed:
            self._unsafe_path()
        if stat.S_ISLNK(value.st_mode):
            self._unsafe_path()
        attributes = getattr(value, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if attributes & reparse_flag or not stat.S_ISDIR(value.st_mode):
            self._unsafe_path()
        path_failed = False
        try:
            if lock.resolve(strict=True).parent != directory.resolve(strict=True):
                self._unsafe_path()
        except FileNotFoundError:
            return False
        except (OSError, RuntimeError):
            path_failed = True
        if path_failed:
            self._unsafe_path()
        return True

    @classmethod
    def _prepare_private_directory(cls, path, *, boundary):
        path = Path(path)
        path_failed = False
        try:
            path.mkdir(parents=boundary is None, exist_ok=True)
        except OSError:
            path_failed = True
        if path_failed:
            cls._unsafe_private_path()
        if cls._is_reparse(path):
            cls._unsafe_private_path()
        path_failed = False
        try:
            resolved = path.resolve(strict=True)
            if not path.is_dir():
                cls._unsafe_private_path()
            if boundary is not None:
                resolved_boundary = Path(boundary).resolve(strict=True)
                if not resolved.is_relative_to(resolved_boundary) or resolved == resolved_boundary:
                    cls._unsafe_private_path()
        except (OSError, RuntimeError):
            path_failed = True
        if path_failed:
            cls._unsafe_private_path()
        return path

    @staticmethod
    def _is_reparse(path):
        path_failed = False
        try:
            value = Path(path).lstat()
        except FileNotFoundError:
            return False
        except OSError:
            path_failed = True
        if path_failed:
            TemporaryRocketshipStore._unsafe_path()
        if stat.S_ISLNK(value.st_mode):
            return True
        attributes = getattr(value, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse_flag)

    @staticmethod
    def _unsafe_path():
        raise SourceCollectionError(
            "temporary_path_unsafe",
            "Временное хранилище содержит небезопасный путь.",
        ) from None

    @staticmethod
    def _unsafe_private_path():
        raise SourceCollectionError(
            "private_path_unsafe",
            "Приватное хранилище содержит небезопасный путь.",
        ) from None

    @staticmethod
    def _json_value(value):
        return json.loads(json.dumps(
            value,
            ensure_ascii=False,
            default=lambda item: item.isoformat() if hasattr(item, "isoformat") else str(item),
        ))

    @staticmethod
    def _json_bytes(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _stable_identity(record):
        external_id = str(record.get("external_id") or "").strip()
        canonical_url = str(record.get("canonical_url") or "").strip()
        if external_id:
            material = {"kind": "external_id", "value": external_id}
        elif canonical_url:
            material = {"kind": "canonical_url", "value": canonical_url}
        else:
            raise SourceCollectionError(
                "temporary_identity_ambiguous",
                "Временная карточка не содержит стабильную identity.",
            )
        return hashlib.sha256(json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()

    @staticmethod
    def _stable_handle(owner, source, page_key, identity_digest):
        material = json.dumps(
            {
                "owner": str(owner.pk),
                "source": source.slug,
                "page": str(page_key),
                "identity": identity_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()[:40]

    @staticmethod
    def _parse_expiry(value):
        try:
            return TemporaryRocketshipStore._utc(datetime.fromisoformat(str(value)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _utc(value):
        if not isinstance(value, datetime):
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _unlink_all(paths):
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
