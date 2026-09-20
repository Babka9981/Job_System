from jobs.sources.core.contracts import Batch, Coverage
from jobs.sources.public.common import normalized_description, parse_datetime, positive


class RemoteOkAdapter:
    endpoint = "https://remoteok.com/api"
    timeout = 15

    def __init__(self, *, http):
        self.http = http

    def collect(self, source, cursor=None):
        payload = self.http.get_json(self.endpoint, params=None, timeout=self.timeout)
        metadata = payload[0] if payload and isinstance(payload[0], dict) and "last_updated" in payload[0] else {}
        jobs = payload[1:] if metadata else payload
        records = tuple(self._normalize(source, job) for job in jobs if isinstance(job, dict) and job.get("url"))
        published = [record["published_at"] for record in records if record["published_at"]]
        return Batch(
            records=records,
            coverage=Coverage(
                window_start=min(published) if published else None,
                truncated=True,
                reason="Одна ограниченная лента Remote OK за цикл",
            ),
            provider_updated_at=parse_datetime(metadata.get("last_updated")),
        )

    def _normalize(self, source, job):
        description, raw_hash = normalized_description(job.get("description"))
        salary_min = positive(job.get("salary_min"))
        salary_max = positive(job.get("salary_max"))
        salary = {}
        if salary_min is not None:
            salary["min"] = salary_min
        if salary_max is not None:
            salary["max"] = salary_max
        if salary:
            salary.update({
                "currency": "USD",
                "period": "year",
                "original": f'{job.get("salary_min", "")}–{job.get("salary_max", "")} USD',
            })
        location = (job.get("location") or "").strip()
        tags = [str(tag) for tag in (job.get("tags") or [])]
        return {
            "source_slug": source.slug if source is not None else "remote-ok",
            "external_id": str(job.get("id") or job.get("slug") or ""),
            "canonical_url": job["url"],
            "apply_url": job.get("apply_url") or job["url"],
            "title": job.get("position") or "",
            "company": job.get("company") or "",
            "description": description,
            "description_permission": False,
            "published_at": parse_datetime(job.get("date") or job.get("epoch")),
            "role": job.get("position") or "",
            "industry": ", ".join(tags),
            "work_arrangement": "remote",
            "country_restrictions": [location] if location else [],
            "timezone_restrictions": [],
            "salary": salary,
            "raw_hash": raw_hash,
            "adapter_confirmed_permalink": True,
            "attribution": {"label": "Remote OK", "url": "https://remoteok.com"},
        }
