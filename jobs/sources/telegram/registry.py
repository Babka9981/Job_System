from jobs.models.models import Source
from jobs.sources.telegram.links import normalize_source_link


def _source_for_link(owner, link):
    normalized = normalize_source_link(link)
    handle = normalized.rsplit("/", 1)[-1]
    source = Source.objects.filter(owner=owner, slug=f"telegram-{handle}", kind="telegram").first()
    return source, normalized, handle


def add_or_enable_source(owner, link):
    source, normalized, handle = _source_for_link(owner, link)
    if source is None:
        source = Source.objects.create(
            owner=owner,
            slug=f"telegram-{handle}",
            name=f"@{handle}",
            kind="telegram",
            adapter="telegram",
            enabled=True,
            status=Source.Status.NEEDS_ACCESS,
            config={
                "access_mode": "telegram_session",
                "url": normalized,
                "reason": "Нужна пользовательская Telegram-сессия; право обработки контента подтверждается отдельно.",
                "service_interval_seconds": 0,
                "provider_freshness": "По публикациям канала",
            },
        )
        return source, True
    config = dict(source.config) if isinstance(source.config, dict) else {}
    config["url"] = normalized
    config["access_mode"] = "telegram_session"
    source.config = config
    source.enabled = True
    source.save(update_fields=["config", "enabled", "updated_at"])
    return source, False


def set_source_enabled(owner, link, enabled):
    source, _, _ = _source_for_link(owner, link)
    if source is None:
        raise Source.DoesNotExist("Telegram-источник не найден.")
    source.enabled = enabled is True
    source.save(update_fields=["enabled", "updated_at"])
    return source

