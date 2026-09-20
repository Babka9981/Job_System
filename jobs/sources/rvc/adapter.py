import asyncio
import json
import time

from jobs.sources.core.contracts import Batch, Coverage, SourceCollectionError


class RvcStreamableHttpToolCaller:
    """Synchronous production bridge to the official RVC Streamable HTTP MCP."""

    endpoint = "https://app.rvc.global/mcp"
    allowed_tools = frozenset({"rvc_search_jobs", "get_job"})

    def __init__(self, *, client_factory=None, timeout=20):
        self.client_factory = client_factory or self._official_client
        self.timeout = min(max(float(timeout), 1), 30)

    @staticmethod
    def _official_client(endpoint):
        from mcp import Client

        return Client(endpoint)

    def __call__(self, name, arguments):
        if name not in self.allowed_tools:
            raise SourceCollectionError("mcp_tool_not_allowed", "RVC MCP tool не разрешён.")
        return asyncio.run(self._call(name, arguments))

    async def _call(self, name, arguments):
        async with self.client_factory(self.endpoint) as client:
            return await asyncio.wait_for(
                client.call_tool(name, arguments),
                timeout=self.timeout,
            )


class RvcMcpClient:
    """Small synchronous boundary around an official Streamable HTTP MCP tool caller."""

    endpoint = "https://app.rvc.global/mcp"

    def __init__(self, *, call_tool, sleeper=time.sleep, max_attempts=3):
        self.call_tool = call_tool
        self.sleeper = sleeper
        self.max_attempts = max(1, min(int(max_attempts), 3))

    def search_jobs(self, **arguments):
        return self._call("rvc_search_jobs", arguments)

    def get_job(self, *, masked_id):
        return self._call("get_job", {"masked_id": masked_id})

    def _call(self, name, arguments):
        for attempt in range(1, self.max_attempts + 1):
            error = None
            try:
                return self._result(self.call_tool(name, arguments))
            except SourceCollectionError as exc:
                if not exc.retryable or attempt == self.max_attempts:
                    error = SourceCollectionError(exc.code, exc.safe_message, retryable=exc.retryable)
            except (TimeoutError, OSError):
                if attempt == self.max_attempts:
                    error = SourceCollectionError(
                        "upstream_mcp",
                        "Не удалось связаться с RVC.",
                        retryable=True,
                    )
            except Exception:
                error = SourceCollectionError("upstream_mcp", "RVC не выполнил запрос.")
            if error is not None:
                raise error from None
            self.sleeper(min(2 ** (attempt - 1), 4))
        raise SourceCollectionError("upstream_mcp", "Не удалось связаться с RVC.", retryable=True)

    @staticmethod
    def _result(result):
        if isinstance(result, dict):
            if result.get("isError") is True:
                raise SourceCollectionError("upstream_mcp_tool", "RVC MCP tool завершился ошибкой.")
            is_envelope = any(key in result for key in ("isError", "content", "structuredContent"))
            if not is_envelope:
                return result
            structured = result.get("structuredContent")
            content = result.get("content")
        else:
            if getattr(result, "isError", False) is True or getattr(result, "is_error", False) is True:
                raise SourceCollectionError("upstream_mcp_tool", "RVC MCP tool завершился ошибкой.")
            structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
            content = getattr(result, "content", None)
        if isinstance(structured, dict):
            return structured
        if isinstance(content, list):
            for block in content:
                block_type = getattr(block, "type", None) if not isinstance(block, dict) else block.get("type")
                text = getattr(block, "text", None) if not isinstance(block, dict) else block.get("text")
                if block_type not in (None, "text"):
                    continue
                if text:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        continue
        raise SourceCollectionError("invalid_payload", "RVC MCP вернул неожиданный формат.")


class RvcAdapter:
    """Normalize official RVC teaser tools without representing teasers as job bodies."""

    def __init__(self, *, client=None):
        self.client = client

    def collect(self, source, cursor=None):
        if cursor:
            raise SourceCollectionError("invalid_cursor", "RVC не предоставляет постраничный cursor.")
        if self.client is None:
            raise SourceCollectionError("mcp_client_missing", "RVC MCP-клиент не настроен.")
        config = source.config if isinstance(source.config, dict) else {}
        query = str(config.get("query") or "").strip()
        language = str(config.get("conversation_language_code") or "ru").strip()
        countries = config.get("target_country_codes", [])
        if not query or not isinstance(countries, list):
            raise SourceCollectionError("invalid_source_config", "Для RVC нужен конкретный запрос и список ISO-2 стран.")
        arguments = {
            "query": query,
            "conversation_language_code": language,
            "target_country_codes": countries,
            "limit": 10,
        }
        if not countries:
            arguments["work_arrangements"] = ["FULLY_REMOTE"]
        payload = self.client.search_jobs(**arguments)
        if isinstance(payload, dict):
            status = payload.get("status")
            if status == "clarification_required":
                raise SourceCollectionError("clarification_required", "RVC просит уточнить поисковый запрос.")
            if status == "error":
                raise SourceCollectionError("upstream_error", "RVC не выполнил поиск.", retryable=True)
            if status != "results" or not isinstance(payload.get("results"), list):
                raise SourceCollectionError("invalid_payload", "RVC вернул неожиданный статус поиска.")
            payload = payload.get("results", [])
        if not isinstance(payload, list):
            raise SourceCollectionError("invalid_payload", "RVC вернул неожиданный формат.")
        records = tuple(self._normalize(source, item) for item in payload[:10] if isinstance(item, dict) and item.get("attributed_url"))
        return Batch(
            records=records,
            coverage=Coverage(truncated=len(payload) > 10, reason="RVC возвращает максимум 10 teaser-карточек; это не полный JD"),
        )

    def get_job(self, source, masked_id):
        masked_id = str(masked_id or "").strip()
        if not masked_id:
            raise SourceCollectionError("invalid_masked_id", "RVC masked_id не задан.")
        if self.client is None:
            raise SourceCollectionError("mcp_client_missing", "RVC MCP-клиент не настроен.")
        payload = self.client.get_job(masked_id=masked_id)
        if not isinstance(payload, dict) or not payload.get("attributed_url"):
            raise SourceCollectionError("invalid_payload", "RVC вернул неожиданный teaser.")
        return self._normalize(source, payload)

    @staticmethod
    def _normalize(source, item):
        attributed_url = item["attributed_url"]
        return {
            "source_slug": source.slug,
            "external_id": str(item.get("masked_id") or ""),
            "canonical_url": attributed_url,
            "apply_url": "",
            "title": item.get("title") or "",
            "company": item.get("company") or "",
            "description": "",
            "description_permission": False,
            "published_at": None,
            "role": item.get("title") or "",
            "industry": "",
            "work_arrangement": item.get("arrangement") or "",
            "country_restrictions": [],
            "timezone_restrictions": [],
            "salary": {"original": item.get("salary_display") or ""},
            "raw_hash": "",
            "adapter_confirmed_permalink": True,
            "attribution": {"label": "RVC teaser", "url": attributed_url, "teaser_only": True},
        }
