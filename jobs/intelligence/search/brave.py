import inspect
import json
import os
import time
from urllib.parse import urlencode

from jobs.intelligence.http_process import HTTPProcessError, run_http_exchange
from .base import SearchHit, SearchProviderError, SearchProviderUnavailable


def _http_transport(*, url, api_key, params, timeout, deadline, clock, process_factory=None):
    request_url = f"{url}?{urlencode(params)}"
    safe_error = None
    result = None
    try:
        result = run_http_exchange({
            "url": request_url,
            "method": "GET",
            "headers": {"Accept": "application/json", "X-Subscription-Token": api_key},
            "max_bytes": 5_000_000,
            "socket_timeout": min(timeout, max(deadline - clock(), 0.1)),
        }, deadline=deadline, clock=clock, process_factory=process_factory)
    except HTTPProcessError as exc:
        if exc.code == "deadline_exceeded":
            safe_error = SearchProviderError("deadline_exceeded", "Истёк лимит времени веб-поиска.")
        elif exc.code == "too_large":
            safe_error = SearchProviderError("invalid_response", "Ответ Brave превышает лимит.")
        else:
            safe_error = SearchProviderUnavailable("provider_unavailable", "Brave временно недоступен.")
    if safe_error is not None:
        raise safe_error
    status = result["status"]
    if status == 429:
        raise SearchProviderUnavailable("quota_exceeded", "Квота Brave исчерпана.")
    if status >= 500:
        raise SearchProviderUnavailable("provider_unavailable", "Brave временно недоступен.")
    if status != 200:
        try:
            error_body = json.loads(result["body"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError):
            error_body = None
        error = error_body.get("error") if isinstance(error_body, dict) else None
        if isinstance(error, dict) and error.get("code") == "OPTION_NOT_IN_PLAN":
            raise SearchProviderError("option_not_in_plan", "Тариф Brave не включает LLM Context.")
        raise SearchProviderError("provider_error", "Brave отклонил поисковый запрос.")
    parsed = None
    try:
        parsed = json.loads(result["body"])
    except (TypeError, ValueError, UnicodeDecodeError):
        safe_error = SearchProviderError("invalid_response", "Brave вернул некорректный ответ.")
    if safe_error is not None:
        raise safe_error
    return parsed


def _deadline_aware(function):
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.kind == parameter.VAR_KEYWORD for parameter in parameters) or {"deadline", "clock"} <= {
        parameter.name for parameter in parameters
    }


class BraveSearchProvider:
    """Brave LLM Context is used only to discover candidate URLs."""

    endpoint = "https://api.search.brave.com/res/v1/llm/context"
    web_endpoint = "https://api.search.brave.com/res/v1/web/search"
    provider_name = "brave"
    price_env = "BRAVE_LLM_CONTEXT_COST_USD"
    is_mock = False

    def __init__(self, *, api_key=None, transport=None, timeout=15):
        self.api_key = api_key if api_key is not None else os.environ.get("BRAVE_SEARCH_API_KEY", "")
        self.transport = transport or _http_transport
        self.timeout = min(max(float(timeout), 1.0), 30.0)

    @property
    def deadline_ready(self):
        return _deadline_aware(self.transport)

    def _search_endpoint(self, query, *, max_results, deadline, clock, web):
        if not self.api_key:
            raise SearchProviderUnavailable("missing_api_key", "Brave API key не настроен.")
        query = " ".join(str(query).split())
        if not query:
            raise SearchProviderError("invalid_query", "Пустой поисковый запрос.")
        clock = clock or time.monotonic
        deadline = deadline if deadline is not None else clock() + self.timeout
        if clock() >= deadline:
            raise SearchProviderError("deadline_exceeded", "Истёк лимит времени веб-поиска.")
        if not self.deadline_ready:
            raise SearchProviderUnavailable("unsafe_transport", "Search transport не поддерживает отменяемый deadline.")
        limit = min(max(1, int(max_results)), 5)
        is_web = web
        if is_web:
            url = self.web_endpoint
            params = {"q": query[:500], "count": limit, "safesearch": "strict"}
        else:
            url = self.endpoint
            params = {
                    "q": query[:500],
                    "count": limit,
                    "maximum_number_of_urls": limit,
                    "maximum_number_of_tokens": 1024,
                    "maximum_number_of_tokens_per_url": 512,
                    "safesearch": "strict",
            }
        body = self.transport(
            url=url,
            api_key=self.api_key,
            params=params,
            timeout=min(self.timeout, deadline - clock()),
            deadline=deadline,
            clock=clock,
        )
        if clock() >= deadline:
            raise SearchProviderError("deadline_exceeded", "Истёк лимит времени веб-поиска.")
        if not is_web:
            grounding = body.get("grounding") if isinstance(body, dict) else None
            items = grounding.get("generic") if isinstance(grounding, dict) else None
        else:
            web_payload = body.get("web") if isinstance(body, dict) else None
            items = web_payload.get("results") if isinstance(web_payload, dict) else None
        if not isinstance(items, list):
            raise SearchProviderError("invalid_response", "Brave вернул некорректный ответ.")
        hits = []
        for item in items[:limit]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            if not is_web:
                snippets = item.get("snippets") if isinstance(item.get("snippets"), list) else []
                snippet = " ".join(str(value) for value in snippets)
                published_at = None
            else:
                snippet = str(item.get("description") or "")
                published_at = str(item.get("page_age") or "") or None
            hits.append(SearchHit(
                url=item["url"],
                title=str(item.get("title") or "")[:500],
                snippet=snippet[:2000],
                published_at=published_at,
            ))
        return hits

    def search_context(self, query: str, *, max_results: int = 5, deadline=None, clock=None) -> list[SearchHit]:
        return self._search_endpoint(
            query, max_results=max_results, deadline=deadline, clock=clock, web=False,
        )

    def search_web(self, query: str, *, max_results: int = 5, deadline=None, clock=None) -> list[SearchHit]:
        return self._search_endpoint(
            query, max_results=max_results, deadline=deadline, clock=clock, web=True,
        )

    def search(self, query: str, *, max_results: int = 5, deadline=None, clock=None) -> list[SearchHit]:
        try:
            return self.search_context(
                query, max_results=max_results, deadline=deadline, clock=clock,
            )
        except SearchProviderError as exc:
            if exc.code != "option_not_in_plan":
                raise
        return self.search_web(
            query, max_results=max_results, deadline=deadline, clock=clock,
        )
