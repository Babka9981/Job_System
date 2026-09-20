import base64
import inspect
import json
import os
import time

from jobs.intelligence.http_process import HTTPProcessError, run_http_exchange
from .base import SearchHit, SearchProviderError, SearchProviderUnavailable


def _http_transport(*, url, api_key, payload, timeout, deadline, clock, process_factory=None):
    try:
        result = run_http_exchange({
            "url": url,
            "method": "POST",
            "headers": {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            "body_b64": base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii"),
            "max_bytes": 5_000_000,
            "socket_timeout": min(timeout, max(deadline - clock(), 0.1)),
        }, deadline=deadline, clock=clock, process_factory=process_factory)
        return json.loads(result["body"])
    except HTTPProcessError as exc:
        if exc.code == "deadline_exceeded":
            raise SearchProviderError("deadline_exceeded", "Истёк лимит времени веб-поиска.") from None
        if exc.code == "too_large":
            raise SearchProviderError("invalid_response", "Ответ веб-поиска превышает лимит.") from None
        raise SearchProviderError("provider_error", "Веб-поиск временно недоступен.") from exc
    except (ValueError, TypeError) as exc:
        raise SearchProviderError("provider_error", "Веб-поиск временно недоступен.") from exc


def _deadline_aware(function):
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.kind == parameter.VAR_KEYWORD for parameter in parameters) or {"deadline", "clock"} <= {
        parameter.name for parameter in parameters
    }


class TavilySearchProvider:
    """Candidate discovery only: snippets are never accepted as research evidence.

    Verified 2026-09-20 against Tavily's official Search docs. Pricing and terms can
    change, so activation still requires a local key and an owner-selected plan.
    """

    endpoint = "https://api.tavily.com/search"
    is_mock = False

    def __init__(self, *, api_key=None, transport=None, timeout=15):
        self.api_key = api_key if api_key is not None else os.environ.get("TAVILY_API_KEY", "")
        self.transport = transport or _http_transport
        self.timeout = min(max(float(timeout), 1.0), 30.0)

    @property
    def deadline_ready(self):
        return _deadline_aware(self.transport)

    def search(self, query: str, *, max_results: int = 5, deadline=None, clock=None) -> list[SearchHit]:
        if not self.api_key:
            raise SearchProviderUnavailable("missing_api_key", "Tavily API key не настроен.")
        query = " ".join(str(query).split())
        if not query:
            raise SearchProviderError("invalid_query", "Пустой поисковый запрос.")
        clock = clock or time.monotonic
        deadline = deadline if deadline is not None else clock() + self.timeout
        if clock() >= deadline:
            raise SearchProviderError("deadline_exceeded", "Истёк лимит времени веб-поиска.")
        if not self.deadline_ready:
            raise SearchProviderUnavailable("unsafe_transport", "Search transport не поддерживает отменяемый deadline.")
        payload = {
            "query": query[:500],
            "topic": "general",
            "search_depth": "basic",
            "auto_parameters": False,
            "max_results": min(max(1, int(max_results)), 5),
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_usage": True,
            "safe_search": True,
        }
        body = self.transport(
            url=self.endpoint, api_key=self.api_key, payload=payload,
            timeout=min(self.timeout, deadline - clock()), deadline=deadline, clock=clock,
        )
        if clock() > deadline:
            raise SearchProviderError("deadline_exceeded", "Истёк лимит времени веб-поиска.")
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise SearchProviderError("invalid_response", "Веб-поиск вернул некорректный ответ.")
        hits = []
        for item in results[: payload["max_results"]]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            hits.append(SearchHit(
                url=item["url"],
                title=str(item.get("title") or "")[:500],
                snippet=str(item.get("content") or "")[:2000],
                published_at=str(item.get("published_date") or "") or None,
            ))
        return hits
