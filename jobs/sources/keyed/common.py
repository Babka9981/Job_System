import os

from jobs.sources.core.contracts import SourceCollectionError


def required_secret(name, environ=None):
    value = (os.environ if environ is None else environ).get(name, "").strip()
    if not value:
        raise SourceCollectionError("credentials_missing", "Источник не настроен: добавьте данные доступа локально.")
    return value


def bounded_int(value, *, default, minimum, maximum):
    if isinstance(value, bool):
        return default
    try:
        return min(max(int(value), minimum), maximum)
    except (TypeError, ValueError, OverflowError):
        return default
