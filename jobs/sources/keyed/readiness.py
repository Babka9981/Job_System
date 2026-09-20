from dataclasses import dataclass


@dataclass(frozen=True)
class ManualOnlySource:
    slug: str
    status: str
    reason: str
    manual_url: str


def manual_only_sources():
    reason = "Разрешённый массовый канал не подтверждён; скрапинг отключён, используйте ручную ссылку."
    return (
        ManualOnlySource("remocate", "not_ready", reason, "https://www.remocate.app/"),
        ManualOnlySource("itcharm", "not_ready", reason, "https://itcharm.com/vacancies"),
    )

