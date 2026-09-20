from zoneinfo import ZoneInfo

from django.conf import settings
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from jobs.core.access import owner_required
from jobs.models.models import Source
from jobs.sources.core.registry import seed_sources


MODE_LABELS = {
    "public_api": "Открытый API",
    "public_mcp": "Открытый MCP",
    "api_key": "API с ключом",
    "paid_api": "Платный API",
    "telegram_session": "Telegram-сессия",
    "not_ready": "Подключение не готово",
}
STATUS_TONES = {
    Source.Status.READY: "success",
    Source.Status.LIMITED: "warning",
    Source.Status.NEEDS_ACCESS: "info",
    Source.Status.NOT_CONFIGURED: "neutral",
    Source.Status.ERROR: "danger",
}


def _interval_label(seconds):
    try:
        if isinstance(seconds, bool) or (isinstance(seconds, float) and not seconds.is_integer()):
            raise ValueError
        seconds = int(seconds or 0)
    except (TypeError, ValueError, OverflowError):
        return "Ошибка настройки интервала"
    if not seconds:
        return "По расписанию сборщика"
    if seconds % 86400 == 0:
        return f"Раз в {seconds // 86400} сут."
    if seconds % 3600 == 0:
        return f"Раз в {seconds // 3600} ч."
    return f"Не чаще раза в {seconds // 60} мин."


def _display_time(value):
    if not value:
        return "Ещё не было"
    if isinstance(value, str):
        value = parse_datetime(value)
        if not value:
            return "Неизвестно"
    local = timezone.localtime(value, ZoneInfo(settings.USER_TIME_ZONE))
    return local.strftime("%d.%m.%Y %H:%M")


def _coverage_label(coverage):
    if not coverage:
        return "Ещё не проверено"
    parts = []
    if coverage.get("window_start"):
        parts.append(f'Окно с {_display_time(coverage["window_start"])}')
    if coverage.get("reason"):
        parts.append(coverage["reason"])
    if coverage.get("truncated"):
        parts.append("Ограниченная выдача")
    return " · ".join(parts) if parts else "Граница окна неизвестна"


def _row(source):
    config = source.config if isinstance(source.config, dict) else {}
    coverage = source.coverage if isinstance(source.coverage, dict) else {}
    error = config.get("last_error") or {}
    return {
        "source": source,
        "mode": MODE_LABELS.get(config.get("access_mode"), config.get("access_mode") or "Не указан"),
        "status_label": source.get_status_display(),
        "tone": STATUS_TONES.get(source.status, "neutral"),
        "last_success": _display_time(source.last_success),
        "last_check": _display_time(config.get("last_check")),
        "error": error.get("message") or "Нет",
        "reason": config.get("reason") or "Нет",
        "coverage_reason": _coverage_label(coverage),
        "service_interval": _interval_label(config.get("service_interval_seconds")),
        "provider_freshness": config.get("provider_freshness") or "Не заявлена",
    }


@owner_required
def sources_view(request):
    seed_sources(request.user)
    sources = list(Source.objects.filter(owner=request.user).order_by("kind", "name"))
    rows = [_row(source) for source in sources]
    return render(request, "sources/index.html", {
        "section": "sources",
        "title": "Источники",
        "rows": rows,
        "total_count": len(rows),
        "site_count": sum(row["source"].kind == "site" for row in rows),
        "telegram_count": sum(row["source"].kind == "telegram" for row in rows),
    })
