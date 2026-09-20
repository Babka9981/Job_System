import hashlib
from datetime import UTC, datetime

from jobs.vacancies.services import sanitize_source_text


def normalized_description(value):
    text = sanitize_source_text(value or "")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def parse_datetime(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # Providers publish both seconds and milliseconds.
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def positive(value):
    try:
        return value if value is not None and float(value) > 0 else None
    except (TypeError, ValueError):
        return None
