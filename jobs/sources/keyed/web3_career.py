import os

from jobs.sources.core.contracts import Batch, Coverage, SourceCollectionError
from jobs.sources.keyed.common import bounded_int, required_secret
from jobs.sources.keyed.durable import literal_true
from jobs.sources.keyed.http import KeyedJsonHttpClient
from jobs.sources.public.common import normalized_description, parse_datetime, positive


class Web3CareerAdapter:
    endpoint = "https://web3.career/api/v1"
    timeout = 15

    def __init__(self, *, http=None, environ=None):
        self.http = http or KeyedJsonHttpClient()
        self.environ = environ

    @staticmethod
    def live_check_status():
        return "credential-gated"

    def collect(self, source, cursor=None):
        token = required_secret("WEB3_CAREER_API_TOKEN", self.environ)
        config = source.config if isinstance(source.config, dict) else {}
        queries = config.get("role_queries") or ["product-manager"]
        if not isinstance(queries, list) or not queries or not all(isinstance(item, str) and item.strip() for item in queries):
            raise SourceCollectionError("invalid_source_config", "Запросы Web3.career настроены неверно.")
        try:
            index = int(cursor or 0)
        except (TypeError, ValueError):
            raise SourceCollectionError("invalid_cursor", "Позиция Web3.career повреждена.") from None
        if index < 0 or index >= len(queries):
            raise SourceCollectionError("invalid_cursor", "Позиция Web3.career вышла за пределы запросов.")
        params = {
            "token": token,
            "tag": queries[index].strip(),
            "remote": "true",
            "limit": bounded_int(config.get("limit"), default=100, minimum=1, maximum=100),
        }
        payload = self.http.get_json(self.endpoint, params=params, timeout=self.timeout)
        if isinstance(payload, dict):
            jobs = payload.get("jobs", [])
        elif isinstance(payload, list):
            jobs = next((item for item in payload if isinstance(item, list)), None)
            if jobs is None and all(isinstance(item, dict) for item in payload):
                jobs = payload
        else:
            jobs = None
        if not isinstance(jobs, list):
            raise SourceCollectionError("invalid_payload", "Web3.career вернул неожиданный формат.")
        environment = os.environ if self.environ is None else self.environ
        permission_confirmed = literal_true(environment.get("WEB3_CAREER_FULL_DESCRIPTION_CONFIRMED"))
        records = tuple(
            self._normalize(source, job, permission_confirmed=permission_confirmed)
            for job in jobs if isinstance(job, dict) and job.get("apply_url")
        )
        published = [item["published_at"] for item in records if item["published_at"]]
        next_cursor = str(index + 1) if index + 1 < len(queries) else None
        return Batch(
            records=records,
            next_cursor=next_cursor,
            coverage=Coverage(
                window_start=min(published) if published else None,
                truncated=len(jobs) >= params["limit"],
                reason="Web3.career не предоставляет cursor/offset; выдача ограничена 100" if len(jobs) >= params["limit"] else "",
            ),
        )

    @staticmethod
    def _normalize(source, job, *, permission_confirmed=False):
        description, raw_hash = normalized_description(job.get("description") or job.get("job_description"))
        salary_min = positive(job.get("salary_min"))
        salary_max = positive(job.get("salary_max"))
        salary = {key: value for key, value in (("min", salary_min), ("max", salary_max)) if value is not None}
        if salary:
            salary.update({"currency": job.get("salary_currency") or "USD", "period": job.get("salary_period") or "year"})
        apply_url = job["apply_url"]
        return {
            "source_slug": source.slug,
            "external_id": str(job.get("id") or job.get("slug") or ""),
            "canonical_url": apply_url,
            "apply_url": apply_url,
            "title": job.get("title") or job.get("position") or "",
            "company": job.get("company") or job.get("company_name") or "",
            "description": description,
            "description_permission": bool(description) and permission_confirmed is True,
            "published_at": parse_datetime(job.get("date") or job.get("published_at")),
            "role": job.get("title") or job.get("position") or "",
            "industry": ", ".join(str(tag) for tag in (job.get("tags") or [])),
            "work_arrangement": "remote" if job.get("remote") is not False else "",
            "country_restrictions": [job["location"]] if job.get("location") else [],
            "timezone_restrictions": [],
            "salary": salary,
            "raw_hash": raw_hash,
            "adapter_confirmed_permalink": True,
            "attribution": {
                "label": "Web3.career",
                "url": apply_url,
                "follow": True,
                "full_description_contract_confirmed": permission_confirmed is True,
            },
        }
