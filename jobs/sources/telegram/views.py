from django.contrib import messages
from django.shortcuts import redirect, render

from jobs.core.access import owner_required
from jobs.models.models import Source
from jobs.sources.core.registry import seed_sources
from jobs.sources.telegram.links import TelegramLinkError
from jobs.sources.telegram.registry import add_or_enable_source, set_source_enabled
from jobs.sources.telegram.session import telethon_runtime_status


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _checkpoint_message_id(config):
    value = _mapping(config.get("telegram_checkpoint")).get("message_id")
    return value if type(value) is int and value >= 0 else None


def _review_count(config):
    value = config.get("telegram_review")
    return len(value) if isinstance(value, list) else 0


def _safe_error_message(config):
    value = _mapping(config.get("last_error")).get("message")
    return value if isinstance(value, str) else ""


@owner_required
def telegram_sources_view(request):
    seed_sources(request.user)
    error = ""
    status_code = 200
    if request.method == "POST":
        action = request.POST.get("action", "")
        link = request.POST.get("link", "")
        try:
            if action == "add":
                source, created = add_or_enable_source(request.user, link)
                messages.success(request, f"{source.name}: {'добавлен' if created else 'включён'}.")
            elif action in {"enable", "disable"}:
                source = set_source_enabled(request.user, link, action == "enable")
                messages.success(request, f"{source.name}: {'включён' if source.enabled else 'отключён'}.")
            else:
                raise TelegramLinkError("Неизвестное действие с Telegram-источником.")
            return redirect(request.path)
        except (TelegramLinkError, Source.DoesNotExist) as exc:
            error = str(exc)
            status_code = 400

    rows = []
    for source in Source.objects.filter(owner=request.user, kind="telegram").order_by("name"):
        config = source.config if isinstance(source.config, dict) else {}
        rows.append({
            "source": source,
            "link": config.get("url", ""),
            "checkpoint": _checkpoint_message_id(config),
            "review_count": _review_count(config),
            "last_error": _safe_error_message(config),
        })
    return render(request, "sources/telegram/index.html", {
        "section": "sources",
        "title": "Telegram-источники",
        "rows": rows,
        "runtime": telethon_runtime_status(),
        "form_error": error,
        "live_verification": "Живая авторизация и доступ к 41 seed-каналу не проверена без локальной session.",
    }, status=status_code)
