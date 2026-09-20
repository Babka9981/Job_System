from dataclasses import dataclass

from jobs.models.models import Source


SITE_SOURCES = (
    ("remote-ok", "Remote OK", "remote_ok", True, "public_api", "Открытый JSON API; статус подтвердится после проверки."),
    ("web3-career", "Web3.career", "web3_career", True, "api_key", "Нужен API-токен и проверка параметров доступа."),
    ("rvc", "Remote Vacancies Club", "rvc", True, "public_mcp", "Адаптер MCP будет подключён отдельным этапом."),
    ("remote-rocketship", "Remote Rocketship", "remote_rocketship", True, "paid_api", "Нужны платный план и API-токен."),
    ("remocate", "Remocate", "remocate", False, "not_ready", "Разрешённый массовый канал не подтверждён; скрапинг отключён."),
    ("itcharm", "ITCharm", "itcharm", False, "not_ready", "Штатный API или экспорт ещё не подтверждён; скрапинг отключён."),
    ("himalayas", "Himalayas", "himalayas", True, "public_api", "Открытый JSON API; данные поставщика обновляются раз в сутки."),
    ("jobicy", "Jobicy", "jobicy", True, "public_api", "Открытый JSON API; не чаще одного запроса в час."),
    ("crypto-jobs-list", "CryptoJobsList", "crypto_jobs_list", True, "api_key", "Нужны API-ключ и подтверждение индивидуальных условий."),
)

TELEGRAM_HANDLES = (
    "revacancy", "cyprusithr", "workayte", "remotegeekjob", "it_evacuation",
    "geekjobs", "forproducts", "hr_itwork", "product_jobs", "evacuatejobs",
    "holder_job_marketing", "theyseeku_it", "itfreelancers", "zarubezhom_jobs",
    "workingincrypto", "Remoteit", "it_vakansii_jobs", "it_vak", "it_vac",
    "Relocats", "cryptojobslist", "cryptoheadhunter", "young_relocate", "devs_it",
    "products_jobs_projects", "remote_jobs_relocate", "job_web3", "opento_crypto",
    "forchiefs", "rabota_kripta", "jobs_project_managers", "cryptoMjob",
    "cryptovakansii", "web30job", "jtbl_vacancy", "techjobsdaily", "hrlunapark",
    "cozy_hr", "cryptoweb3work", "DataOpportunity", "unicastjobs",
)


@dataclass(frozen=True)
class SeedResult:
    created: int
    updated: int


def _status(access_mode):
    if access_mode in {"api_key", "paid_api"}:
        return Source.Status.NEEDS_ACCESS
    return Source.Status.NOT_CONFIGURED


def seed_sources(owner):
    """Create the canonical 9-site/41-channel catalog without duplicates."""
    definitions = []
    for slug, name, adapter, enabled, access_mode, reason in SITE_SOURCES:
        definitions.append({
            "slug": slug,
            "name": name,
            "kind": "site",
            "adapter": adapter,
            "enabled": enabled,
            "status": _status(access_mode),
            "config": {
                "access_mode": access_mode,
                "reason": reason,
                "service_interval_seconds": 86400 if adapter == "himalayas" else 3600 if adapter == "jobicy" else 0,
                "provider_freshness": "24 часа" if adapter == "himalayas" else "Не заявлена",
            },
        })
    for handle in TELEGRAM_HANDLES:
        definitions.append({
            "slug": f"telegram-{handle.lower()}",
            "name": f"@{handle}",
            "kind": "telegram",
            "adapter": "telegram",
            "enabled": True,
            "status": Source.Status.NEEDS_ACCESS,
            "config": {
                "access_mode": "telegram_session",
                "url": f"https://t.me/{handle}",
                "reason": "Нужна пользовательская Telegram-сессия; право обработки контента подтверждается отдельно.",
                "service_interval_seconds": 0,
                "provider_freshness": "По публикациям канала",
            },
        })

    created = 0
    for definition in definitions:
        source, was_created = Source.objects.get_or_create(
            owner=owner,
            slug=definition["slug"],
            defaults={key: value for key, value in definition.items() if key != "slug"},
        )
        if not was_created:
            source.name = definition["name"]
            source.kind = definition["kind"]
            source.adapter = definition["adapter"]
            existing_config = source.config if isinstance(source.config, dict) else {}
            source.config = {**definition["config"], **existing_config}
            source.save(update_fields=["name", "kind", "adapter", "config", "updated_at"])
        created += int(was_created)
    return SeedResult(created=created, updated=len(definitions) - created)
