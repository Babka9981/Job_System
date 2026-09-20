import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import timedelta
from http.client import IncompleteRead
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from jobs.models.models import Source, SourceRecord, TemporarySourceContent, Vacancy
from jobs.sources.core.contracts import Batch, SourceCollectionError
from jobs.sources.core.collector import default_adapters
from jobs.sources.keyed.crypto_jobs_list import CryptoJobsListAdapter
from jobs.sources.keyed.durable import DurableUtcCounter
from jobs.sources.keyed.http import KeyedJsonHttpClient
from jobs.sources.keyed.readiness import manual_only_sources
from jobs.sources.keyed.remote_rocketship import (
    RemoteRocketshipAdapter,
    RocketshipDailyQuota,
    TemporaryRocketshipStore,
)

from jobs.sources.keyed.web3_career import Web3CareerAdapter
from jobs.sources.rvc.adapter import RvcAdapter, RvcMcpClient, RvcStreamableHttpToolCaller


class RecordingHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.payload

    def post_json(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        callback = kwargs.get("before_attempt")
        if callback:
            callback()
        return self.payload


class KeyedAdapterTests(TestCase):
    def setUp(self):
        self.private_directory = tempfile.TemporaryDirectory(prefix="job-t05-private-")
        self.settings_override = override_settings(PRIVATE_ROOT=Path(self.private_directory.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.private_directory.cleanup)
        self.owner = get_user_model().objects.create_user("owner")

    def source(self, slug, adapter, config=None):
        return Source.objects.create(
            owner=self.owner, slug=slug, name=slug, kind="site", adapter=adapter, config=config or {}
        )

    @mock.patch.dict(os.environ, {"WEB3_CAREER_API_TOKEN": "super-secret"}, clear=False)
    def test_web3_queries_are_bounded_and_apply_url_is_unchanged(self):
        source = self.source("web3-career", "web3_career", {"role_queries": ["product-manager", "customer-support"], "limit": 999})
        http = RecordingHttp(["metadata", "generated", [{
            "id": 17,
            "title": "Product Lead",
            "company": "DAO",
            "description": "<p>Own product</p>",
            "apply_url": "https://web3.career/jobs/17?ref=provider",
            "date": "2026-09-20T10:00:00Z",
        }]])

        first = Web3CareerAdapter(http=http).collect(source)
        second = Web3CareerAdapter(http=http).collect(source, first.next_cursor)

        self.assertEqual(http.calls[0][2]["params"]["limit"], 100)
        self.assertEqual(http.calls[0][2]["params"]["tag"], "product-manager")
        self.assertEqual(http.calls[1][2]["params"]["tag"], "customer-support")
        self.assertNotIn("show_description", http.calls[0][2]["params"])
        self.assertEqual(first.records[0]["apply_url"], "https://web3.career/jobs/17?ref=provider")
        self.assertFalse(first.records[0]["description_permission"])
        self.assertNotIn("super-secret", repr(first))
        self.assertIsNone(second.next_cursor)

    @mock.patch.dict(os.environ, {
        "WEB3_CAREER_API_TOKEN": "super-secret",
        "WEB3_CAREER_FULL_DESCRIPTION_CONFIRMED": "true",
    }, clear=True)
    def test_web3_description_permission_requires_explicit_local_contract_confirmation(self):
        source = self.source("web3-career", "web3_career")
        record = Web3CareerAdapter(http=RecordingHttp([{
            "id": 17,
            "title": "Product Lead",
            "company": "DAO",
            "description": "Full role body",
            "apply_url": "https://web3.career/jobs/17",
        }])).collect(source).records[0]

        self.assertTrue(record["description_permission"])
        self.assertTrue(record["attribution"]["full_description_contract_confirmed"])

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_web3_token_is_safe_and_does_not_block_other_adapters(self):
        with self.assertRaises(SourceCollectionError) as caught:
            Web3CareerAdapter(http=RecordingHttp([])).collect(self.source("web3-career", "web3_career"))
        self.assertEqual(caught.exception.code, "credentials_missing")
        self.assertNotIn("token", caught.exception.safe_message.casefold())

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_credentials_are_never_read_from_source_config(self):
        source = self.source("web3-career", "web3_career", {"token": "must-not-be-used"})
        with self.assertRaises(SourceCollectionError) as caught:
            Web3CareerAdapter(http=RecordingHttp([])).collect(source)
        self.assertEqual(caught.exception.code, "credentials_missing")
        self.assertNotIn("must-not-be-used", repr(caught.exception))

    @mock.patch.dict(os.environ, {
        "CRYPTOJOBS_LIST_API_KEY": "cjl-secret",
        "CRYPTOJOBS_LIST_FULL_DESCRIPTION_CONFIRMED": "true",
    }, clear=False)
    def test_crypto_jobs_list_pages_to_total_pages_and_keeps_full_description_provenance(self):
        source = self.source("crypto-jobs-list", "crypto_jobs_list")
        http = RecordingHttp({
            "jobs": [{
                "id": "job-1", "jobTitle": "Product Manager", "companyName": "Protocol",
                "description": "<p>Full role description</p>", "canonicalURL": "https://cryptojobslist.com/jobs/job-1",
                "publishedAt": "2026-09-20T09:00:00Z", "remote": True, "tags": ["product", "defi"],
            }],
            "meta": {"page": 1, "totalPages": 2},
        })

        batch = CryptoJobsListAdapter(http=http).collect(source)

        self.assertEqual(batch.next_cursor, "2")
        self.assertEqual(http.calls[0][2]["headers"], {"x-api-key": "cjl-secret"})
        self.assertEqual(batch.records[0]["description"], "Full role description")
        self.assertTrue(batch.records[0]["description_permission"])
        self.assertEqual(batch.records[0]["attribution"]["url"], "https://cryptojobslist.com/jobs/job-1")

    @mock.patch.dict(os.environ, {"CRYPTOJOBS_LIST_API_KEY": "cjl-secret"}, clear=False)
    def test_crypto_jobs_list_makes_at_most_four_cycle_starts_per_day(self):
        source = self.source("crypto-jobs-list", "crypto_jobs_list")
        http = RecordingHttp({"jobs": [], "meta": {"page": 1, "totalPages": 1}})
        adapter = CryptoJobsListAdapter(http=http)

        for _ in range(4):
            adapter.collect(source)
        with self.assertRaises(SourceCollectionError) as caught:
            adapter.collect(source)

        self.assertEqual(caught.exception.code, "daily_cycle_limit")
        self.assertEqual(len(http.calls), 4)
        self.assertEqual(CryptoJobsListAdapter.recommended_schedule_hours, (9, 13, 17, 21))

    @mock.patch.dict(os.environ, {"CRYPTOJOBS_LIST_API_KEY": "cjl-secret"}, clear=True)
    def test_crypto_description_is_not_permitted_without_local_contract_confirmation(self):
        source = self.source("crypto-jobs-list", "crypto_jobs_list")
        http = RecordingHttp({
            "jobs": [{
                "id": "job-1", "jobTitle": "PM", "companyName": "Protocol",
                "description": "A field that looks complete", "canonicalURL": "https://cryptojobslist.com/jobs/job-1",
            }],
            "meta": {"page": 1, "totalPages": 1},
        })
        record = CryptoJobsListAdapter(http=http).collect(source).records[0]
        self.assertFalse(record["description_permission"])
        self.assertFalse(record["attribution"]["full_description_contract_confirmed"])

    def test_manual_only_sources_are_explicit_not_ready_links(self):
        statuses = {item.slug: item for item in manual_only_sources()}
        self.assertEqual(set(statuses), {"remocate", "itcharm"})
        self.assertTrue(all(item.status == "not_ready" and item.manual_url.startswith("https://") for item in statuses.values()))
        self.assertTrue(all("скрап" in item.reason.casefold() for item in statuses.values()))


class RvcAdapterTests(TestCase):
    def test_production_rvc_wiring_uses_official_streamable_http_without_live_network(self):
        endpoints = []
        calls = []

        class Client:
            def __init__(self, endpoint):
                endpoints.append(endpoint)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def call_tool(self, name, arguments):
                calls.append((name, arguments))
                return {
                    "isError": False,
                    "content": [{"type": "text", "text": json.dumps({"status": "results", "results": []})}],
                }

        adapter = default_adapters()["rvc"]
        transport = adapter.client.call_tool
        self.assertIsInstance(transport, RvcStreamableHttpToolCaller)
        transport.client_factory = Client
        transport.timeout = 1
        source = Source(
            slug="rvc",
            config={"query": "product", "conversation_language_code": "ru", "target_country_codes": []},
        )

        self.assertEqual(adapter.collect(source).records, ())
        self.assertEqual(endpoints, ["https://app.rvc.global/mcp"])
        self.assertEqual(calls[0][0], "rvc_search_jobs")

    def test_rvc_mcp_client_calls_only_official_tool_names(self):
        calls = []

        def call_tool(name, arguments):
            calls.append((name, arguments))
            if name == "rvc_search_jobs":
                payload = {"status": "results", "results": []}
            else:
                payload = {"masked_id": arguments["masked_id"], "attributed_url": "https://app.rvc.global/jobs/x"}
            return {"isError": False, "content": [{"type": "text", "text": json.dumps(payload)}]}

        client = RvcMcpClient(call_tool=call_tool)
        self.assertEqual(client.search_jobs(query="product", conversation_language_code="ru", target_country_codes=[], limit=10)["status"], "results")
        self.assertEqual(client.get_job(masked_id="x")["masked_id"], "x")
        self.assertEqual([name for name, _ in calls], ["rvc_search_jobs", "get_job"])

    def test_rvc_mcp_is_error_is_not_confused_with_zero_results(self):
        zero = RvcMcpClient(call_tool=lambda *_: {
            "isError": False,
            "content": [{"type": "text", "text": json.dumps({"status": "results", "results": []})}],
        })
        self.assertEqual(zero.search_jobs(query="product")["results"], [])

        failed = RvcMcpClient(call_tool=lambda *_: {
            "isError": True,
            "content": [{"type": "text", "text": "private upstream details"}],
        })
        with self.assertRaises(SourceCollectionError) as caught:
            failed.search_jobs(query="product")
        self.assertEqual(caught.exception.code, "upstream_mcp_tool")
        self.assertNotIn("private upstream details", repr(caught.exception))

    def test_rvc_mcp_timeout_retry_is_bounded_and_safe(self):
        attempts = []
        sleeps = []

        def call_tool(name, arguments):
            attempts.append(name)
            raise TimeoutError("provider internals")

        client = RvcMcpClient(call_tool=call_tool, sleeper=sleeps.append)
        with self.assertRaises(SourceCollectionError) as caught:
            client.search_jobs(query="product", conversation_language_code="ru", target_country_codes=[], limit=10)
        self.assertEqual(caught.exception.code, "upstream_mcp")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleeps, [1, 2])
        self.assertNotIn("internals", repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(
            "provider internals",
            "".join(traceback.format_exception(caught.exception)),
        )

    def test_rvc_mcp_provider_exception_does_not_retain_secret_chain(self):
        secret = "rvc-provider-secret-never-log"

        def call_tool(name, arguments):
            raise RuntimeError(f"provider response contained {secret}")

        client = RvcMcpClient(call_tool=call_tool)
        with self.assertRaises(SourceCollectionError) as caught:
            client.search_jobs(query="product")

        self.assertEqual(caught.exception.code, "upstream_mcp")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        rendered = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn(secret, rendered)

    def test_rvc_uses_official_tools_caps_results_and_never_claims_full_jd(self):
        class Client:
            def search_jobs(self, **arguments):
                self.arguments = arguments
                return [{
                    "masked_id": f"masked-{index}", "title": f"Role {index}", "company": "RVC Co",
                    "arrangement": "FULLY_REMOTE", "salary_display": "$5k", "skills": ["Product"],
                    "attributed_url": f"https://app.rvc.global/jobs/masked-{index}",
                } for index in range(12)]

        client = Client()
        source = Source(slug="rvc", config={"query": "product manager", "conversation_language_code": "ru", "target_country_codes": []})
        batch = RvcAdapter(client=client).collect(source)

        self.assertEqual(client.arguments["limit"], 10)
        self.assertEqual(client.arguments["work_arrangements"], ["FULLY_REMOTE"])
        self.assertEqual(len(batch.records), 10)
        self.assertEqual(batch.records[0]["canonical_url"], batch.records[0]["attribution"]["url"])
        self.assertEqual(batch.records[0]["description"], "")
        self.assertFalse(batch.records[0]["description_permission"])
        self.assertIn("teaser", batch.coverage.reason.casefold())

    def test_rvc_get_job_uses_masked_id_and_stays_teaser_only(self):
        class Client:
            def get_job(self, *, masked_id):
                self.masked_id = masked_id
                return {
                    "masked_id": masked_id, "title": "Lead", "company": "RVC",
                    "attributed_url": "https://app.rvc.global/jobs/masked", "teaser": "Not a full body",
                }

        client = Client()
        source = Source(slug="rvc", config={})
        record = RvcAdapter(client=client).get_job(source, "masked")
        self.assertEqual(client.masked_id, "masked")
        self.assertEqual(record["description"], "")
        self.assertFalse(record["description_permission"])


class KeyedHttpTests(TestCase):
    def test_401_is_non_retryable_and_secret_never_enters_safe_error(self):
        secret = "ultra-secret"

        def opener(request, timeout):
            raise HTTPError(request.full_url, 401, secret, {}, None)

        with self.assertRaises(SourceCollectionError) as caught:
            KeyedJsonHttpClient(opener=opener, sleeper=lambda _: None).get_json(
                "https://provider.example/jobs", params={"token": secret}
            )
        self.assertEqual(caught.exception.code, "credentials_rejected")
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn(secret, repr(caught.exception))

    def test_429_retries_are_bounded_and_timeout_is_safe(self):
        attempts = []
        sleeps = []

        def opener(request, timeout):
            attempts.append(timeout)
            raise HTTPError(request.full_url, 429, "quota", {}, None)

        with self.assertRaises(SourceCollectionError) as caught:
            KeyedJsonHttpClient(opener=opener, sleeper=sleeps.append, max_attempts=3).get_json(
                "https://provider.example/jobs", timeout=999
            )
        self.assertEqual(caught.exception.code, "upstream_http_429")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts, [30, 30, 30])
        self.assertEqual(sleeps, [10, 10])

    def test_invalid_json_is_not_mislabeled_as_network_failure(self):
        secret = "invalid-json-secret-never-log"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return f"not-json:{secret}".encode()

        with self.assertRaises(SourceCollectionError) as caught:
            KeyedJsonHttpClient(opener=lambda request, timeout: Response()).get_json("https://provider.example/jobs")
        self.assertEqual(caught.exception.code, "invalid_json")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        rendered = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn(secret, rendered)

    def test_partial_body_failure_retries_without_retaining_partial_secret(self):
        secret = "partial-body-secret-never-log"
        attempts = []
        sleeps = []

        def opener(request, timeout):
            attempts.append(timeout)
            raise IncompleteRead(f"prefix:{secret}".encode(), 100)

        with self.assertRaises(SourceCollectionError) as caught:
            KeyedJsonHttpClient(
                opener=opener,
                sleeper=sleeps.append,
                max_attempts=3,
            ).get_json("https://provider.example/jobs")

        self.assertEqual(caught.exception.code, "upstream_network")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleeps, [1, 2])
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(secret, "".join(traceback.format_exception(caught.exception)))


class RocketshipTemporaryStoreTests(TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="job-t05-temp-")
        self.private_directory = tempfile.TemporaryDirectory(prefix="job-t05-private-")
        self.settings_override = override_settings(
            TEMPORARY_ROOT=Path(self.temporary_directory.name),
            PRIVATE_ROOT=Path(self.private_directory.name),
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temporary_directory.cleanup)
        self.addCleanup(self.private_directory.cleanup)
        self.owner = get_user_model().objects.create_user("owner")
        self.source = Source.objects.create(
            owner=self.owner, slug="remote-rocketship", name="Rocketship", kind="site", adapter="remote_rocketship"
        )

    def response(self):
        return {
            "jobOpenings": [{
                "id": 55, "roleTitle": "Head of Product", "roleDescription": "Full paid description",
                "roleRequirements": "Scale product", "url": "https://company.example/jobs/55",
                "created_at": "2026-09-20T08:00:00Z", "company": {"name": "Acme", "homePageURL": "https://acme.example"},
            }],
            "pagination": {"page": 1, "totalPages": 2, "hasNextPage": True},
        }

    def assert_sanitized_boundary_error(self, caught, *, code, secret):
        self.assertEqual(caught.exception.code, code)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(secret, repr(caught.exception))
        self.assertNotIn(secret, "".join(traceback.format_exception(caught.exception)))

    def test_provider_payload_json_error_does_not_retain_secret_document(self):
        secret = "provider-payload-secret-never-log"
        now = timezone.now()
        batch = RemoteRocketshipAdapter._batch_from_payload(self.source, self.response(), now, page=1)
        store = TemporaryRocketshipStore(clock=lambda: now)
        handle = store.store_batch(self.owner, self.source, batch)[0]
        (store.directory(self.owner, self.source) / f"{handle}.json").write_text(
            f"invalid:{secret}",
            encoding="utf-8",
        )

        with self.assertRaises(SourceCollectionError) as caught:
            store.read(self.owner, self.source, handle)

        self.assert_sanitized_boundary_error(
            caught,
            code="temporary_content_invalid",
            secret=secret,
        )

    def test_private_state_and_index_json_failures_drop_secret_exception_context(self):
        secret = "private-index-secret-never-log"
        store = TemporaryRocketshipStore()
        state_path = store._user_state_path(self.owner, "safe-handle")
        state_path.write_text(f"invalid:{secret}", encoding="utf-8")

        with self.assertRaises(SourceCollectionError) as private_state:
            store.read_user_state(self.owner, "safe-handle")
        self.assert_sanitized_boundary_error(
            private_state,
            code="user_state_missing",
            secret=secret,
        )

        directory = store.directory(self.owner, self.source)
        store._prepare_directory(directory)
        (directory / "index.json").write_text(f"invalid:{secret}", encoding="utf-8")
        with self.assertRaises(SourceCollectionError) as index:
            store.list(self.owner, self.source)
        self.assert_sanitized_boundary_error(
            index,
            code="temporary_index_invalid",
            secret=secret,
        )

    def test_private_state_file_failure_drops_secret_exception_context(self):
        secret = "private-file-secret-never-log"
        store = TemporaryRocketshipStore()
        with mock.patch(
            "jobs.sources.keyed.remote_rocketship.atomic_write_json",
            side_effect=OSError(secret),
        ):
            with self.assertRaises(SourceCollectionError) as caught:
                store.save_user_state(self.owner, "safe-handle", status="saved", note="note")
        self.assert_sanitized_boundary_error(
            caught,
            code="user_state_write_failed",
            secret=secret,
        )

    @mock.patch.dict(os.environ, {
        "REMOTE_ROCKETSHIP_API_KEY": "rocket-secret",
        "REMOTE_ROCKETSHIP_ENABLED": "true",
        "REMOTE_ROCKETSHIP_ACTIVE_PLAN_CONFIRMED": "true",
    }, clear=True)
    def test_rocketship_is_temporary_only_and_never_enters_permanent_models_or_backup(self):
        http = RecordingHttp(self.response())
        now = timezone.now()
        adapter = RemoteRocketshipAdapter(http=http, clock=lambda: now)

        batch = adapter.collect(self.source)
        store = TemporaryRocketshipStore(clock=lambda: now)
        handles = store.store_batch(self.owner, self.source, batch)

        self.assertEqual(batch.next_cursor, "2")
        request = http.calls[0]
        self.assertEqual(request[2]["headers"], {"Authorization": "Bearer rocket-secret"})
        self.assertLessEqual(request[2]["json_body"]["filters"]["itemsPerPage"], 50)
        self.assertTrue(request[2]["json_body"]["includeJobDescription"])
        self.assertEqual(len(handles), 1)
        payload = store.read(self.owner, self.source, handles[0])
        self.assertEqual(set(payload), {"raw", "normalized", "derived", "cache"})
        index = json.loads((store.directory(self.owner, self.source) / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(set(index), {"version", "items"})
        self.assertEqual(set(index["items"]), {handles[0]})
        self.assertEqual(set(index["items"][handles[0]]), {"file", "expires_at"})
        self.assertEqual(payload["raw"]["id"], 55)
        self.assertNotIn("id", index["items"][handles[0]])
        self.assertNotIn("url", index["items"][handles[0]])
        self.assertFalse(Vacancy.objects.exists())
        self.assertFalse(SourceRecord.objects.exists())
        self.assertFalse(TemporarySourceContent.objects.exists())
        private_bytes = b"".join(path.read_bytes() for path in Path(self.private_directory.name).rglob("*.json"))
        self.assertNotIn(b"company.example", private_bytes)
        self.assertNotIn(b"Head of Product", private_bytes)

    @mock.patch.dict(os.environ, {
        "REMOTE_ROCKETSHIP_API_KEY": "rocket-secret",
        "REMOTE_ROCKETSHIP_ENABLED": "true",
        "REMOTE_ROCKETSHIP_ACTIVE_PLAN_CONFIRMED": "true",
    }, clear=True)
    def test_expired_content_fails_closed_but_user_state_survives_without_provider_data(self):
        current = [timezone.now()]
        adapter = RemoteRocketshipAdapter(http=RecordingHttp(self.response()), clock=lambda: current[0])
        store = TemporaryRocketshipStore(clock=lambda: current[0])
        handle = store.store_batch(self.owner, self.source, adapter.collect(self.source))[0]
        store.save_user_state(self.owner, handle, status="saved", note="My note")

        current[0] += timedelta(hours=24, seconds=1)
        with self.assertRaises(SourceCollectionError) as caught:
            store.read(self.owner, self.source, handle)

        self.assertEqual(caught.exception.code, "temporary_content_expired")
        self.assertEqual(store.read_user_state(self.owner, handle), {"status": "saved", "note": "My note"})
        self.assertEqual(store.list(self.owner, self.source), ())
        private_bytes = b"".join(path.read_bytes() for path in Path(self.private_directory.name).rglob("*.json"))
        self.assertNotIn(b"company.example", private_bytes)
        self.assertNotIn(b"Head of Product", private_bytes)

    def test_atomic_store_rolls_back_files_and_purge_discovers_orphans(self):
        now = timezone.now()
        batch = RemoteRocketshipAdapter._batch_from_payload(self.source, self.response(), now, page=1)
        replacements = []

        def fail_index_replace(source, target):
            replacements.append(Path(target).name)
            if Path(target).name == "index.json":
                raise OSError("disk failure")
            os.replace(source, target)

        failing = TemporaryRocketshipStore(clock=lambda: now, replacer=fail_index_replace)
        with self.assertRaises(SourceCollectionError):
            failing.store_batch(self.owner, self.source, batch)
        self.assertEqual(failing.list(self.owner, self.source), ())
        self.assertEqual(list(failing.directory(self.owner, self.source).glob("*.json")), [])

        orphan = failing.directory(self.owner, self.source) / "orphan.json"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("{}", encoding="utf-8")
        report = TemporaryRocketshipStore(clock=lambda: now).purge_expired(self.owner, self.source)
        self.assertEqual(report.orphans, 1)
        self.assertFalse(orphan.exists())

    def test_purge_all_rejects_directory_symlink_and_never_touches_outside_files(self):
        root = Path(self.temporary_directory.name) / "rocketship"
        owner_directory = root / str(self.owner.pk)
        owner_directory.mkdir(parents=True)
        outside = Path(self.private_directory.name) / "outside-provider-data"
        outside.mkdir()
        victim = outside / "victim.json"
        victim.write_text('{"must":"survive"}', encoding="utf-8")
        link = owner_directory / self.source.slug
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if os.name != "nt":
                self.skipTest(f"directory symlink is unavailable: {exc}")
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if created.returncode:
                self.skipTest("directory symlink and junction are unavailable")

        with self.assertRaises(SourceCollectionError) as caught:
            TemporaryRocketshipStore().purge_all()

        self.assertEqual(caught.exception.code, "temporary_path_unsafe")
        self.assertTrue(victim.exists())
        self.assertEqual(victim.read_text(encoding="utf-8"), '{"must":"survive"}')

    def test_purge_all_rejects_indexed_file_symlink_outside_exact_store_root(self):
        directory = TemporaryRocketshipStore().directory(self.owner, self.source)
        directory.mkdir(parents=True)
        outside = Path(self.private_directory.name) / "outside.json"
        outside.write_text('{"must":"survive"}', encoding="utf-8")
        handle = "safe-handle"
        linked = directory / f"{handle}.json"
        try:
            linked.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"file symlink is unavailable: {exc}")
        expires = (timezone.now() + timedelta(hours=1)).isoformat()
        (directory / "index.json").write_text(json.dumps({
            "version": 1,
            "items": {handle: {"file": f"{handle}.json", "expires_at": expires}},
        }), encoding="utf-8")

        with self.assertRaises(SourceCollectionError) as caught:
            TemporaryRocketshipStore().purge_all()

        self.assertEqual(caught.exception.code, "temporary_path_unsafe")
        self.assertTrue(outside.exists())

    def test_purge_all_busy_lock_is_bounded_and_preserves_orphan(self):
        directory = TemporaryRocketshipStore().directory(self.owner, self.source)
        directory.mkdir(parents=True)
        orphan = directory / "orphan.json"
        orphan.write_text("{}", encoding="utf-8")
        (directory / ".store.lock").mkdir()
        started = time.monotonic()

        with self.assertRaises(SourceCollectionError) as caught:
            TemporaryRocketshipStore(lock_timeout=0.05).purge_all()

        self.assertEqual(caught.exception.code, "temporary_store_busy")
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(orphan.exists())

    def test_purge_all_retries_when_observed_lock_disappears_before_validation(self):
        store = TemporaryRocketshipStore(lock_timeout=0.2, sleeper=lambda _: None)
        directory = store.directory(self.owner, self.source)
        directory.mkdir(parents=True)
        orphan = directory / "orphan.json"
        orphan.write_text("{}", encoding="utf-8")
        lock = directory / ".store.lock"
        original_mkdir = Path.mkdir
        lock_attempts = []

        def racing_mkdir(path, *args, **kwargs):
            if path == lock:
                lock_attempts.append(path)
                if len(lock_attempts) == 1:
                    raise FileExistsError
            return original_mkdir(path, *args, **kwargs)

        with mock.patch.object(Path, "mkdir", racing_mkdir):
            report = store.purge_all()

        self.assertGreaterEqual(len(lock_attempts), 2)
        self.assertEqual(report.orphans, 1)
        self.assertFalse(orphan.exists())

    def test_two_spawned_processes_share_lock_without_hang_lost_purge_or_outside_delete(self):
        directory = TemporaryRocketshipStore().directory(self.owner, self.source)
        directory.mkdir(parents=True)
        orphan = directory / "orphan.json"
        orphan.write_text("{}", encoding="utf-8")
        ready = directory / "holder.ready"
        release = directory / "holder.release"
        environment = dict(os.environ, DJANGO_SETTINGS_MODULE="config.settings")
        holder_script = """
import sys, time
from pathlib import Path
import django
django.setup()
from django.test import override_settings
from jobs.sources.keyed.remote_rocketship import TemporaryRocketshipStore
root, directory, ready, release = map(Path, sys.argv[1:])
with override_settings(TEMPORARY_ROOT=root):
    with TemporaryRocketshipStore(lock_timeout=2)._directory_lock(directory):
        ready.write_text('ready', encoding='utf-8')
        deadline = time.monotonic() + 8
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not release.exists():
            raise SystemExit('holder-timeout')
print('holder-released')
"""
        contender_script = """
import sys
from pathlib import Path
import django
django.setup()
from django.test import override_settings
from jobs.sources.keyed.remote_rocketship import TemporaryRocketshipStore
from jobs.sources.core.contracts import SourceCollectionError
with override_settings(TEMPORARY_ROOT=Path(sys.argv[1])):
    try:
        TemporaryRocketshipStore(lock_timeout=0.2).purge_all()
    except SourceCollectionError as exc:
        print(exc.code)
    else:
        print('unexpected-success')
"""
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_script, self.temporary_directory.name,
             str(directory), str(ready), str(release)],
            cwd=Path.cwd(),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "lock holder must become ready without hang")
            contender = subprocess.run(
                [sys.executable, "-c", contender_script, self.temporary_directory.name],
                cwd=Path.cwd(),
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(contender.returncode, 0, contender.stderr)
            self.assertEqual(contender.stdout.strip(), "temporary_store_busy")
            self.assertTrue(orphan.exists())
            release.write_text("release", encoding="utf-8")
            stdout, stderr = holder.communicate(timeout=5)
            self.assertEqual(holder.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), "holder-released")
        finally:
            if holder.poll() is None:
                holder.terminate()
                holder.communicate(timeout=5)
        report = TemporaryRocketshipStore().purge_all()
        self.assertEqual(report.orphans, 1)
        self.assertFalse(orphan.exists())

    @mock.patch.dict(os.environ, {"REMOTE_ROCKETSHIP_API_KEY": "rocket-secret"}, clear=True)
    def test_key_alone_does_not_activate_paid_api(self):
        http = RecordingHttp(self.response())
        with self.assertRaises(SourceCollectionError) as caught:
            RemoteRocketshipAdapter(http=http).collect(self.source)
        self.assertEqual(caught.exception.code, "source_not_activated")
        self.assertEqual(http.calls, [])

    def test_daily_quota_is_durable_and_fails_closed_at_both_caps(self):
        now = timezone.now()
        quota = RocketshipDailyQuota(clock=lambda: now)
        for _ in range(500):
            quota.reserve_request()
        with self.assertRaises(SourceCollectionError) as request_error:
            RocketshipDailyQuota(clock=lambda: now).reserve_request()
        self.assertEqual(request_error.exception.code, "daily_request_quota")

        jobs_quota = RocketshipDailyQuota(clock=lambda: now, namespace="rocketship-jobs-test")
        jobs_quota.record_jobs(3000)
        with self.assertRaises(SourceCollectionError) as jobs_error:
            RocketshipDailyQuota(clock=lambda: now, namespace="rocketship-jobs-test").record_jobs(1)
        self.assertEqual(jobs_error.exception.code, "daily_job_quota")

    def test_quota_json_and_file_failures_drop_secret_exception_context(self):
        now = timezone.now()
        secret = "quota-boundary-secret-never-log"
        quota = RocketshipDailyQuota(clock=lambda: now, namespace="quota-sanitized")
        quota.counter.root.mkdir(parents=True)
        state_path = quota.counter.root / f"{now.date().isoformat()}.json"
        state_path.write_text(f"invalid:{secret}", encoding="utf-8")

        with self.assertRaises(SourceCollectionError) as invalid:
            quota.reserve_request()
        self.assert_sanitized_boundary_error(
            invalid,
            code="quota_state_invalid",
            secret=secret,
        )

        state_path.unlink()
        with mock.patch(
            "jobs.sources.keyed.durable.atomic_write_json",
            side_effect=OSError(secret),
        ):
            with self.assertRaises(SourceCollectionError) as write_failed:
                quota.reserve_request()
        self.assert_sanitized_boundary_error(
            write_failed,
            code="quota_state_write_failed",
            secret=secret,
        )

    @mock.patch.dict(os.environ, {
        "REMOTE_ROCKETSHIP_API_KEY": "rocket-secret",
        "REMOTE_ROCKETSHIP_ENABLED": "true",
        "REMOTE_ROCKETSHIP_ACTIVE_PLAN_CONFIRMED": "true",
    }, clear=True)
    def test_every_http_retry_reserves_request_and_cap_stops_before_next_attempt(self):
        now = timezone.now()
        attempts = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps(self_response).encode("utf-8")

        self_response = self.response()

        def flaky_opener(request, timeout):
            attempts.append(request)
            if len(attempts) < 3:
                raise HTTPError(request.full_url, 500, "temporary", {}, None)
            return Response()

        quota = RocketshipDailyQuota(clock=lambda: now, namespace="retry-count")
        adapter = RemoteRocketshipAdapter(
            http=KeyedJsonHttpClient(opener=flaky_opener, sleeper=lambda _: None),
            clock=lambda: now,
            quota=quota,
        )
        adapter.collect(self.source)
        self.assertEqual(len(attempts), 3)
        for _ in range(497):
            quota.reserve_request()
        with self.assertRaises(SourceCollectionError) as exhausted:
            quota.reserve_request()
        self.assertEqual(exhausted.exception.code, "daily_request_quota")

        capped_attempts = []

        def always_fails(request, timeout):
            capped_attempts.append(request)
            raise HTTPError(request.full_url, 500, "temporary", {}, None)

        capped = RocketshipDailyQuota(clock=lambda: now, namespace="retry-hard-cap")
        for _ in range(499):
            capped.reserve_request()
        with self.assertRaises(SourceCollectionError) as stopped:
            RemoteRocketshipAdapter(
                http=KeyedJsonHttpClient(opener=always_fails, sleeper=lambda _: None),
                clock=lambda: now,
                quota=capped,
            ).collect(self.source)
        self.assertEqual(stopped.exception.code, "daily_request_quota")
        self.assertEqual(len(capped_attempts), 1)

    @mock.patch.dict(os.environ, {
        "REMOTE_ROCKETSHIP_API_KEY": "rocket-secret",
        "REMOTE_ROCKETSHIP_ENABLED": "true",
        "REMOTE_ROCKETSHIP_ACTIVE_PLAN_CONFIRMED": "true",
    }, clear=True)
    def test_uncertain_partial_response_retry_reserves_job_capacity_per_attempt(self):
        now = timezone.now()
        attempts = []
        quota = RocketshipDailyQuota(clock=lambda: now, namespace="partial-retry-job-cap")
        quota.record_jobs(2950)

        def incomplete_response(request, timeout):
            attempts.append(request)
            raise IncompleteRead(b"possibly-delivered-page", 100)

        with self.assertRaises(SourceCollectionError) as caught:
            RemoteRocketshipAdapter(
                http=KeyedJsonHttpClient(opener=incomplete_response, sleeper=lambda _: None),
                clock=lambda: now,
                quota=quota,
            ).collect(self.source)

        self.assertEqual(caught.exception.code, "daily_job_quota")
        self.assertEqual(len(attempts), 1)
        with self.assertRaises(SourceCollectionError) as exhausted:
            quota.record_jobs(1)
        self.assertEqual(exhausted.exception.code, "daily_job_quota")

    def test_concurrent_stores_keep_both_index_updates(self):
        now = timezone.now()
        first = RemoteRocketshipAdapter._batch_from_payload(self.source, self.response(), now, page=1)
        second_response = self.response()
        second_response["jobOpenings"][0] = {**second_response["jobOpenings"][0], "id": 56, "url": "https://company.example/jobs/56"}
        second = RemoteRocketshipAdapter._batch_from_payload(self.source, second_response, now, page=1)
        store = TemporaryRocketshipStore(clock=lambda: now)
        results = []
        errors = []

        def write(batch):
            try:
                results.extend(store.store_batch(self.owner, self.source, batch))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(batch,)) for batch in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(set(store.list(self.owner, self.source)), set(results))

    def test_same_page_key_is_idempotent_and_rollback_never_removes_preexisting_card(self):
        now = timezone.now()
        batch = RemoteRocketshipAdapter._batch_from_payload(self.source, self.response(), now, page=1)
        store = TemporaryRocketshipStore(clock=lambda: now)

        first = store.store_page(self.owner, self.source, batch, page_key="initial:never")
        retried = store.store_page(self.owner, self.source, batch, page_key="initial:never")
        store.rollback_page(self.owner, self.source, retried)

        self.assertEqual(retried.handles, first.handles)
        self.assertEqual(retried.created, ())
        self.assertEqual(store.list(self.owner, self.source), first.handles)

    def test_stable_record_identity_survives_reorder_and_changed_rollback_restores_exact_card(self):
        now = timezone.now()
        response = self.response()
        response["jobOpenings"].append({
            **response["jobOpenings"][0],
            "id": 56,
            "url": "https://company.example/jobs/56",
            "roleTitle": "Product Lead",
        })
        first_batch = RemoteRocketshipAdapter._batch_from_payload(self.source, response, now, page=1)
        store = TemporaryRocketshipStore(clock=lambda: now)
        first = store.store_page(self.owner, self.source, first_batch, page_key="initial:never")
        by_id = {store.read(self.owner, self.source, handle)["raw"]["id"]: handle for handle in first.handles}
        index_path = store.directory(self.owner, self.source) / "index.json"
        original_index = json.loads(index_path.read_text(encoding="utf-8"))

        reordered = Batch(records=tuple(reversed(first_batch.records)))
        reorder_receipt = store.store_page(self.owner, self.source, reordered, page_key="initial:never")
        self.assertEqual(set(reorder_receipt.handles), set(first.handles))
        self.assertEqual(reorder_receipt.created, ())

        changed_response = self.response()
        changed_response["jobOpenings"][0]["roleDescription"] = "Changed description"
        changed = RemoteRocketshipAdapter._batch_from_payload(self.source, changed_response, now, page=1)
        update_receipt = store.store_page(self.owner, self.source, changed, page_key="initial:never")
        self.assertEqual(update_receipt.handles, (by_id[55],))
        self.assertEqual(store.read(self.owner, self.source, by_id[55])["raw"]["roleDescription"], "Changed description")

        store.rollback_page(self.owner, self.source, update_receipt)
        store.rollback_page(self.owner, self.source, update_receipt)
        restored = store.read(self.owner, self.source, by_id[55])
        self.assertEqual(restored["raw"]["roleDescription"], "Full paid description")
        self.assertEqual(set(store.list(self.owner, self.source)), set(first.handles))
        self.assertEqual(json.loads(index_path.read_text(encoding="utf-8")), original_index)

        successful = store.store_page(self.owner, self.source, changed, page_key="initial:never")
        self.assertEqual(successful.handles, (by_id[55],))
        self.assertEqual(len(store.list(self.owner, self.source)), 2)
        self.assertEqual(store.read(self.owner, self.source, by_id[55])["raw"]["roleDescription"], "Changed description")

    def test_changed_existing_card_is_restored_when_index_commit_fails(self):
        now = timezone.now()
        original = RemoteRocketshipAdapter._batch_from_payload(self.source, self.response(), now, page=1)
        base = TemporaryRocketshipStore(clock=lambda: now)
        receipt = base.store_page(self.owner, self.source, original, page_key="initial:never")
        handle = receipt.handles[0]

        changed_response = self.response()
        changed_response["jobOpenings"][0]["roleDescription"] = "Must roll back"
        changed = RemoteRocketshipAdapter._batch_from_payload(self.source, changed_response, now, page=1)

        def fail_index(source, target):
            if Path(target).name == "index.json":
                raise OSError("index commit failure")
            os.replace(source, target)

        failing = TemporaryRocketshipStore(clock=lambda: now, replacer=fail_index)
        with self.assertRaises(SourceCollectionError) as caught:
            failing.store_page(self.owner, self.source, changed, page_key="initial:never")

        self.assert_sanitized_boundary_error(
            caught,
            code="temporary_store_failed",
            secret="index commit failure",
        )
        restored = base.read(self.owner, self.source, handle)
        self.assertEqual(restored["raw"]["roleDescription"], "Full paid description")
        self.assertEqual(base.list(self.owner, self.source), (handle,))

    def test_duplicate_or_missing_stable_identity_fails_closed_without_cards(self):
        now = timezone.now()
        batch = RemoteRocketshipAdapter._batch_from_payload(self.source, self.response(), now, page=1)
        store = TemporaryRocketshipStore(clock=lambda: now)
        duplicate = Batch(records=(batch.records[0], batch.records[0]))
        with self.assertRaises(SourceCollectionError) as duplicate_error:
            store.store_page(self.owner, self.source, duplicate, page_key="initial:never")
        self.assertEqual(duplicate_error.exception.code, "temporary_identity_ambiguous")

        missing = dict(batch.records[0])
        missing["external_id"] = ""
        missing["canonical_url"] = ""
        with self.assertRaises(SourceCollectionError) as missing_error:
            store.store_page(self.owner, self.source, Batch(records=(missing,)), page_key="initial:never")
        self.assertEqual(missing_error.exception.code, "temporary_identity_ambiguous")
        self.assertEqual(store.list(self.owner, self.source), ())

    def test_user_state_rejects_owner_directory_symlink_or_junction_without_outside_mutation(self):
        state_root = Path(self.private_directory.name) / "rocketship-user-state"
        state_root.mkdir()
        outside = Path(self.temporary_directory.name) / "outside-user-state"
        outside.mkdir()
        victim = outside / "victim.json"
        victim.write_text('{"must":"survive"}', encoding="utf-8")
        link = state_root / str(self.owner.pk)
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if os.name != "nt":
                self.skipTest(f"directory symlink is unavailable: {exc}")
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if created.returncode:
                self.skipTest("directory symlink and junction are unavailable")

        store = TemporaryRocketshipStore()
        with self.assertRaises(SourceCollectionError) as write_error:
            store.save_user_state(self.owner, "victim", status="saved", note="outside overwrite")
        self.assertEqual(write_error.exception.code, "private_path_unsafe")
        with self.assertRaises(SourceCollectionError) as read_error:
            store.read_user_state(self.owner, "victim")
        self.assertEqual(read_error.exception.code, "private_path_unsafe")
        self.assertEqual(victim.read_text(encoding="utf-8"), '{"must":"survive"}')

    def test_two_subprocess_stores_do_not_lose_index_updates(self):
        gate = Path(self.temporary_directory.name) / "writers.start"
        environment = dict(os.environ, DJANGO_SETTINGS_MODULE="config.settings")
        writer_script = """
import sys, time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import django
django.setup()
from django.test import override_settings
from django.utils import timezone
from jobs.sources.core.contracts import Batch
from jobs.sources.keyed.remote_rocketship import TemporaryRocketshipStore
root, gate = map(Path, sys.argv[1:3])
deadline = time.monotonic() + 5
while not gate.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
if not gate.exists():
    raise SystemExit('writer-gate-timeout')
record = {
    'expires_at': timezone.now() + timedelta(hours=1),
    '_temporary_payload': {'raw': {'id': sys.argv[3]}, 'normalized': {}, 'derived': {}, 'cache': {}},
}
with override_settings(TEMPORARY_ROOT=root):
    handles = TemporaryRocketshipStore(lock_timeout=2).store_batch(
        SimpleNamespace(pk=int(sys.argv[4])),
        SimpleNamespace(slug=sys.argv[5]),
        Batch(records=(record,)),
    )
print(handles[0])
"""
        writers = [
            subprocess.Popen(
                [sys.executable, "-c", writer_script, self.temporary_directory.name,
                 str(gate), external_id, str(self.owner.pk), self.source.slug],
                cwd=Path.cwd(),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for external_id in ("first", "second")
        ]
        try:
            gate.write_text("start", encoding="utf-8")
            handles = []
            for writer in writers:
                stdout, stderr = writer.communicate(timeout=5)
                self.assertEqual(writer.returncode, 0, stderr)
                handles.append(stdout.strip())
        finally:
            for writer in writers:
                if writer.poll() is None:
                    writer.terminate()
                    writer.communicate(timeout=5)

        self.assertEqual(len(set(handles)), 2)
        self.assertEqual(
            set(TemporaryRocketshipStore().list(self.owner, self.source)),
            set(handles),
        )

    def test_list_waits_for_store_and_does_not_purge_fresh_staged_file(self):
        now = timezone.now()
        batch = RemoteRocketshipAdapter._batch_from_payload(self.source, self.response(), now, page=1)
        content_written = threading.Event()
        allow_index = threading.Event()

        def blocking_replace(source, target):
            os.replace(source, target)
            if Path(target).name != "index.json":
                content_written.set()
                allow_index.wait(timeout=5)

        store = TemporaryRocketshipStore(clock=lambda: now, replacer=blocking_replace)
        stored = []
        listed = []
        writer = threading.Thread(target=lambda: stored.extend(store.store_batch(self.owner, self.source, batch)))
        writer.start()
        self.assertTrue(content_written.wait(timeout=5))
        reader = threading.Thread(target=lambda: listed.extend(store.list(self.owner, self.source)))
        reader.start()
        time.sleep(0.05)
        self.assertTrue(reader.is_alive())
        allow_index.set()
        writer.join(timeout=5)
        reader.join(timeout=5)

        self.assertEqual(listed, stored)
        self.assertTrue((store.directory(self.owner, self.source) / f"{stored[0]}.json").exists())

    @mock.patch.dict(os.environ, {
        "REMOTE_ROCKETSHIP_API_KEY": "rocket-secret",
        "REMOTE_ROCKETSHIP_ENABLED": "true",
        "REMOTE_ROCKETSHIP_ACTIVE_PLAN_CONFIRMED": "true",
    }, clear=True)
    def test_job_quota_is_checked_before_network_and_payload_size_after(self):
        now = timezone.now()
        quota = RocketshipDailyQuota(clock=lambda: now)
        quota.record_jobs(3000)
        http = RecordingHttp(self.response())
        with self.assertRaises(SourceCollectionError) as caught:
            RemoteRocketshipAdapter(http=http, clock=lambda: now, quota=quota).collect(self.source)
        self.assertEqual(caught.exception.code, "daily_job_quota")
        self.assertEqual(http.calls, [])

        class Quota:
            def reserve_request(self):
                return None

            def reserve_job_capacity(self, count):
                self.count = count

        oversized = self.response()
        oversized["jobOpenings"] = oversized["jobOpenings"] * 51
        with self.assertRaises(SourceCollectionError) as payload_error:
            RemoteRocketshipAdapter(http=RecordingHttp(oversized), clock=lambda: now, quota=Quota()).collect(self.source)
        self.assertEqual(payload_error.exception.code, "quota_payload_exceeded")

    def test_live_checks_are_explicitly_credential_gated(self):
        self.assertEqual(RemoteRocketshipAdapter.live_check_status(), "credential-gated")
        self.assertEqual(Web3CareerAdapter.live_check_status(), "credential-gated")
        self.assertEqual(CryptoJobsListAdapter.live_check_status(), "credential-gated")
