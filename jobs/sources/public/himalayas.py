import json
import math

from jobs.sources.core.contracts import Batch, Coverage
from jobs.sources.public.common import normalized_description, parse_datetime, positive


class HimalayasAdapter:
    search_endpoint = "https://himalayas.app/jobs/api/search"
    feed_endpoint = "https://himalayas.app/jobs/api"
    timeout = 15
    default_queries = ("product manager", "project manager", "customer support lead")

    def __init__(self, *, http):
        self.http = http

    def collect(self, source, cursor=None):
        state = self._decode_cursor(cursor)
        if state["phase"] == "feed":
            return self._collect_feed(source, state.get("cursor"))
        return self._collect_search(source, state)

    def _collect_search(self, source, state):
        queries = tuple(source.config.get("queries") or self.default_queries)
        query_index = min(state.get("query", 0), len(queries) - 1)
        page = max(int(state.get("page", 1)), 1)
        payload = self.http.get_json(
            self.search_endpoint,
            params={"q": queries[query_index], "sort": "recent", "page": page},
            timeout=self.timeout,
        )
        jobs = payload.get("jobs") or []
        records = tuple(self._normalize(source, job) for job in jobs)
        total = max(int(payload.get("totalCount") or len(jobs)), 0)
        limit = max(int(payload.get("limit") or len(jobs) or 1), 1)
        pages = max(math.ceil(total / limit), 1)
        if page < pages:
            next_state = {"phase": "search", "query": query_index, "page": page + 1}
        elif query_index + 1 < len(queries):
            next_state = {"phase": "search", "query": query_index + 1, "page": 1}
        else:
            next_state = {"phase": "feed", "cursor": None}
        published = [record["published_at"] for record in records if record["published_at"]]
        return Batch(
            records=records,
            next_cursor=json.dumps(next_state, separators=(",", ":")),
            coverage=Coverage(window_start=min(published) if published else None),
            provider_updated_at=parse_datetime(payload.get("updatedAt")),
        )

    def _collect_feed(self, source, cursor):
        params = {"limit": 20}
        if cursor:
            params["cursor"] = cursor
        payload = self.http.get_json(self.feed_endpoint, params=params, timeout=self.timeout)
        records = tuple(self._normalize(source, job) for job in (payload.get("jobs") or []))
        next_cursor = payload.get("nextCursor")
        encoded = json.dumps({"phase": "feed", "cursor": next_cursor}, separators=(",", ":")) if next_cursor else None
        published = [record["published_at"] for record in records if record["published_at"]]
        return Batch(
            records=records,
            next_cursor=encoded,
            coverage=Coverage(window_start=min(published) if published else None),
            provider_updated_at=parse_datetime(payload.get("updatedAt")),
        )

    @staticmethod
    def _decode_cursor(cursor):
        if not cursor:
            return {"phase": "search", "query": 0, "page": 1}
        try:
            decoded = json.loads(cursor)
        except (TypeError, ValueError):
            return {"phase": "search", "query": 0, "page": 1}
        return decoded if decoded.get("phase") in {"search", "feed"} else {"phase": "search", "query": 0, "page": 1}

    @staticmethod
    def _normalize(source, job):
        description, raw_hash = normalized_description(job.get("description") or job.get("excerpt"))
        timezone_restrictions = job.get("timezoneRestrictions")
        if timezone_restrictions is None:
            timezone_restrictions = job.get("timezoneRestriction") or []
        categories = job.get("categories")
        if categories is None:
            categories = job.get("category") or []
        if isinstance(categories, str):
            categories = [categories]
        locations = job.get("locationRestrictions") or []
        countries = [item.get("name") or item.get("alpha2") for item in locations if isinstance(item, dict)]
        salary = {}
        salary_min = positive(job.get("minSalary"))
        salary_max = positive(job.get("maxSalary"))
        if salary_min is not None:
            salary["min"] = salary_min
        if salary_max is not None:
            salary["max"] = salary_max
        if salary:
            salary.update({
                "currency": job.get("currency") or "",
                "period": job.get("salaryPeriod") or "annual",
                "original": f'{job.get("minSalary", "")}–{job.get("maxSalary", "")} {job.get("currency", "")}'.strip(),
            })
        canonical_url = job.get("url") or job.get("applicationLink")
        return {
            "source_slug": source.slug,
            "external_id": str(job.get("guid") or ""),
            "canonical_url": canonical_url,
            "apply_url": job.get("applicationLink") or canonical_url,
            "title": job.get("title") or "",
            "company": job.get("companyName") or "",
            "description": description,
            "description_permission": False,
            "published_at": parse_datetime(job.get("pubDate")),
            "expires_at": parse_datetime(job.get("expiryDate")),
            "role": job.get("title") or "",
            "industry": ", ".join(str(item) for item in categories),
            "work_arrangement": "remote",
            "country_restrictions": [country for country in countries if country],
            "timezone_restrictions": [str(item) for item in timezone_restrictions],
            "salary": salary,
            "raw_hash": raw_hash,
            "adapter_confirmed_permalink": False,
            "attribution": {"label": "Himalayas", "url": "https://himalayas.app"},
        }
