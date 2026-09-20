import json
import os
import tempfile
from datetime import UTC
from pathlib import Path

from django.conf import settings

from jobs.sources.core.contracts import SourceCollectionError


def literal_true(value):
    return isinstance(value, str) and value.strip().casefold() == "true"


def atomic_write_json(path, value, *, replacer=os.replace):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(prefix=".stage-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        replacer(staged_name, path)
    except Exception:
        try:
            os.unlink(staged_name)
        except FileNotFoundError:
            pass
        raise


class DurableUtcCounter:
    """A tiny fail-closed daily counter protected by an atomic directory lock."""

    def __init__(self, namespace, *, clock, root=None):
        safe_namespace = "".join(character for character in namespace if character.isalnum() or character in "-_")
        self.root = Path(root or settings.PRIVATE_ROOT) / "source-quotas" / safe_namespace
        self.clock = clock

    def increment(self, field, amount, limit, error_code, error_message):
        now = self.clock()
        now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        day = now.date().isoformat()
        path = self.root / f"{day}.json"
        lock = self.root / f"{day}.lock"
        self.root.mkdir(parents=True, exist_ok=True)
        lock_error = None
        try:
            lock.mkdir()
        except FileExistsError:
            lock_error = SourceCollectionError(
                "quota_state_busy",
                "Состояние лимита временно занято.",
                retryable=True,
            )
        if lock_error is not None:
            raise lock_error from None
        try:
            state_error = None
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                state = {"date": day}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                state_error = SourceCollectionError(
                    "quota_state_invalid",
                    "Состояние дневного лимита повреждено.",
                )
            if state_error is not None:
                raise state_error from None
            current = state.get(field, 0)
            if isinstance(current, bool) or not isinstance(current, int) or current < 0:
                raise SourceCollectionError("quota_state_invalid", "Состояние дневного лимита повреждено.")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise SourceCollectionError("quota_amount_invalid", "Значение дневного лимита некорректно.")
            if current + amount > limit:
                raise SourceCollectionError(error_code, error_message)
            state[field] = current + amount
            write_error = None
            try:
                atomic_write_json(path, state)
            except OSError:
                write_error = SourceCollectionError(
                    "quota_state_write_failed",
                    "Не удалось сохранить дневной лимит.",
                )
            if write_error is not None:
                raise write_error from None
            return state[field]
        finally:
            try:
                lock.rmdir()
            except FileNotFoundError:
                pass
