from jobs.sources.core.contracts import Batch, Coverage
from jobs.sources.public.common import normalized_description, parse_datetime, positive


class JobicyAdapter:
    endpoint = "https://jobicy.com/api/v2/remote-jobs"
    timeout = 15

    def __init__(self, *, http):
        self.http = http

    def collect(self, source, cursor=None):
        requested = min(max(int(source.config.get("count") or 200), 1), 200)
        params = {"count": requested}
        for key in ("geo", "industry", "tag"):
            if source.config.get(key):
                params[key] = source.config[key]
        payload = self.http.get_json(self.endpoint, params=params, timeout=self.timeout)
        jobs = payload.get("jobs") or []
        records = tuple(self._normalize(source, job) for job in jobs)
        published = [record["published_at"] for record in records if record["published_at"]]
        count = int(payload.get("jobCount") or len(jobs))
        reason = f"Jobicy возвращает не более {requested} последних записей без подтверждённой пагинации"
        return Batch(
            records=records,
            next_cursor=None,
            coverage=Coverage(
                window_start=min(published) if published else None,
                truncated=count >= requested,
                reason=reason if count >= requested else "",
            ),
            provider_updated_at=parse_datetime(payload.get("lastUpdate")),
        )

    @staticmethod
    def _normalize(source, job):
        description, raw_hash = normalized_description(job.get("jobDescription") or job.get("jobExcerpt"))
        industries = job.get("jobIndustry") or []
        if isinstance(industries, str):
            industries = [industries]
        job_types = job.get("jobType") or []
        if isinstance(job_types, str):
            job_types = [job_types]
        salary = {}
        salary_min = positive(job.get("salaryMin"))
        salary_max = positive(job.get("salaryMax"))
        if salary_min is not None:
            salary["min"] = salary_min
        if salary_max is not None:
            salary["max"] = salary_max
        if salary:
            salary.update({
                "currency": job.get("salaryCurrency") or "",
                "period": job.get("salaryPeriod") or "",
                "original": f'{job.get("salaryMin", "")}–{job.get("salaryMax", "")} {job.get("salaryCurrency", "")}'.strip(),
            })
        geo = (job.get("jobGeo") or "").strip()
        url = job.get("url") or ""
        return {
            "source_slug": source.slug,
            "external_id": str(job.get("id") or job.get("jobSlug") or ""),
            "canonical_url": url,
            "apply_url": url,
            "title": job.get("jobTitle") or "",
            "company": job.get("companyName") or "",
            "description": description,
            "description_permission": False,
            "published_at": parse_datetime(job.get("pubDate")),
            "role": job.get("jobTitle") or "",
            "industry": ", ".join(str(item) for item in industries),
            "work_arrangement": "remote " + ", ".join(str(item) for item in job_types),
            "country_restrictions": [geo] if geo else [],
            "timezone_restrictions": [],
            "salary": salary,
            "raw_hash": raw_hash,
            "adapter_confirmed_permalink": True,
            "attribution": {"label": "Jobicy", "url": "https://jobicy.com"},
        }
