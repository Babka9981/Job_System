from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Coverage:
    window_start: datetime | None = None
    truncated: bool = False
    reason: str = ""

    def as_dict(self):
        return {
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "truncated": self.truncated,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Batch:
    records: tuple[dict[str, Any], ...]
    next_cursor: str | None = None
    coverage: Coverage = Coverage()
    provider_updated_at: datetime | None = None


class SourceCollectionError(Exception):
    """A safe, user-displayable upstream failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        super().__init__(message)
