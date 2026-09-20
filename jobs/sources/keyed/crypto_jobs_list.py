from django.utils import timezone

from jobs.sources.core.contracts import Batch, Coverage, SourceCollectionError
from jobs.sources.keyed.common import bounded_int, required_secret
from jobs.sources.keyed.durable import DurableUtcCounter, literal_true
from jobs.sources.keyed.http import KeyedJsonHttpClient
from jobs.sources.public.common import normalized_description, parse_datetime


class CryptoJobsListAdapter:
    endpoint = "https://api.cryptojobslist.com/public/jobs"
    timeout = 15
    recommended_schedule_hours = (9, 13, 17, 21)

    def __init__(self, *, http=None, environ=None, clock=timezone.now, cycle_counter=None):
        self.http = http or KeyedJsonHttpClient()
        self.environ = environ
        self.clock = clock
        self.cycle_counter = cycle_counter

    @staticmethod
    def live_check_status():
        return "credential-gated"

    def collect(self, source, cursor=None):
        api_key = required_secret("CRYPTOJOBS_LIST_API_KEY", self.environ)
        if cursor is None:
            counter = self.cycle_counter or DurableUtcCounter(
                f"crypto-jobs-list-{source.owner_id}-{source.slug}", clock=self.clock
            )
            counter.increment(
                "cycles", 1, 4, "daily_cycle_limit",
                "CryptoJobsList уже запускался четыре раза за текущие сутки UTC.",
            )
        config = source.config if isinstance(source.config, dict) else {}
        if cursor is None:
            page = 1
        else:
            try:
                page = int(cursor)
            except (TypeError, ValueError, OverflowError):
                raise SourceCollectionError("invalid_cursor", "Позиция CryptoJobsList повреждена.") from None
            if page < 2 or page > 10_000:
                raise SourceCollectionError("invalid_cursor", "Позиция CryptoJobsList вышла за пределы страниц.")
        params = {"page": page, "limit": bounded_int(config.get("limit"), default=100, minimum=1, maximum=100)}
        for key in ("query", "tags", "location", "publishedAt"):
            if config.get(key):
                params[key] = config[key]
        if config.get("remote") is not None:
            params["remote"] = str(config["remote"]).lower()
        payload = self.http.get_json(self.endpoint, params=params, headers={"x-api-key": api_key}, timeout=self.timeout)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list) or not isinstance(payload.get("meta"), dict):
            raise SourceCollectionError("invalid_payload", "CryptoJobsList вернул неожиданный формат.")
        meta = payload["meta"]
        current = bounded_int(meta.get("page"), default=page, minimum=1, maximum=10_000)
        total = bounded_int(meta.get("totalPages"), default=current, minimum=current, maximum=10_000)
        permission_confirmed = literal_true(
            (self.environ if self.environ is not None else __import__("os").environ).get(
                "CRYPTOJOBS_LIST_FULL_DESCRIPTION_CONFIRMED"
            )
        )
        records = tuple(
            self._normalize(source, job, permission_confirmed=permission_confirmed)
            for job in payload["jobs"] if isinstance(job, dict) and job.get("canonicalURL")
        )
        published = [item["published_at"] for item in records if item["published_at"]]
        return Batch(
            records=records,
            next_cursor=str(current + 1) if current < total else None,
            coverage=Coverage(window_start=min(published) if published else None, truncated=current < total),
        )

    @staticmethod
    def _normalize(source, job, *, permission_confirmed=False):
        description_value = job.get("description") or job.get("jobDescription") or job.get("jobDescriptionHtml") or ""
        description, raw_hash = normalized_description(description_value)
        canonical_url = job["canonicalURL"]
        return {
            "source_slug": source.slug,
            "external_id": str(job.get("id") or job.get("slug") or ""),
            "canonical_url": canonical_url,
            "apply_url": canonical_url,
            "title": job.get("jobTitle") or "",
            "company": job.get("companyName") or "",
            "description": description,
            "description_permission": bool(description) and permission_confirmed is True,
            "published_at": parse_datetime(job.get("publishedAt")),
            "role": job.get("jobTitle") or "",
            "industry": ", ".join(str(tag) for tag in (job.get("tags") or [])),
            "work_arrangement": "remote" if job.get("remote") is True else "",
            "country_restrictions": [job["jobLocation"]] if job.get("jobLocation") else [],
            "timezone_restrictions": [],
            "salary": {},
            "raw_hash": raw_hash,
            "adapter_confirmed_permalink": True,
            "attribution": {
                "label": "CryptoJobsList",
                "url": canonical_url,
                "canonical": True,
                "full_description_contract_confirmed": permission_confirmed is True,
            },
        }
