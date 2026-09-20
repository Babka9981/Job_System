import hashlib
import os
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from urllib.error import HTTPError
from unittest.mock import patch

from jobs.models.models import Lease, Source, SourceRecord, Vacancy
from jobs.sources.core.collector import CollectorBusy, collect_sources, default_adapters
from jobs.sources.core.contracts import Batch, Coverage, SourceCollectionError
from jobs.sources.core.registry import seed_sources
from jobs.sources.keyed.remote_rocketship import TemporaryRocketshipStore
from jobs.sources.public.remote_ok import RemoteOkAdapter
from jobs.sources.public.himalayas import HimalayasAdapter
from jobs.sources.public.jobicy import JobicyAdapter
from jobs.sources.public.http import JsonHttpClient


class FakeHttp:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get_json(self, url, *, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return self.payloads.pop(0)


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body.encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


def normalized_record(source_slug, external_id):
    return {
        "source_slug": source_slug,
        "external_id": external_id,
        "canonical_url": f"https://jobs.example.test/{source_slug}/{external_id}",
        "title": f"Role {external_id}",
        "company": "Example",
        "description": f"Description {external_id}",
        "description_permission": False,
        "salary": {},
        "raw_hash": f"hash-{source_slug}-{external_id}",
        "attribution": {"label": source_slug, "url": "https://jobs.example.test"},
    }


class SourceRegistryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="password")

    def test_seed_creates_exact_catalog_and_is_idempotent(self):
        first = seed_sources(self.owner)
        existing = Source.objects.get(owner=self.owner, slug="jobicy")
        existing.status = Source.Status.LIMITED
        existing.enabled = False
        existing.config = {**existing.config, "last_error": {"code": "rate_limit", "message": "Retry later"}}
        existing.save()
        second = seed_sources(self.owner)

        self.assertEqual((first.created, first.updated), (50, 0))
        self.assertEqual((second.created, second.updated), (0, 50))
        self.assertEqual(Source.objects.filter(owner=self.owner, kind="site").count(), 9)
        self.assertEqual(Source.objects.filter(owner=self.owner, kind="telegram").count(), 41)
        self.assertEqual(Source.objects.filter(owner=self.owner).count(), 50)
        existing.refresh_from_db()
        self.assertEqual(existing.status, Source.Status.LIMITED)
        self.assertFalse(existing.enabled)
        self.assertEqual(existing.config["last_error"]["code"], "rate_limit")
        rocketship = Source.objects.get(owner=self.owner, slug="remote-rocketship")
        self.assertEqual(rocketship.config["service_interval_seconds"], 14400)

    def test_default_adapters_expose_keyed_sources_and_inject_rvc_tool_boundary(self):
        calls = []

        def call_tool(name, arguments):
            calls.append((name, arguments))
            return {"status": "results", "results": []}

        adapters = default_adapters(rvc_call_tool=call_tool)

        self.assertTrue({"web3_career", "crypto_jobs_list", "remote_rocketship", "rvc"} <= set(adapters))
        source = Source(
            owner=self.owner,
            slug="rvc",
            config={"query": "product", "conversation_language_code": "ru", "target_country_codes": []},
        )
        self.assertEqual(adapters["rvc"].collect(source).records, ())
        self.assertEqual(calls[0][0], "rvc_search_jobs")
        self.assertIsNotNone(default_adapters()["rvc"].client)

    def test_env_example_documents_exact_keyed_and_optional_ai_names(self):
        values = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
        for expected in (
            "WEB3_CAREER_API_TOKEN=",
            "WEB3_CAREER_FULL_DESCRIPTION_CONFIRMED=false",
            "CRYPTOJOBS_LIST_API_KEY=",
            "CRYPTOJOBS_LIST_FULL_DESCRIPTION_CONFIRMED=false",
            "REMOTE_ROCKETSHIP_API_KEY=",
            "REMOTE_ROCKETSHIP_ENABLED=false",
            "REMOTE_ROCKETSHIP_ACTIVE_PLAN_CONFIRMED=false",
            "OPENAI_API_KEY=",
            "OPENAI_MODEL=",
            "OPENAI_PRICES_JSON=",
        ):
            self.assertIn(expected, values)
        self.assertNotIn("CRYPTOJOBS_LIST_DESCRIPTION_CONFIRMED=", values)
        self.assertNotIn("REMOTE_ROCKETSHIP_ACCESS_CONFIRMED=", values)


class PublicAdapterTests(TestCase):
    def test_http_client_retries_transient_failures_with_bounded_backoff(self):
        attempts = []
        responses = [OSError("temporary"), FakeResponse('{"ok": true}')]

        def opener(request, timeout):
            attempts.append((request.full_url, timeout))
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        sleeps = []
        client = JsonHttpClient(opener=opener, sleeper=sleeps.append, max_attempts=3)

        payload = client.get_json("https://api.example.test/jobs", params={"q": "product role"}, timeout=9)

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(len(attempts), 2)
        self.assertIn("q=product+role", attempts[0][0])
        self.assertEqual(sleeps, [1])

    def test_http_client_honors_public_api_rate_limit_backoff(self):
        responses = [
            HTTPError("https://api.example.test/jobs", 429, "rate limited", None, None),
            FakeResponse('{"ok": true}'),
        ]

        def opener(request, timeout):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        sleeps = []
        client = JsonHttpClient(opener=opener, sleeper=sleeps.append, max_attempts=2)

        self.assertEqual(client.get_json("https://api.example.test/jobs", timeout=9), {"ok": True})
        self.assertEqual(sleeps, [60])

    def test_remote_ok_skips_metadata_and_preserves_exact_attribution(self):
        http = FakeHttp([[
            {"last_updated": 1760000000, "legal": "Link back"},
            {
                "id": "42", "position": "Product Lead", "company": "Acme",
                "description": "<p>Own product</p>", "date": "2026-09-18T10:30:00+00:00",
                "location": "Worldwide", "tags": ["product", "fintech"],
                "salary_min": 0, "salary_max": 90000,
                "url": "https://remoteok.com/remote-jobs/42",
                "apply_url": "https://remoteok.com/l/42",
            },
        ]])

        batch = RemoteOkAdapter(http=http).collect(None, "ignored")

        self.assertEqual(http.calls, [("https://remoteok.com/api", None, 15)])
        self.assertEqual(len(batch.records), 1)
        record = batch.records[0]
        self.assertEqual(record["external_id"], "42")
        self.assertEqual(record["salary"], {"max": 90000, "currency": "USD", "period": "year", "original": "0–90000 USD"})
        self.assertEqual(record["attribution"], {"label": "Remote OK", "url": "https://remoteok.com"})
        self.assertEqual(record["description"], "Own product")
        self.assertEqual(record["raw_hash"], hashlib.sha256("Own product".encode()).hexdigest())
        self.assertTrue(record["adapter_confirmed_permalink"])
        self.assertFalse(record["description_permission"])
        self.assertTrue(batch.coverage.truncated)
        self.assertEqual(batch.coverage.reason, "Одна ограниченная лента Remote OK за цикл")

    def test_himalayas_search_is_recent_paginated_and_accepts_plural_fields(self):
        http = FakeHttp([{
            "jobs": [{
                "guid": "h-1", "title": "Product Manager", "companyName": "Himalaya Co",
                "applicationLink": "https://company.example/jobs/1", "description": "<p>Lead product</p>",
                "pubDate": 1789700000000, "expiryDate": 1792300000000,
                "locationRestrictions": [{"alpha2": "DE", "name": "Germany"}],
                "timezoneRestrictions": ["UTC+1", "UTC+3"], "categories": ["Product", "Fintech"],
                "minSalary": 5000, "maxSalary": 7000, "currency": "USD", "salaryPeriod": "monthly",
            }],
            "totalCount": 25, "limit": 20, "page": 1, "updatedAt": 1789700100000,
        }])
        source = type("SourceFixture", (), {"slug": "himalayas", "config": {"queries": ["product manager"]}})()

        batch = HimalayasAdapter(http=http).collect(source, None)

        self.assertEqual(http.calls[0], (
            "https://himalayas.app/jobs/api/search",
            {"q": "product manager", "sort": "recent", "page": 1},
            15,
        ))
        self.assertIn('"page":2', batch.next_cursor)
        record = batch.records[0]
        self.assertEqual(record["timezone_restrictions"], ["UTC+1", "UTC+3"])
        self.assertEqual(record["industry"], "Product, Fintech")
        self.assertEqual(record["country_restrictions"], ["Germany"])
        self.assertEqual(record["salary"]["period"], "monthly")
        self.assertEqual(record["canonical_url"], "https://company.example/jobs/1")
        self.assertFalse(record["adapter_confirmed_permalink"])
        self.assertEqual(record["description"], "Lead product")
        self.assertEqual(record["raw_hash"], hashlib.sha256("Lead product".encode()).hexdigest())
        self.assertEqual(record["attribution"]["label"], "Himalayas")

    def test_jobicy_caps_count_and_reports_non_paginated_window(self):
        http = FakeHttp([{
            "jobCount": 200, "lastUpdate": "2026-09-20T08:00:00+00:00",
            "jobs": [{
                "id": 77, "url": "https://jobicy.com/jobs/product-owner-77",
                "jobTitle": "Product Owner", "companyName": "Example",
                "jobDescription": "<p>Payments product</p>", "jobGeo": "Europe",
                "jobIndustry": ["Product & Operations"], "jobType": ["full-time"],
                "pubDate": "2026-09-19T08:00:00+00:00", "salaryMin": 60000,
                "salaryMax": 80000, "salaryCurrency": "EUR", "salaryPeriod": "yearly",
            }],
        }])
        source = type("SourceFixture", (), {
            "slug": "jobicy", "config": {"count": 999, "tag": "product", "industry": "product-operations"},
        })()

        batch = JobicyAdapter(http=http).collect(source, "ignored")

        self.assertEqual(http.calls[0], (
            "https://jobicy.com/api/v2/remote-jobs",
            {"count": 200, "industry": "product-operations", "tag": "product"},
            15,
        ))
        self.assertIsNone(batch.next_cursor)
        self.assertTrue(batch.coverage.truncated)
        self.assertIn("200", batch.coverage.reason)
        record = batch.records[0]
        self.assertEqual(record["external_id"], "77")
        self.assertEqual(record["industry"], "Product & Operations")
        self.assertEqual(record["country_restrictions"], ["Europe"])
        self.assertEqual(record["attribution"], {"label": "Jobicy", "url": "https://jobicy.com"})
        self.assertEqual(record["description"], "Payments product")
        self.assertEqual(record["raw_hash"], hashlib.sha256("Payments product".encode()).hexdigest())


class CollectorTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="password")

    def test_checkpoint_resume_and_source_errors_are_isolated(self):
        partial = Source.objects.create(owner=self.owner, slug="partial", name="Partial", kind="site", adapter="partial")
        healthy = Source.objects.create(owner=self.owner, slug="healthy", name="Healthy", kind="site", adapter="healthy")

        class PartialAdapter:
            def collect(self, source, cursor):
                if not cursor:
                    return Batch((normalized_record(source.slug, "one"),), "page-2", Coverage())
                raise SourceCollectionError("temporary", "Временный сбой", retryable=True)

        class HealthyAdapter:
            def collect(self, source, cursor):
                return Batch((normalized_record(source.slug, "ok"),), None, Coverage())

        first = collect_sources(
            self.owner,
            adapters={"partial": PartialAdapter(), "healthy": HealthyAdapter()},
            holder="run-1",
            now=timezone.now(),
        )

        partial.refresh_from_db()
        healthy.refresh_from_db()
        self.assertEqual(partial.cursor, "page-2")
        self.assertEqual(partial.status, Source.Status.ERROR)
        self.assertEqual(healthy.status, Source.Status.READY)
        self.assertEqual(first.succeeded, 1)
        self.assertEqual(first.failed, 1)
        self.assertEqual(Vacancy.objects.count(), 2)

        class ResumeAdapter:
            def collect(self, source, cursor):
                self.cursor = cursor
                return Batch((normalized_record(source.slug, "two"),), None, Coverage())

        resume = ResumeAdapter()
        second = collect_sources(
            self.owner,
            adapters={"partial": resume},
            holder="run-2",
            now=timezone.now(),
            source_slugs=["partial"],
        )

        partial.refresh_from_db()
        self.assertEqual(resume.cursor, "page-2")
        self.assertEqual(partial.cursor, "")
        self.assertEqual(partial.status, Source.Status.READY)
        self.assertEqual(second.records, 1)
        self.assertEqual(Vacancy.objects.count(), 3)

    def test_needs_access_adapter_failure_is_recorded_and_does_not_block_public_source(self):
        restricted = Source.objects.create(
            owner=self.owner, slug="restricted", name="Restricted", kind="site",
            adapter="restricted", status=Source.Status.NEEDS_ACCESS,
        )
        public = Source.objects.create(
            owner=self.owner, slug="public", name="Public", kind="site", adapter="public",
        )

        class MissingCredentials:
            def collect(self, source, cursor):
                raise SourceCollectionError("credentials_missing", "Доступ к источнику не настроен.")

        class Healthy:
            def collect(self, source, cursor):
                return Batch((normalized_record(source.slug, "one"),), None, Coverage())

        report = collect_sources(
            self.owner,
            adapters={"restricted": MissingCredentials(), "public": Healthy()},
            holder="needs-access-check",
        )

        restricted.refresh_from_db()
        public.refresh_from_db()
        self.assertEqual((report.failed, report.succeeded, report.skipped), (1, 1, 0))
        self.assertEqual(restricted.config["last_error"]["code"], "credentials_missing")
        self.assertEqual(public.status, Source.Status.READY)

    @patch.dict(os.environ, {}, clear=True)
    def test_default_rocketship_missing_activation_is_safe_and_public_source_continues(self):
        rocketship = Source.objects.create(
            owner=self.owner, slug="remote-rocketship", name="Rocketship", kind="site",
            adapter="remote_rocketship", status=Source.Status.NEEDS_ACCESS,
        )
        public = Source.objects.create(
            owner=self.owner, slug="public", name="Public", kind="site", adapter="public-fixture",
        )

        class Healthy:
            def collect(self, source, cursor):
                return Batch((normalized_record(source.slug, "one"),), None, Coverage())

        adapters = default_adapters()
        adapters["public-fixture"] = Healthy()
        report = collect_sources(self.owner, adapters=adapters, holder="default-rocketship-check")

        rocketship.refresh_from_db()
        public.refresh_from_db()
        self.assertEqual((report.failed, report.succeeded), (1, 1))
        self.assertEqual(rocketship.config["last_error"]["code"], "source_not_activated")
        self.assertEqual(public.status, Source.Status.READY)

    def test_rocketship_collection_uses_temporary_store_and_never_generic_upsert(self):
        temporary = Source.objects.create(
            owner=self.owner, slug="remote-rocketship", name="Rocketship", kind="site",
            adapter="remote_rocketship", status=Source.Status.NEEDS_ACCESS,
        )
        now = timezone.now()

        class Rocketship:
            def collect(self, source, cursor):
                record = normalized_record(source.slug, "temporary")
                record.update({
                    "expires_at": now + timezone.timedelta(hours=1),
                    "_temporary_payload": {"raw": {"id": 1}, "normalized": {}, "derived": {}, "cache": {}},
                })
                return Batch((record,), None, Coverage())

        with tempfile.TemporaryDirectory(prefix="job-t05-collector-") as directory:
            with override_settings(TEMPORARY_ROOT=Path(directory)):
                report = collect_sources(
                    self.owner,
                    adapters={"remote_rocketship": Rocketship()},
                    holder="rocketship-temp-check",
                    now=now,
                    clock=lambda: now,
                )
                handles = TemporaryRocketshipStore(clock=lambda: now).list(self.owner, temporary)

        temporary.refresh_from_db()
        self.assertEqual((report.succeeded, report.records), (1, 1))
        self.assertEqual(len(handles), 1)
        self.assertEqual(temporary.status, Source.Status.READY)
        self.assertFalse(SourceRecord.objects.exists())
        self.assertFalse(Vacancy.objects.exists())

    def test_rocketship_lost_lease_before_temp_write_leaves_no_cards(self):
        source = Source.objects.create(
            owner=self.owner, slug="remote-rocketship", name="Rocketship", kind="site",
            adapter="remote_rocketship", status=Source.Status.NEEDS_ACCESS,
        )
        now = timezone.now()

        class LeaseStealingAdapter:
            def collect(self, source, cursor):
                Lease.objects.filter(name=f"source-collector:{source.owner_id}").update(
                    holder="new-holder", expires_at=now + timezone.timedelta(minutes=10)
                )
                record = normalized_record(source.slug, "must-not-write")
                record.update({
                    "expires_at": now + timezone.timedelta(hours=1),
                    "_temporary_payload": {"raw": {"id": 1}, "normalized": {}, "derived": {}, "cache": {}},
                })
                return Batch((record,), None, Coverage())

        with tempfile.TemporaryDirectory(prefix="job-t05-before-store-") as directory:
            with override_settings(TEMPORARY_ROOT=Path(directory)):
                store = TemporaryRocketshipStore(clock=lambda: now)
                with self.assertRaises(CollectorBusy):
                    collect_sources(
                        self.owner,
                        adapters={"remote_rocketship": LeaseStealingAdapter()},
                        holder="old-holder",
                        now=now,
                        clock=lambda: now,
                        temporary_store=store,
                    )
                self.assertEqual(store.list(self.owner, source), ())

    def test_rocketship_stolen_lease_after_store_rolls_back_new_cards(self):
        source = Source.objects.create(
            owner=self.owner, slug="remote-rocketship", name="Rocketship", kind="site",
            adapter="remote_rocketship", status=Source.Status.NEEDS_ACCESS,
        )
        now = timezone.now()

        class Adapter:
            def collect(self, source, cursor):
                record = normalized_record(source.slug, "new")
                record.update({
                    "expires_at": now + timezone.timedelta(hours=1),
                    "_temporary_payload": {"raw": {"id": 2}, "normalized": {}, "derived": {}, "cache": {}},
                })
                return Batch((record,), None, Coverage())

        class StealingStore(TemporaryRocketshipStore):
            def _steal(self, source):
                Lease.objects.filter(name=f"source-collector:{source.owner_id}").update(
                    holder="new-holder", expires_at=now + timezone.timedelta(minutes=10)
                )

            def store_batch(self, owner, source, batch):
                handles = super().store_batch(owner, source, batch)
                self._steal(source)
                return handles

            def store_page(self, owner, source, batch, *, page_key):
                receipt = super().store_page(owner, source, batch, page_key=page_key)
                self._steal(source)
                return receipt

        with tempfile.TemporaryDirectory(prefix="job-t05-after-store-") as directory:
            with override_settings(TEMPORARY_ROOT=Path(directory)):
                store = StealingStore(clock=lambda: now)
                with self.assertRaises(CollectorBusy):
                    collect_sources(
                        self.owner,
                        adapters={"remote_rocketship": Adapter()},
                        holder="old-holder",
                        now=now,
                        clock=lambda: now,
                        temporary_store=store,
                    )
                self.assertEqual(store.list(self.owner, source), ())

    def test_rocketship_lease_expiring_during_store_rolls_back_before_checkpoint(self):
        source = Source.objects.create(
            owner=self.owner, slug="remote-rocketship", name="Rocketship", kind="site",
            adapter="remote_rocketship", status=Source.Status.NEEDS_ACCESS,
        )
        now = timezone.now()

        class Adapter:
            def collect(self, source, cursor):
                record = normalized_record(source.slug, "expires-during-write")
                record.update({
                    "expires_at": now + timezone.timedelta(hours=1),
                    "_temporary_payload": {"raw": {"id": 3}, "normalized": {}, "derived": {}, "cache": {}},
                })
                return Batch((record,), None, Coverage())

        moments = iter((now, now + timezone.timedelta(seconds=301)))
        with tempfile.TemporaryDirectory(prefix="job-t05-expired-store-") as directory:
            with override_settings(TEMPORARY_ROOT=Path(directory)):
                store = TemporaryRocketshipStore(clock=lambda: now)
                with self.assertRaises(CollectorBusy):
                    collect_sources(
                        self.owner,
                        adapters={"remote_rocketship": Adapter()},
                        holder="expiring-holder",
                        now=now,
                        clock=lambda: next(moments),
                        temporary_store=store,
                    )
                self.assertEqual(store.list(self.owner, source), ())
                source.refresh_from_db()
                self.assertEqual(source.cursor, "")

    def test_rocketship_checkpoint_failure_rolls_back_only_new_cards_and_retry_is_idempotent(self):
        source = Source.objects.create(
            owner=self.owner, slug="remote-rocketship", name="Rocketship", kind="site",
            adapter="remote_rocketship", status=Source.Status.NEEDS_ACCESS,
        )
        now = timezone.now()

        def batch(external_id):
            record = normalized_record(source.slug, external_id)
            record.update({
                "expires_at": now + timezone.timedelta(hours=1),
                "_temporary_payload": {
                    "raw": {"id": external_id}, "normalized": {}, "derived": {}, "cache": {},
                },
            })
            return Batch((record,), None, Coverage())

        class Adapter:
            def collect(self, source, cursor):
                return batch("retry-page")

        with tempfile.TemporaryDirectory(prefix="job-t05-checkpoint-") as directory:
            with override_settings(
                TEMPORARY_ROOT=Path(directory),
                PRIVATE_ROOT=Path(directory) / "private",
            ):
                store = TemporaryRocketshipStore(clock=lambda: now)
                existing = store.store_batch(self.owner, source, batch("existing"))[0]
                store.save_user_state(self.owner, existing, status="saved", note="keep me")
                with patch("jobs.sources.core.collector._checkpoint", side_effect=RuntimeError("db write failed")):
                    failed = collect_sources(
                        self.owner,
                        adapters={"remote_rocketship": Adapter()},
                        holder="checkpoint-failure",
                        now=now,
                        clock=lambda: now,
                        temporary_store=store,
                    )
                self.assertEqual(failed.failed, 1)
                self.assertEqual(store.list(self.owner, source), (existing,))
                self.assertEqual(
                    store.read_user_state(self.owner, existing),
                    {"status": "saved", "note": "keep me"},
                )

                retried = collect_sources(
                    self.owner,
                    adapters={"remote_rocketship": Adapter()},
                    holder="checkpoint-retry",
                    now=now,
                    clock=lambda: now,
                    temporary_store=store,
                )
                handles = store.list(self.owner, source)

        self.assertEqual((retried.succeeded, retried.records), (1, 1))
        self.assertEqual(len(handles), 2)
        self.assertEqual(len(set(handles)), 2)

    def test_unexpired_lease_blocks_even_if_holder_token_is_reused(self):
        now = timezone.now()
        Lease.objects.create(
            name=f"source-collector:{self.owner.pk}",
            holder="reused-token",
            expires_at=now + timezone.timedelta(minutes=2),
        )

        with self.assertRaises(CollectorBusy):
            collect_sources(self.owner, adapters={}, holder="reused-token", now=now)

    def test_atomic_initial_lease_collision_is_reported_as_busy(self):
        with patch("jobs.sources.core.collector.Lease.objects.create", side_effect=IntegrityError("race")):
            with self.assertRaises(CollectorBusy):
                collect_sources(self.owner, adapters={}, holder="loser", now=timezone.now())

    def test_expired_takeover_fences_old_holder_before_page_write(self):
        source = Source.objects.create(owner=self.owner, slug="fenced", name="Fenced", kind="site", adapter="fenced")
        now = timezone.now()

        class TakeoverAdapter:
            def collect(self, source, cursor):
                Lease.objects.filter(name=f"source-collector:{source.owner_id}").update(
                    holder="new-run", expires_at=now + timezone.timedelta(minutes=10)
                )
                raise SourceCollectionError("late_failure", "Старый процесс завершился ошибкой")

        with self.assertRaises(CollectorBusy):
            collect_sources(self.owner, adapters={"fenced": TakeoverAdapter()}, holder="old-run", now=now)

        self.assertFalse(Vacancy.objects.exists())
        source.refresh_from_db()
        self.assertEqual(source.status, Source.Status.NOT_CONFIGURED)
        self.assertEqual(Lease.objects.get(name=f"source-collector:{self.owner.pk}").holder, "new-run")

    def test_lease_is_renewed_between_long_pages(self):
        source = Source.objects.create(owner=self.owner, slug="long", name="Long", kind="site", adapter="long")
        start = timezone.now()
        moments = iter((start, start + timezone.timedelta(minutes=4), start + timezone.timedelta(minutes=8)))

        class LongAdapter:
            def __init__(self):
                self.calls = 0
                self.expiry_before_second = None

            def collect(self, source, cursor):
                self.calls += 1
                if self.calls == 2:
                    self.expiry_before_second = Lease.objects.get(name=f"source-collector:{source.owner_id}").expires_at
                return Batch(
                    (normalized_record(source.slug, str(self.calls)),),
                    "page-2" if self.calls == 1 else None,
                    Coverage(),
                )

        adapter = LongAdapter()
        report = collect_sources(
            self.owner,
            adapters={"long": adapter},
            holder="long-run",
            clock=lambda: next(moments),
        )

        self.assertEqual(report.records, 2)
        self.assertEqual(adapter.expiry_before_second, start + timezone.timedelta(minutes=9))

    def test_collector_binds_record_to_current_source_and_drops_wrong_source_evidence(self):
        actual = Source.objects.create(owner=self.owner, slug="actual", name="Actual", kind="site", adapter="fixture")
        Source.objects.create(owner=self.owner, slug="claimed", name="Claimed", kind="site", adapter="unused")

        class WrongSourceAdapter:
            def collect(self, source, cursor):
                record = normalized_record("claimed", "spoofed")
                record.update({"description_permission": True, "adapter_confirmed_permalink": True})
                return Batch((record,), None, Coverage())

        collect_sources(self.owner, adapters={"fixture": WrongSourceAdapter()}, holder="binding-check")

        stored = SourceRecord.objects.get()
        self.assertEqual(stored.source, actual)
        self.assertFalse(stored.description_permission)
        self.assertFalse(stored.adapter_confirmed_permalink)

    def test_invalid_interval_config_is_isolated_to_its_source(self):
        invalid = Source.objects.create(
            owner=self.owner, slug="invalid", name="Invalid", kind="site", adapter="fixture",
            config={"service_interval_seconds": "hourly"},
        )
        malformed = Source.objects.create(
            owner=self.owner, slug="malformed", name="Malformed", kind="site", adapter="fixture",
            config=["not", "a", "mapping"],
        )
        Source.objects.create(owner=self.owner, slug="healthy-config", name="Healthy", kind="site", adapter="fixture")

        class Adapter:
            def collect(self, source, cursor):
                return Batch((normalized_record(source.slug, "one"),), None, Coverage())

        report = collect_sources(self.owner, adapters={"fixture": Adapter()}, holder="config-check")

        invalid.refresh_from_db()
        malformed.refresh_from_db()
        self.assertEqual((report.failed, report.succeeded), (2, 1))
        self.assertEqual(invalid.status, Source.Status.ERROR)
        self.assertEqual(invalid.config["last_error"]["code"], "invalid_source_config")
        self.assertEqual(malformed.status, Source.Status.ERROR)
        self.assertEqual(malformed.config["last_error"]["code"], "invalid_source_config")
        self.assertEqual(Vacancy.objects.count(), 1)

    def test_initial_window_keeps_unknown_dates_and_skips_known_older_than_seven_days(self):
        source = Source.objects.create(owner=self.owner, slug="window", name="Window", kind="site", adapter="window")
        now = timezone.now()

        class WindowAdapter:
            def collect(self, source, cursor):
                old = normalized_record(source.slug, "old")
                old["published_at"] = (now - timezone.timedelta(days=8)).replace(tzinfo=None).isoformat()
                recent = normalized_record(source.slug, "recent")
                recent["published_at"] = now - timezone.timedelta(days=2)
                unknown = normalized_record(source.slug, "unknown")
                unknown["published_at"] = None
                return Batch((old, recent, unknown), None, Coverage(window_start=now - timezone.timedelta(days=8)))

        report = collect_sources(
            self.owner, adapters={"window": WindowAdapter()}, holder="window-run", now=now, clock=lambda: now
        )

        self.assertEqual(report.records, 2)
        self.assertEqual(set(source.records.values_list("external_id", flat=True)), {"recent", "unknown"})

    def test_coverage_is_aggregated_across_pages_and_keeps_narrowest_warning(self):
        source = Source.objects.create(owner=self.owner, slug="coverage", name="Coverage", kind="site", adapter="coverage")
        now = timezone.now()

        class CoverageAdapter:
            def collect(self, source, cursor):
                if not cursor:
                    return Batch(
                        (normalized_record(source.slug, "one"),), "page-2",
                        Coverage(window_start=now - timezone.timedelta(days=2), truncated=True, reason="Лимит первой страницы"),
                    )
                return Batch(
                    (normalized_record(source.slug, "two"),), None,
                    Coverage(window_start=now - timezone.timedelta(days=5), truncated=False),
                )

        collect_sources(
            self.owner, adapters={"coverage": CoverageAdapter()}, holder="coverage-run", now=now, clock=lambda: now
        )

        source.refresh_from_db()
        self.assertEqual(source.coverage["window_start"], (now - timezone.timedelta(days=5)).isoformat())
        self.assertTrue(source.coverage["truncated"])
        self.assertEqual(source.coverage["reason"], "Лимит первой страницы")
        self.assertEqual(source.status, Source.Status.LIMITED)

    def test_resume_filters_old_records_on_page_after_crash_but_keeps_unknown_dates(self):
        source = Source.objects.create(
            owner=self.owner, slug="resume-window", name="Resume window", kind="site", adapter="resume-window",
            cursor="page-2", last_success=timezone.now() - timezone.timedelta(minutes=5),
        )
        now = timezone.now()

        class ResumeWindowAdapter:
            def collect(self, source, cursor):
                self.cursor = cursor
                old = normalized_record(source.slug, "old-page-2")
                old["published_at"] = now - timezone.timedelta(days=30)
                unknown = normalized_record(source.slug, "unknown-page-2")
                unknown["published_at"] = None
                return Batch((old, unknown), None, Coverage(window_start=old["published_at"]))

        adapter = ResumeWindowAdapter()
        report = collect_sources(
            self.owner, adapters={"resume-window": adapter}, holder="resume-window-run", now=now, clock=lambda: now
        )

        self.assertEqual(adapter.cursor, "page-2")
        self.assertEqual(report.records, 1)
        self.assertEqual(set(source.records.values_list("external_id", flat=True)), {"unknown-page-2"})

    def test_later_cycle_never_reimports_known_archival_records(self):
        source = Source.objects.create(
            owner=self.owner, slug="later-window", name="Later window", kind="site", adapter="later-window",
            last_success=timezone.now() - timezone.timedelta(days=1),
        )
        now = timezone.now()

        class LaterWindowAdapter:
            def collect(self, source, cursor):
                old = normalized_record(source.slug, "archival")
                old["published_at"] = now - timezone.timedelta(days=90)
                unknown = normalized_record(source.slug, "still-unknown")
                unknown["published_at"] = None
                return Batch((old, unknown), None, Coverage(window_start=old["published_at"]))

        report = collect_sources(
            self.owner, adapters={"later-window": LaterWindowAdapter()}, holder="later-window-run", now=now, clock=lambda: now
        )

        self.assertEqual(report.records, 1)
        self.assertEqual(set(source.records.values_list("external_id", flat=True)), {"still-unknown"})

    def test_malformed_config_with_cursor_is_rejected_before_resume(self):
        malformed = Source.objects.create(
            owner=self.owner, slug="malformed-resume", name="Malformed resume", kind="site", adapter="fixture",
            config=["bad"], cursor="resume-token",
        )

        class Adapter:
            def collect(self, source, cursor):
                return Batch((normalized_record(source.slug, "must-not-import"),), None, Coverage())

        report = collect_sources(self.owner, adapters={"fixture": Adapter()}, holder="malformed-resume-run")

        malformed.refresh_from_db()
        self.assertEqual((report.failed, report.records), (1, 0))
        self.assertEqual(malformed.cursor, "resume-token")
        self.assertEqual(malformed.config["last_error"]["code"], "invalid_source_config")
        self.assertFalse(Vacancy.objects.exists())


@override_settings(ROOT_URLCONF="config.urls")
class SourceScreenTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="password")
        self.client.force_login(self.owner)

    def test_screen_shows_honest_status_mode_error_coverage_and_two_frequencies(self):
        seed_sources(self.owner)
        source = Source.objects.get(owner=self.owner, slug="jobicy")
        source.status = Source.Status.LIMITED
        source.last_success = timezone.now()
        source.coverage = {
            "window_start": "2026-09-17T08:00:00+00:00",
            "truncated": True,
            "reason": "Лимит 200 записей",
            "provider_updated_at": "2026-09-20T08:00:00+00:00",
        }
        source.config = {
            **source.config,
            "last_check": "2026-09-20T09:00:00+00:00",
            "last_error": {"code": "upstream_429", "message": "Поставщик ограничил частоту."},
        }
        source.save()
        finite = Source.objects.get(owner=self.owner, slug="remote-ok")
        finite.coverage = {"window_start": "2026-09-18T08:00:00+00:00", "truncated": False, "reason": ""}
        finite.save(update_fields=["coverage"])
        unknown = Source.objects.get(owner=self.owner, slug="himalayas")
        unknown.coverage = {"window_start": None, "truncated": False, "reason": ""}
        unknown.save(update_fields=["coverage"])

        response = self.client.get(reverse("sources"))

        self.assertEqual(response.status_code, 200)
        for expected in (
            "Источники", "50 источников", "9 сайтов", "41 Telegram", "Jobicy",
            "Открытый API", "Ограниченная выдача", "Лимит 200 записей",
            "Поставщик ограничил частоту.", "Интервал сервиса", "Свежесть поставщика",
            "Последняя проверка", "Окно с 17.09.2026 11:00", "Окно с 18.09.2026 11:00",
            "Граница окна неизвестна", "Ограниченная выдача",
        ):
            self.assertContains(response, expected)
        self.assertNotContains(response, "Окно не ограничено")
        self.assertContains(response, "aria-current=\"page\"")
        self.assertContains(response, reverse("manage"))
        self.assertContains(response, "Управлять Telegram-каналами")

    def test_invalid_interval_is_rendered_fail_closed_instead_of_500(self):
        seed_sources(self.owner)
        source = Source.objects.get(owner=self.owner, slug="remote-ok")
        source.config = {**source.config, "service_interval_seconds": "hourly"}
        source.save(update_fields=["config"])

        response = self.client.get(reverse("sources"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ошибка настройки интервала")
