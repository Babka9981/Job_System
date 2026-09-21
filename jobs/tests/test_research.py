import socket
import subprocess
import sys
import time
import traceback
import base64
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from jobs.intelligence.fetch import FetchError, PublicFetcher
from jobs.intelligence.http_process import HTTPProcessError, run_http_exchange
from jobs.intelligence.research import research
from jobs.intelligence.search import (
    BraveSearchProvider,
    FixtureSearchProvider,
    SearchHit,
    SearchProviderError,
    SearchProviderUnavailable,
    TavilySearchProvider,
)
from jobs.intelligence.search.brave import _http_transport as brave_http_transport
from jobs.intelligence.search.tavily import _http_transport as tavily_http_transport
from jobs.models.models import Profile, ProfileFact, Research, UsageLedger, UsageReservation, Vacancy


class SearchProviderTests(TestCase):
    def test_provider_transport_failures_and_malformed_json_drop_sensitive_exception_chains(self):
        sentinel = "secret-key-and-response-body-sentinel"
        providers = (
            ("brave", brave_http_transport, {
                "url": BraveSearchProvider.endpoint, "api_key": sentinel, "params": {"q": "Acme"},
                "timeout": 1, "deadline": 100, "clock": lambda: 0,
            }),
            ("tavily", tavily_http_transport, {
                "url": TavilySearchProvider.endpoint, "api_key": sentinel, "payload": {"query": "Acme"},
                "timeout": 1, "deadline": 100, "clock": lambda: 0,
            }),
        )
        for name, transport, kwargs in providers:
            for failure in ("transport", "json"):
                with self.subTest(provider=name, failure=failure):
                    if failure == "transport":
                        try:
                            raise RuntimeError(sentinel)
                        except RuntimeError as private_error:
                            upstream = HTTPProcessError("network_error")
                            upstream.__cause__ = private_error
                        effect = mock.patch(
                            f"jobs.intelligence.search.{name}.run_http_exchange", side_effect=upstream,
                        )
                    else:
                        effect = mock.patch(
                            f"jobs.intelligence.search.{name}.run_http_exchange",
                            return_value={"status": 200, "headers": {}, "body": sentinel.encode()},
                        )
                    with effect, self.assertRaises(SearchProviderError) as caught:
                        transport(**kwargs)
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertIsNone(caught.exception.__context__)
                    self.assertNotIn(sentinel, "".join(traceback.format_exception(caught.exception)))

    def test_brave_llm_context_uses_get_contract_and_returns_candidate_urls_only(self):
        captured = {}

        def transport(*, url, api_key, params, timeout, deadline, clock):
            captured.update(url=url, api_key=api_key, params=params)
            return {"grounding": {"generic": [{
                "url": "https://acme.example/about", "title": "About",
                "snippets": ["candidate context, not evidence"],
            }]}}

        result = BraveSearchProvider(api_key="local-test", transport=transport).search(
            "Acme product manager", deadline=100, clock=lambda: 0,
        )
        self.assertEqual(captured["url"], "https://api.search.brave.com/res/v1/llm/context")
        self.assertEqual(captured["api_key"], "local-test")
        self.assertEqual(captured["params"]["maximum_number_of_urls"], 5)
        self.assertEqual(result[0].snippet, "candidate context, not evidence")

    def test_brave_falls_back_to_web_search_only_for_explicit_plan_error(self):
        calls = []

        def transport(*, url, api_key, params, timeout, deadline, clock):
            calls.append({"url": url, "params": params, "deadline": deadline})
            if url == BraveSearchProvider.endpoint:
                raise SearchProviderError("option_not_in_plan", "safe plan error")
            return {"web": {"results": [{
                "url": "https://acme.example/about",
                "title": "About Acme",
                "description": "candidate web snippet, not evidence",
                "page_age": "2026-09-20T12:00:00Z",
            }]}}

        result = BraveSearchProvider(api_key="local-test", transport=transport).search(
            "Acme product manager", max_results=3, deadline=100, clock=lambda: 0,
        )

        self.assertEqual([call["url"] for call in calls], [
            "https://api.search.brave.com/res/v1/llm/context",
            "https://api.search.brave.com/res/v1/web/search",
        ])
        self.assertEqual([call["deadline"] for call in calls], [100, 100])
        self.assertEqual(calls[1]["params"], {"q": "Acme product manager", "count": 3, "safesearch": "strict"})
        self.assertEqual(result, [SearchHit(
            url="https://acme.example/about",
            title="About Acme",
            snippet="candidate web snippet, not evidence",
            published_at="2026-09-20T12:00:00Z",
        )])

    def test_brave_fallback_cannot_return_at_shared_deadline(self):
        class Clock:
            value = 0

            def __call__(self):
                return self.value

        clock = Clock()
        calls = []

        def transport(**kwargs):
            calls.append(kwargs["url"])
            if kwargs["url"] == BraveSearchProvider.endpoint:
                clock.value = 9
                raise SearchProviderError("option_not_in_plan", "safe plan error")
            clock.value = 10
            return {"web": {"results": []}}

        provider = BraveSearchProvider(api_key="local-test", transport=transport)
        with self.assertRaises(SearchProviderError) as caught:
            provider.search("Acme", deadline=10, clock=clock)

        self.assertEqual(caught.exception.code, "deadline_exceeded")
        self.assertEqual(calls, [provider.endpoint, provider.web_endpoint])

    def test_brave_does_not_fallback_for_other_4xx_or_quota(self):
        failures = (
            SearchProviderError("provider_error", "safe 4xx"),
            SearchProviderUnavailable("quota_exceeded", "safe quota"),
        )
        for failure in failures:
            with self.subTest(code=failure.code):
                transport = mock.Mock(side_effect=failure)
                provider = BraveSearchProvider(api_key="local-test", transport=transport)

                with self.assertRaises(SearchProviderError) as caught:
                    provider.search("Acme", deadline=100, clock=lambda: 0)

                self.assertEqual(caught.exception.code, failure.code)
                self.assertEqual(transport.call_count, 1)
                self.assertEqual(transport.call_args.kwargs["url"], provider.endpoint)

    def test_brave_rejects_invalid_response_and_classifies_quota_without_leaking_key(self):
        provider = BraveSearchProvider(
            api_key="brave-secret-sentinel",
            transport=lambda **kwargs: {"grounding": {"generic": "not-a-list"}},
        )
        with self.assertRaises(SearchProviderError) as invalid:
            provider.search("Acme", deadline=100, clock=lambda: 0)
        self.assertEqual(invalid.exception.code, "invalid_response")
        with mock.patch(
            "jobs.intelligence.search.brave.run_http_exchange",
            return_value={"status": 429, "headers": {}, "body": b"{}"},
        ) as exchange:
            with self.assertRaises(SearchProviderUnavailable) as quota:
                brave_http_transport(
                    url=provider.endpoint, api_key="brave-secret-sentinel", params={"q": "Acme"},
                    timeout=1, deadline=100, clock=lambda: 0,
                )
        self.assertEqual(quota.exception.code, "quota_exceeded")
        self.assertNotIn("brave-secret-sentinel", str(quota.exception))
        request = exchange.call_args.args[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["headers"]["X-Subscription-Token"], "brave-secret-sentinel")
        self.assertIn("?q=Acme", request["url"])

    def test_brave_http_transport_exposes_only_safe_explicit_plan_code(self):
        response_body = b'{"error":{"code":"OPTION_NOT_IN_PLAN","detail":"private response sentinel"}}'
        with mock.patch(
            "jobs.intelligence.search.brave.run_http_exchange",
            return_value={"status": 400, "headers": {}, "body": response_body},
        ):
            with self.assertRaises(SearchProviderError) as caught:
                brave_http_transport(
                    url=BraveSearchProvider.endpoint,
                    api_key="private-key-sentinel",
                    params={"q": "Acme"},
                    timeout=1,
                    deadline=100,
                    clock=lambda: 0,
                )

        self.assertEqual(caught.exception.code, "option_not_in_plan")
        formatted = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn("private response sentinel", formatted)
        self.assertNotIn("private-key-sentinel", formatted)

    def test_brave_default_transport_does_not_fallback_for_other_structured_4xx(self):
        response_body = b'{"error":{"code":"INVALID_REQUEST","detail":"private response sentinel"}}'
        provider = BraveSearchProvider(api_key="private-key-sentinel")
        with mock.patch(
            "jobs.intelligence.search.brave.run_http_exchange",
            return_value={"status": 400, "headers": {}, "body": response_body},
        ) as exchange:
            with self.assertRaises(SearchProviderError) as caught:
                provider.search("Acme", deadline=100, clock=lambda: 0)

        self.assertEqual(caught.exception.code, "provider_error")
        self.assertEqual(exchange.call_count, 1)
        formatted = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn("private response sentinel", formatted)
        self.assertNotIn("private-key-sentinel", formatted)

    def test_brave_default_transport_caps_plan_fallback_at_two_requests(self):
        responses = [
            {"status": 400, "headers": {}, "body": b'{"error":{"code":"OPTION_NOT_IN_PLAN"}}'},
            {"status": 400, "headers": {}, "body": b'{"error":{"code":"OPTION_NOT_IN_PLAN"}}'},
        ]
        provider = BraveSearchProvider(api_key="private-key-sentinel")
        with mock.patch(
            "jobs.intelligence.search.brave.run_http_exchange", side_effect=responses,
        ) as exchange:
            with self.assertRaises(SearchProviderError) as caught:
                provider.search("Acme", deadline=100, clock=lambda: 0)

        self.assertEqual(caught.exception.code, "option_not_in_plan")
        self.assertEqual(exchange.call_count, 2)
        request_urls = [call.args[0]["url"] for call in exchange.call_args_list]
        self.assertIn("/res/v1/llm/context?", request_urls[0])
        self.assertIn("/res/v1/web/search?", request_urls[1])

    def test_tavily_is_unavailable_without_local_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SearchProviderUnavailable) as error:
                TavilySearchProvider().search("Acme product manager")
        self.assertEqual(error.exception.code, "missing_api_key")

    def test_tavily_uses_bounded_candidate_only_payload(self):
        captured = {}

        def transport(*, url, api_key, payload, timeout, deadline, clock):
            captured.update(url=url, api_key=api_key, payload=payload, timeout=timeout)
            return {"results": [{"url": "https://acme.example/about", "title": "About", "content": "candidate snippet"}]}

        result = TavilySearchProvider(api_key="local-test", transport=transport).search(
            "Acme product manager", max_results=5, deadline=100, clock=lambda: 0,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(captured["payload"]["search_depth"], "basic")
        self.assertFalse(captured["payload"]["include_answer"])
        self.assertFalse(captured["payload"]["include_raw_content"])
        self.assertFalse(captured["payload"]["auto_parameters"])
        self.assertTrue(captured["payload"]["include_usage"])
        self.assertLessEqual(captured["payload"]["max_results"], 5)

    def test_production_search_requires_known_price_and_positive_daily_budget(self):
        owner = get_user_model().objects.create_user("budget-owner")
        profile = Profile.objects.create(owner=owner, preferences={"daily_budget_usd": "1.00"})
        vacancy = Vacancy.objects.create(owner=owner, title="PM", company="Acme", company_domain="acme.example")
        provider = TavilySearchProvider(api_key="local-test", transport=mock.Mock(return_value={"results": []}))
        with mock.patch.dict("os.environ", {}, clear=True):
            result = research(vacancy, domain="acme.example", provider=provider, fetcher=mock.Mock())
        self.assertEqual(result.status, Research.Status.UNAVAILABLE)
        self.assertIn("budget_unavailable", result.coverage["errors"])
        self.assertEqual(result.coverage["provider_attempts"], [
            {"query": 1, "provider": "tavily", "outcome": "budget_unavailable"},
        ])
        self.assertIn("Tavily: цена или бюджет не настроены", result.coverage["progress"])
        self.assertFalse(provider.transport.called)
        self.assertFalse(UsageLedger.objects.filter(owner=owner).exists())

    def test_unexpected_search_provider_failure_marks_reserved_cost_pending(self):
        owner = get_user_model().objects.create_user("unexpected-provider-owner")
        profile = Profile.objects.create(owner=owner, preferences={"daily_budget_usd": "1.00"})
        vacancy = Vacancy.objects.create(
            owner=owner, title="PM", company="Acme", company_domain="acme.example"
        )

        def broken_transport(*, url, api_key, payload, timeout, deadline, clock):
            raise RuntimeError("provider exploded with an unknown failure")

        provider = TavilySearchProvider(api_key="local-test", transport=broken_transport)
        with mock.patch.dict("os.environ", {"TAVILY_COST_PER_CREDIT_USD": "0.01"}, clear=False):
            result = research(vacancy, domain="acme.example", profile=profile, provider=provider)

        self.assertEqual(result.status, Research.Status.UNAVAILABLE)
        self.assertIn("provider_error", result.coverage["errors"])
        self.assertEqual(UsageReservation.objects.get(owner=owner).status, "pending")

    def test_search_settlement_failure_marks_reserved_cost_pending(self):
        owner = get_user_model().objects.create_user("settlement-failure-owner")
        profile = Profile.objects.create(owner=owner, preferences={"daily_budget_usd": "1.00"})
        vacancy = Vacancy.objects.create(
            owner=owner, title="PM", company="Acme", company_domain="acme.example"
        )

        def successful_transport(*, url, api_key, payload, timeout, deadline, clock):
            return {"results": []}

        provider = TavilySearchProvider(api_key="local-test", transport=successful_transport)
        with mock.patch.dict("os.environ", {"TAVILY_COST_PER_CREDIT_USD": "0.01"}, clear=False):
            with mock.patch(
                "jobs.intelligence.research.service.settle_usage",
                side_effect=RuntimeError("ledger unavailable"),
            ):
                result = research(vacancy, domain="acme.example", profile=profile, provider=provider)

        self.assertEqual(result.status, Research.Status.UNAVAILABLE)
        self.assertIn("provider_error", result.coverage["errors"])
        self.assertEqual(UsageReservation.objects.get(owner=owner).status, "pending")

    def test_default_tavily_transport_kills_and_reaps_at_deadline_without_secret_in_command(self):
        from jobs.intelligence.search import SearchProviderError

        captured = {}
        class Process:
            killed = False
            reaped = False
            returncode = None
            def communicate(self, input=None, timeout=None):
                if not self.killed:
                    captured["stdin"] = input
                    raise subprocess.TimeoutExpired("http-worker", timeout)
                self.reaped = True
                self.returncode = -9
                return (b"", b"")
            def kill(self): self.killed = True
        process = Process()
        def factory(args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs["env"]
            return process
        ambient_secrets = {
            "DJANGO_SECRET_KEY": "django-secret-sentinel",
            "TELEGRAM_API_HASH": "telegram-secret-sentinel",
            "WEB3_CAREER_API_TOKEN": "web3-secret-sentinel",
            "DATABASE_URL": "database-secret-sentinel",
            "FUTURE_PROVIDER_SECRET": "future-secret-sentinel",
            "HTTPS_PROXY": "http://proxy-secret-sentinel.example",
        }
        with mock.patch.dict("os.environ", ambient_secrets, clear=False):
            with self.assertRaises(SearchProviderError) as caught:
                tavily_http_transport(
                    url="https://api.tavily.com/search", api_key="tavily-secret-sentinel", payload={},
                    timeout=1, deadline=time.monotonic() + 0.01, clock=time.monotonic,
                    process_factory=factory,
                )
        self.assertEqual(caught.exception.code, "deadline_exceeded")
        self.assertTrue(process.killed)
        self.assertTrue(process.reaped)
        self.assertNotIn("tavily-secret-sentinel", repr(captured["args"]))
        self.assertNotIn("tavily-secret-sentinel", repr(captured["env"]))
        for sentinel in ambient_secrets.values():
            self.assertNotIn(sentinel, repr(captured["env"]))
        self.assertIn(b"tavily-secret-sentinel", captured["stdin"])


class PublicFetcherTests(TestCase):
    def test_blocks_ipv4_ipv6_private_and_reserved_destinations(self):
        for address in ("127.0.0.1", "10.1.2.3", "169.254.1.1", "192.0.2.1", "224.0.0.1", "239.255.255.250", "::1", "fc00::1", "fe80::1", "ff02::1", "2001:db8::1"):
            with self.subTest(address=address):
                resolver = lambda host, port, address=address, **kwargs: [(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
                with self.assertRaises(FetchError):
                    PublicFetcher(resolver=resolver, transport=mock.Mock()).fetch("https://example.com/page")

    def test_rejects_mixed_dns_and_passes_pinned_public_addresses(self):
        mixed = lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ]
        with self.assertRaises(FetchError):
            PublicFetcher(resolver=mixed, transport=mock.Mock()).fetch("https://example.com")

        transport = mock.Mock(return_value={"status": 200, "headers": {"content-type": "text/html"}, "body": b"safe page"})
        public = lambda host, port, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        PublicFetcher(resolver=public, transport=transport).fetch("https://example.com")
        self.assertEqual(transport.call_args.kwargs["approved_ips"], ("93.184.216.34",))
        self.assertNotIn("Authorization", transport.call_args.kwargs["headers"])

    def test_revalidates_every_redirect_and_blocks_rebinding(self):
        answers = iter([
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        ])
        transport = mock.Mock(return_value={"status": 302, "headers": {"location": "https://redirect.example/secret"}, "body": b""})
        with self.assertRaises(FetchError):
            PublicFetcher(resolver=lambda host, port, **kwargs: next(answers), transport=transport).fetch("https://example.com")
        self.assertEqual(transport.call_count, 1)

    def test_default_child_gets_only_parent_approved_ip_and_original_sni_host(self):
        captured = {}
        class Process:
            returncode = 0
            def communicate(self, input=None, timeout=None):
                captured["request"] = json.loads(input)
                response = {
                    "ok": True, "status": 200,
                    "headers": {"content-type": "text/plain"},
                    "body_b64": base64.b64encode(b"safe").decode("ascii"),
                }
                return (json.dumps(response).encode(), b"")
            def poll(self): return 0
        public = lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        ]
        result = PublicFetcher(
            resolver=public, http_process_factory=lambda *args, **kwargs: Process(),
        ).fetch("https://example.com/company")
        self.assertEqual(result["content"], "safe")
        self.assertEqual(captured["request"]["url"], "https://example.com/company")
        self.assertEqual(captured["request"]["pinned_ips"], ["93.184.216.34"])

    def test_enforces_redirect_bytes_content_type_and_timeout_bounds(self):
        public = lambda host, port, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        with self.assertRaises(FetchError):
            PublicFetcher(resolver=public, transport=mock.Mock(return_value={"status": 200, "headers": {"content-type": "application/octet-stream"}, "body": b"x"})).fetch("https://example.com")
        with self.assertRaises(FetchError):
            PublicFetcher(max_bytes=4, resolver=public, transport=mock.Mock(return_value={"status": 200, "headers": {"content-type": "text/plain"}, "body": b"12345"})).fetch("https://example.com")
        with self.assertRaises(FetchError):
            PublicFetcher(max_redirects=0, resolver=public, transport=mock.Mock(return_value={"status": 302, "headers": {"location": "https://next.example"}, "body": b""})).fetch("https://example.com")
        timeout_transport = mock.Mock(side_effect=TimeoutError)
        with self.assertRaises(FetchError):
            PublicFetcher(timeout=1, resolver=public, transport=timeout_transport).fetch("https://example.com")

    def test_absolute_deadline_includes_dns_and_transport(self):
        class Clock:
            value = 0.0
            def __call__(self):
                return self.value

        clock = Clock()
        def slow_dns(host, port, **kwargs):
            clock.value = 2.0
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

        with self.assertRaises(FetchError) as error:
            PublicFetcher(timeout=1, resolver=slow_dns, transport=mock.Mock(), clock=clock).fetch("https://example.com")
        self.assertEqual(error.exception.code, "timeout")

        clock.value = 0.0
        public = lambda host, port, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        def slow_stream(**kwargs):
            clock.value = 2.0
            return {"status": 200, "headers": {"content-type": "text/plain"}, "body": b"late"}
        with self.assertRaises(FetchError) as error:
            PublicFetcher(timeout=1, resolver=public, transport=slow_stream, clock=clock).fetch("https://example.com")
        self.assertEqual(error.exception.code, "timeout")

    def test_default_dns_process_is_killed_and_reaped_on_timeout(self):
        import subprocess

        class Process:
            killed = False
            reaped = False
            returncode = None

            def communicate(self, timeout=None):
                if not self.killed:
                    raise subprocess.TimeoutExpired("dns", timeout)
                self.reaped = True
                self.returncode = -9
                return ("", "")

            def kill(self):
                self.killed = True

        process = Process()
        captured = {}
        def factory(*args, **kwargs):
            captured["env"] = kwargs["env"]
            return process
        fetcher = PublicFetcher(
            timeout=0.1,
            dns_process_factory=factory,
            transport=mock.Mock(),
        )
        ambient_secrets = {
            "DJANGO_SECRET_KEY": "dns-django-secret-sentinel",
            "TELEGRAM_API_HASH": "dns-telegram-secret-sentinel",
            "CRYPTOJOBS_LIST_API_KEY": "dns-keyed-secret-sentinel",
            "DATABASE_URL": "dns-database-secret-sentinel",
            "FUTURE_PROVIDER_SECRET": "dns-future-secret-sentinel",
            "HTTP_PROXY": "http://dns-proxy-secret-sentinel.example",
        }
        with mock.patch.dict("os.environ", ambient_secrets, clear=False):
            with self.assertRaises(FetchError):
                fetcher.fetch("https://example.com")
        self.assertTrue(process.killed)
        self.assertTrue(process.reaped)
        for sentinel in ambient_secrets.values():
            self.assertNotIn(sentinel, repr(captured["env"]))


class HardDeadlineProcessTests(TestCase):
    @staticmethod
    def _worker_process(response):
        class Process:
            returncode = 0

            def communicate(self, input=None, timeout=None):
                return json.dumps(response).encode("utf-8"), b""

        return Process()

    def test_worker_envelope_enforces_decoded_body_limit_at_base64_ceiling_collision(self):
        exact = base64.b64encode(b"x" * 5_000_000).decode("ascii")
        oversized = base64.b64encode(b"x" * 5_000_001).decode("ascii")
        self.assertEqual(len(exact), len(oversized))

        accepted = run_http_exchange(
            {"url": "https://example.com", "method": "GET", "max_bytes": 5_000_000},
            deadline=time.monotonic() + 1,
            process_factory=lambda *args, **kwargs: self._worker_process({
                "ok": True, "status": 200, "headers": {}, "body_b64": exact,
            }),
        )
        self.assertEqual(accepted["body_b64"], exact)

        with self.assertRaises(HTTPProcessError) as caught:
            run_http_exchange(
                {"url": "https://example.com", "method": "GET", "max_bytes": 5_000_000},
                deadline=time.monotonic() + 1,
                process_factory=lambda *args, **kwargs: self._worker_process({
                    "ok": True, "status": 200, "headers": {}, "body_b64": oversized,
                }),
            )
        self.assertEqual(caught.exception.code, "invalid_response")

    def test_worker_envelope_rejects_malformed_fields(self):
        cases = (
            {"status": True, "headers": {}, "body_b64": ""},
            {"status": 99, "headers": {}, "body_b64": ""},
            {"status": 600, "headers": {}, "body_b64": ""},
            {"status": "200", "headers": {}, "body_b64": ""},
            {"status": 200, "headers": [], "body_b64": ""},
            {"status": 200, "headers": {str(index): "x" for index in range(201)}, "body_b64": ""},
            {"status": 200, "headers": {"x": 1}, "body_b64": ""},
            {"status": 200, "headers": {"x": "x" * 256_001}, "body_b64": ""},
            {"status": 200, "headers": {}, "body_b64": "not-valid-base64!"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(HTTPProcessError) as caught:
                    run_http_exchange(
                        {"url": "https://example.com", "method": "GET", "max_bytes": 16},
                        deadline=time.monotonic() + 1,
                        process_factory=lambda *args, payload=payload, **kwargs: self._worker_process({
                            "ok": True, **payload,
                        }),
                    )
                self.assertEqual(caught.exception.code, "invalid_response")

    def test_worker_envelope_rejects_oversized_headers(self):
        class Process:
            returncode = 0

            def communicate(self, input=None, timeout=None):
                response = {
                    "ok": True,
                    "status": 200,
                    "headers": {"x-oversized": "x" * 300_000},
                    "body_b64": "",
                }
                return json.dumps(response).encode("utf-8"), b""

        with self.assertRaises(HTTPProcessError) as caught:
            run_http_exchange(
                {"url": "https://example.com", "method": "GET", "max_bytes": 16},
                deadline=time.monotonic() + 1,
                process_factory=lambda *args, **kwargs: Process(),
            )

        self.assertEqual(caught.exception.code, "invalid_response")

    def _server(self, mode):
        script = (
            "import socket,sys,time; "
            "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(1); "
            "print(s.getsockname()[1],flush=True); c,_=s.accept(); c.recv(65536); "
            + ("time.sleep(5)" if mode == "headers" else
               "c.sendall(b'HTTP/1.1 200 OK\\r\\nContent-Type: text/plain\\r\\nContent-Length: 100\\r\\n\\r\\na'); "
               "[(time.sleep(.1),c.sendall(b'a')) for _ in range(99)]")
        )
        server = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
        port = int(server.stdout.readline().strip())
        return server, port

    def test_real_slow_headers_and_drip_are_bounded_and_workers_reaped(self):
        for mode in ("headers", "drip"):
            with self.subTest(mode=mode):
                server, port = self._server(mode)
                workers = []
                def factory(*args, **kwargs):
                    process = subprocess.Popen(*args, **kwargs)
                    workers.append(process)
                    return process
                started = time.monotonic()
                try:
                    with self.assertRaises(HTTPProcessError) as caught:
                        run_http_exchange({
                            "url": f"http://localhost:{port}/",
                            "method": "GET", "max_bytes": 1024, "socket_timeout": 10,
                        }, deadline=time.monotonic() + 0.25, process_factory=factory)
                    self.assertEqual(caught.exception.code, "deadline_exceeded")
                    self.assertLess(time.monotonic() - started, 1.5)
                    self.assertTrue(workers)
                    self.assertIsNotNone(workers[0].poll())
                finally:
                    if server.poll() is None:
                        server.kill()
                    server.communicate()

    def test_real_slow_dns_process_is_bounded_killed_and_reaped(self):
        processes = []
        captured = {}
        def slow_dns_factory(args, **kwargs):
            captured["worker_args"] = args
            captured["worker_env"] = kwargs["env"]
            process = subprocess.Popen(
                [sys.executable, "-c", "import socket,time; socket.getaddrinfo=lambda *a,**k: time.sleep(5); socket.getaddrinfo('slow.invalid',443)"],
                stdin=kwargs["stdin"], stdout=kwargs["stdout"], stderr=kwargs["stderr"],
                env=kwargs["env"], close_fds=kwargs["close_fds"],
            )
            processes.append(process)
            return process
        started = time.monotonic()
        with self.assertRaises(HTTPProcessError) as caught:
            run_http_exchange({
                "url": "https://slow.invalid/", "method": "POST",
                "headers": {"Authorization": "Bearer secret-sentinel"},
                "body_b64": "", "max_bytes": 16,
            }, deadline=time.monotonic() + 0.25, process_factory=slow_dns_factory)
        self.assertEqual(caught.exception.code, "deadline_exceeded")
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIsNotNone(processes[0].poll())
        self.assertNotIn("secret-sentinel", repr(captured["worker_args"]))
        self.assertNotIn("secret-sentinel", repr(captured["worker_env"]))


class ResearchServiceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="secret")
        self.profile = Profile.objects.create(owner=self.owner, version=3, confirmed_version=3)
        ProfileFact.objects.create(profile=self.profile, text="Led a B2B fintech launch", kind="case", source="CV", page=2, profile_version=3, confirmed=True)
        self.vacancy = Vacancy.objects.create(owner=self.owner, title="Product Manager", company="Acme", role="Product Manager", description="Build the product")
        self.hits = [
            SearchHit("https://acme.example/about", "Acme About", "snippet is candidate only", published_at=None),
            SearchHit("https://news.example/acme-launch", "Acme launch", "another snippet", published_at="2026-08-01"),
        ]
        self.provider = FixtureSearchProvider([self.hits, self.hits, self.hits])
        self.fetcher = mock.Mock()
        self.fetcher.fetch.side_effect = [
            {"url": "https://acme.example/about", "content": "Acme builds payment infrastructure for global merchants.", "content_type": "text/html", "checked_at": timezone.now()},
            {"url": "https://news.example/acme-launch", "content": "Acme launched its merchant analytics product in August 2026. Details: https://acme.example/news.", "content_type": "text/html", "checked_at": timezone.now()},
        ]

    class ValidGateway:
        def structured(self, **kwargs):
            pages = __import__("json").loads(kwargs["input_text"])["pages"]
            return {
                "facts": [{"page_index": i, "claim": page["content"].split(".")[0], "passage": page["content"], "event_date": ""} for i, page in enumerate(pages)],
                "conflicts": [],
                "hypotheses": [],
            }

    def test_missing_domain_with_ambiguous_search_results_needs_domain_without_fetch(self):
        provider = FixtureSearchProvider([[SearchHit("https://acme-one.example", "Acme", ""), SearchHit("https://acme-two.example", "Acme", "")]])
        result = research(self.vacancy, domain="", role="Product Manager", provider=provider, fetcher=self.fetcher)
        self.assertEqual(result.status, Research.Status.NEEDS_DOMAIN)
        self.assertFalse(self.fetcher.fetch.called)

    def test_single_candidate_host_does_not_become_verified_domain(self):
        provider = FixtureSearchProvider([[SearchHit("https://agency.example/acme", "Acme", "")]])
        result = research(self.vacancy, domain="", role="Product Manager", provider=provider, fetcher=self.fetcher)
        self.assertEqual(result.status, Research.Status.NEEDS_DOMAIN)
        self.assertEqual(result.domain, "")
        self.assertFalse(self.fetcher.fetch.called)

    def test_auto_falls_back_on_explicit_quota_and_keeps_actual_page_as_evidence(self):
        self.profile.preferences = {
            "daily_budget_usd": "1.00", "search_provider": "auto", "openai_model": "gpt-5.6-luna",
        }
        self.profile.save(update_fields=["preferences"])
        calls = {"tavily": 0, "brave": 0}

        def tavily_transport(**kwargs):
            calls["tavily"] += 1
            raise SearchProviderUnavailable("quota_exceeded", "quota")

        def brave_transport(**kwargs):
            calls["brave"] += 1
            return {"grounding": {"generic": [{
                "url": "https://acme.example/about", "title": "About",
                "snippets": ["POISON SNIPPET MUST NOT BECOME EVIDENCE"],
            }]}}

        tavily = TavilySearchProvider(
            api_key="tavily-test",
            transport=tavily_transport,
        )
        brave = BraveSearchProvider(
            api_key="brave-test",
            transport=brave_transport,
        )
        fetcher = mock.Mock()
        fetcher.fetch.return_value = {
            "url": "https://acme.example/about",
            "content": "Acme builds payment infrastructure for global merchants.",
            "checked_at": timezone.now(),
        }
        with mock.patch("jobs.intelligence.research.service.TavilySearchProvider", return_value=tavily), mock.patch(
            "jobs.intelligence.research.service.BraveSearchProvider", return_value=brave,
        ), mock.patch.dict("os.environ", {
            "TAVILY_COST_PER_CREDIT_USD": "0.01", "BRAVE_LLM_CONTEXT_COST_USD": "0.02",
        }, clear=False):
            result = research(
                self.vacancy, domain="acme.example", profile=self.profile,
                fetcher=fetcher, gateway=self.ValidGateway(),
            )
        self.assertEqual(result.coverage["provider"], "auto")
        self.assertEqual(result.coverage["providers"], ["brave"])
        self.assertEqual(result.coverage["provider_attempts"][:2], [
            {"query": 1, "provider": "tavily", "outcome": "quota_exceeded"},
            {"query": 1, "provider": "brave", "outcome": "success"},
        ])
        self.assertIn("Tavily: квота исчерпана → Brave: успешно", result.coverage["progress"])
        self.assertTrue(result.facts)
        self.assertNotIn("POISON", result.facts[0]["passage"])
        self.assertEqual(calls, {"tavily": 3, "brave": 2})
        self.assertEqual(result.coverage["queries"], 5)
        self.assertEqual(len(result.coverage["provider_attempts"]), 5)
        self.assertEqual(UsageReservation.objects.filter(status="released").count(), 3)
        self.assertTrue(UsageLedger.objects.filter(metadata__provider="brave").exists())

    def test_brave_plan_fallback_respects_five_physical_request_cap(self):
        self.profile.preferences = {"daily_budget_usd": "1.00", "search_provider": "brave"}
        self.profile.save(update_fields=["preferences"])
        calls = []

        def transport(**kwargs):
            calls.append(kwargs["url"])
            if kwargs["url"] == BraveSearchProvider.endpoint:
                raise SearchProviderError("option_not_in_plan", "safe plan error")
            return {"web": {"results": [{
                "url": "https://acme.example/about",
                "title": "About",
                "description": "candidate only",
            }]}}

        provider = BraveSearchProvider(api_key="brave-test", transport=transport)
        fetcher = mock.Mock()
        fetcher.fetch.return_value = {
            "url": "https://acme.example/about",
            "content": "Acme builds payment infrastructure for global merchants.",
            "checked_at": timezone.now(),
        }
        with mock.patch.dict(
            "os.environ", {"BRAVE_LLM_CONTEXT_COST_USD": "0.01"}, clear=False,
        ):
            result = research(
                self.vacancy,
                domain="acme.example",
                profile=self.profile,
                provider=provider,
                fetcher=fetcher,
                gateway=self.ValidGateway(),
            )

        self.assertEqual(calls, [
            provider.endpoint, provider.web_endpoint,
            provider.endpoint, provider.web_endpoint,
            provider.endpoint,
        ])
        self.assertEqual(result.coverage["queries"], 5)
        self.assertEqual(result.coverage["provider_attempts"], [
            {"query": 1, "provider": "brave", "outcome": "option_not_in_plan"},
            {"query": 1, "provider": "brave", "outcome": "web_success"},
            {"query": 2, "provider": "brave", "outcome": "option_not_in_plan"},
            {"query": 2, "provider": "brave", "outcome": "web_success"},
            {"query": 3, "provider": "brave", "outcome": "option_not_in_plan"},
            {"query": 3, "provider": "brave", "outcome": "web_query_limit"},
        ])
        self.assertEqual(UsageLedger.objects.filter(metadata__provider="brave").count(), 5)

    def test_brave_plan_fallback_budget_denial_prevents_second_http_request(self):
        self.profile.preferences = {"daily_budget_usd": "0.01", "search_provider": "brave"}
        self.profile.save(update_fields=["preferences"])
        calls = []

        def transport(**kwargs):
            calls.append(kwargs["url"])
            if kwargs["url"] == BraveSearchProvider.endpoint:
                raise SearchProviderError("option_not_in_plan", "safe plan error")
            return {"web": {"results": []}}

        provider = BraveSearchProvider(api_key="brave-test", transport=transport)
        with mock.patch.dict(
            "os.environ", {"BRAVE_LLM_CONTEXT_COST_USD": "0.01"}, clear=False,
        ):
            result = research(
                self.vacancy,
                domain="acme.example",
                profile=self.profile,
                provider=provider,
                fetcher=mock.Mock(),
                gateway=self.ValidGateway(),
            )

        self.assertEqual(calls, [provider.endpoint])
        self.assertEqual(result.coverage["queries"], 1)
        self.assertEqual(result.coverage["provider_attempts"], [
            {"query": 1, "provider": "brave", "outcome": "option_not_in_plan"},
            {"query": 1, "provider": "brave", "outcome": "web_budget_pending"},
        ])
        self.assertEqual(UsageLedger.objects.filter(metadata__provider="brave").count(), 1)

    def test_brave_plan_fallback_records_web_request_failure(self):
        self.profile.preferences = {"daily_budget_usd": "1.00", "search_provider": "brave"}
        self.profile.save(update_fields=["preferences"])

        def transport(**kwargs):
            if kwargs["url"] == BraveSearchProvider.endpoint:
                raise SearchProviderError("option_not_in_plan", "safe plan error")
            raise SearchProviderUnavailable("quota_exceeded", "safe quota error")

        provider = BraveSearchProvider(api_key="brave-test", transport=transport)
        with mock.patch.dict(
            "os.environ", {"BRAVE_LLM_CONTEXT_COST_USD": "0.01"}, clear=False,
        ):
            result = research(
                self.vacancy,
                domain="acme.example",
                profile=self.profile,
                provider=provider,
                fetcher=mock.Mock(),
                gateway=self.ValidGateway(),
            )

        self.assertEqual(result.coverage["queries"], 2)
        self.assertEqual(result.coverage["provider_attempts"], [
            {"query": 1, "provider": "brave", "outcome": "option_not_in_plan"},
            {"query": 1, "provider": "brave", "outcome": "web_quota_exceeded"},
        ])
        self.assertEqual(UsageReservation.objects.filter(status="released").count(), 1)

    def test_needs_domain_keeps_fallback_attempts_errors_and_progress(self):
        self.profile.preferences = {"daily_budget_usd": "1.00", "search_provider": "auto"}
        self.profile.save(update_fields=["preferences"])
        tavily = TavilySearchProvider(
            api_key="tavily-test",
            transport=lambda **kwargs: (_ for _ in ()).throw(
                SearchProviderUnavailable("quota_exceeded", "quota")
            ),
        )
        brave = BraveSearchProvider(
            api_key="brave-test",
            transport=lambda **kwargs: {"grounding": {"generic": [
                {"url": "https://acme-one.example", "title": "One", "snippets": []},
                {"url": "https://acme-two.example", "title": "Two", "snippets": []},
            ]}},
        )
        with mock.patch("jobs.intelligence.research.service.TavilySearchProvider", return_value=tavily), mock.patch(
            "jobs.intelligence.research.service.BraveSearchProvider", return_value=brave,
        ), mock.patch.dict("os.environ", {
            "TAVILY_COST_PER_CREDIT_USD": "0.01", "BRAVE_LLM_CONTEXT_COST_USD": "0.02",
        }, clear=False):
            result = research(self.vacancy, domain="", profile=self.profile)
        self.assertEqual(result.status, Research.Status.NEEDS_DOMAIN)
        self.assertEqual(result.coverage["queries"], 5)
        self.assertIn("quota_exceeded", result.coverage["errors"])
        self.assertEqual(len(result.coverage["provider_attempts"]), 5)
        self.assertIn("Tavily: квота исчерпана → Brave: успешно", result.coverage["progress"])

    def test_auto_does_not_fallback_after_invalid_provider_response(self):
        self.profile.preferences = {"daily_budget_usd": "1.00", "search_provider": "auto"}
        self.profile.save(update_fields=["preferences"])
        tavily = TavilySearchProvider(api_key="tavily-test", transport=lambda **kwargs: {"broken": []})
        brave_transport = mock.Mock(return_value={"grounding": {"generic": []}})
        brave = BraveSearchProvider(api_key="brave-test", transport=brave_transport)
        with mock.patch("jobs.intelligence.research.service.TavilySearchProvider", return_value=tavily), mock.patch(
            "jobs.intelligence.research.service.BraveSearchProvider", return_value=brave,
        ), mock.patch.dict("os.environ", {"TAVILY_COST_PER_CREDIT_USD": "0.01"}, clear=False):
            result = research(self.vacancy, domain="acme.example", profile=self.profile)
        self.assertIn("invalid_response", result.coverage["errors"])
        self.assertFalse(brave_transport.called)

    def test_queries_are_bounded_and_never_include_profile_or_contact(self):
        self.vacancy.contact = "private.person@example.com"
        self.vacancy.save(update_fields=["contact"])
        result = research(self.vacancy, domain="acme.example", role="Product Manager", profile=self.profile, provider=self.provider, fetcher=self.fetcher, gateway=self.ValidGateway())
        self.assertIn(result.status, {Research.Status.COMPLETE, Research.Status.PARTIAL})
        self.assertLessEqual(len(self.provider.queries), 5)
        queries = " ".join(self.provider.queries).lower()
        self.assertIn("acme", queries)
        self.assertIn("product manager", queries)
        self.assertNotIn("private.person", queries)
        self.assertNotIn("fintech launch", queries)
        self.assertLessEqual(self.fetcher.fetch.call_count, 8)

    def test_facts_require_fetched_passage_url_and_dates_not_snippets(self):
        result = research(self.vacancy, domain="acme.example", role="Product Manager", profile=self.profile, provider=self.provider, fetcher=self.fetcher, gateway=self.ValidGateway())
        self.assertTrue(result.facts)
        for fact in result.facts:
            self.assertTrue(fact["passage"])
            self.assertTrue(fact["url"].startswith("https://"))
            self.assertTrue(fact["checked_at"])
            self.assertIn("event_date", fact)
            self.assertIsNone(fact["event_date"])
            if fact["url"].startswith("https://news.example"):
                self.assertIsNone(fact["publication_date"])
        self.assertNotIn("snippet is candidate only", str(result.facts))
        self.assertIn("conflicts", result.coverage)
        self.assertIn("hypotheses", result.coverage)

    def test_llm_treats_pages_as_data_and_rejects_invented_passages(self):
        class Gateway:
            def structured(inner_self, **kwargs):
                self.assertIn("недоверенные данные", kwargs["developer_prompt"])
                self.assertNotIn("Led a B2B", kwargs["input_text"])
                return {
                    "facts": [
                        {"page_index": 0, "claim": "Acme serves merchants", "passage": "Acme builds payment infrastructure for global merchants.", "event_date": "2026-08-01"},
                        {"page_index": 1, "claim": "Invented", "passage": "This text was never fetched", "event_date": ""},
                    ],
                    "conflicts": [],
                    "hypotheses": ["The role may support expansion"],
                }

        result = research(self.vacancy, domain="acme.example", role="Product Manager", profile=self.profile, provider=self.provider, fetcher=self.fetcher, gateway=Gateway())
        self.assertEqual([fact["text"] for fact in result.facts], ["Acme serves merchants"])
        self.assertIsNone(result.facts[0]["event_date"])
        self.assertEqual(result.coverage["hypotheses"], ["The role may support expansion"])

    def test_conflicted_claim_is_not_usable_fact(self):
        class Gateway(self.ValidGateway):
            def structured(inner_self, **kwargs):
                result = super().structured(**kwargs)
                result["conflicts"] = [{"summary": "Sources disagree", "fact_indices": [0]}]
                return result

        result = research(self.vacancy, domain="acme.example", role="Product Manager", profile=self.profile, provider=self.provider, fetcher=self.fetcher, gateway=Gateway())
        self.assertNotIn("payment infrastructure", str(result.facts))
        self.assertEqual(result.coverage["conflicts"][0]["fact_indices"], [0])

    def test_synthesis_failure_keeps_pages_as_sources_not_facts(self):
        class BrokenGateway:
            def structured(self, **kwargs):
                from jobs.intelligence.gateway import GatewayError
                raise GatewayError("provider_error", "failed")

        result = research(self.vacancy, domain="acme.example", role="Product Manager", provider=self.provider, fetcher=self.fetcher, gateway=BrokenGateway())
        self.assertEqual(result.status, Research.Status.PARTIAL)
        self.assertEqual(result.facts, [])
        self.assertEqual(len(result.sources), 2)

    def test_external_title_mention_without_body_identity_is_rejected(self):
        self.fetcher.fetch.side_effect = [
            {"url": "https://acme.example/about", "content": "Official product page", "content_type": "text/html", "checked_at": timezone.now()},
            {"url": "https://news.example/acme-launch", "content": "A different company launched a product.", "content_type": "text/html", "checked_at": timezone.now()},
        ]
        result = research(self.vacancy, domain="acme.example", provider=self.provider, fetcher=self.fetcher, gateway=self.ValidGateway())
        self.assertEqual(result.coverage["external_pages"], 0)

    def test_same_name_external_page_without_confirmed_domain_link_is_rejected(self):
        self.fetcher.fetch.side_effect = [
            {"url": "https://acme.example/about", "content": "Official Acme product page", "content_type": "text/html", "checked_at": timezone.now()},
            {"url": "https://news.example/acme-launch", "content": "Acme launched a payroll product for restaurants.", "content_type": "text/html", "checked_at": timezone.now()},
        ]

        result = research(
            self.vacancy, domain="acme.example", provider=self.provider,
            fetcher=self.fetcher, gateway=self.ValidGateway(),
        )

        self.assertEqual(result.coverage["external_pages"], 0)
        self.assertEqual(result.coverage["identity_rejected_pages"], 1)
        self.assertNotEqual(result.status, Research.Status.COMPLETE)
        self.assertNotIn("payroll", str(result.facts).casefold())

    def test_recruiting_agency_page_mentioning_company_without_domain_is_rejected(self):
        self.fetcher.fetch.side_effect = [
            {"url": "https://acme.example/about", "content": "Official Acme product page", "content_type": "text/html", "checked_at": timezone.now()},
            {"url": "https://agency.example/jobs/acme", "content": "RecruitCo is hiring for Acme. Apply through our agency.", "content_type": "text/html", "checked_at": timezone.now()},
        ]

        result = research(
            self.vacancy, domain="acme.example", provider=self.provider,
            fetcher=self.fetcher, gateway=self.ValidGateway(),
        )

        self.assertEqual(result.coverage["external_pages"], 0)
        self.assertEqual(result.coverage["identity_rejected_pages"], 1)
        self.assertNotEqual(result.status, Research.Status.COMPLETE)
        self.assertNotIn("RecruitCo", str(result.facts))

    def test_external_page_with_company_and_confirmed_domain_link_is_evidence(self):
        self.fetcher.fetch.side_effect = [
            {"url": "https://acme.example/about", "content": "Official Acme product page", "content_type": "text/html", "checked_at": timezone.now()},
            {
                "url": "https://news.example/acme-launch",
                "content": 'Acme launched merchant analytics. <a href="https://www.acme.example/news">Official announcement</a>',
                "content_type": "text/html",
                "checked_at": timezone.now(),
            },
        ]
        test_case = self

        class IdentityGateway(self.ValidGateway):
            def structured(inner_self, **kwargs):
                payload = json.loads(kwargs["input_text"])
                test_case.assertEqual(payload["confirmed_domain"], "acme.example")
                test_case.assertIn("confirmed_domain", kwargs["developer_prompt"])
                return super().structured(**kwargs)

        result = research(
            self.vacancy, domain="acme.example", provider=self.provider,
            fetcher=self.fetcher, gateway=IdentityGateway(),
        )

        self.assertEqual(result.coverage["external_pages"], 1)
        self.assertEqual(result.coverage["identity_rejected_pages"], 0)
        self.assertTrue(any(fact["source_type"] == "external" for fact in result.facts))

    def test_shallow_json_ld_confirmed_domain_url_is_identity_evidence(self):
        self.fetcher.fetch.side_effect = [
            {"url": "https://acme.example/about", "content": "Official Acme product page", "content_type": "text/html", "checked_at": timezone.now()},
            {
                "url": "https://news.example/acme-launch",
                "content": (
                    "<p>Acme launched merchant analytics.</p>"
                    '<script type="application/ld+json">'
                    '{"@type":"Organization","name":"Acme","url":"https://www.acme.example/news"}'
                    "</script>"
                ),
                "content_type": "text/html",
                "checked_at": timezone.now(),
            },
        ]

        result = research(
            self.vacancy, domain="acme.example", provider=self.provider,
            fetcher=self.fetcher, gateway=self.ValidGateway(),
        )

        self.assertEqual(result.coverage["external_pages"], 1)
        self.assertEqual(result.coverage["identity_rejected_pages"], 0)

    def test_untrusted_json_ld_is_bounded_and_rejected_without_raising(self):
        deep_json = '{"child":' * 1200 + '{"url":"https://acme.example"}' + "}" * 1200
        excessive_json = json.dumps({
            "sameAs": [f"https://unrelated-{index}.example" for index in range(1000)]
            + ["https://acme.example/too-late"]
        })
        probes = (
            '<script type="application/ld+json">{"url":</script>',
            f'<script type="application/ld+json">{deep_json}</script>',
            f'<script type="application/ld+json">{excessive_json}</script>',
        )
        for probe in probes:
            with self.subTest(kind=len(probe)):
                provider = FixtureSearchProvider([self.hits, self.hits, self.hits])
                fetcher = mock.Mock()
                fetcher.fetch.side_effect = [
                    {"url": "https://acme.example/about", "content": "Official Acme page", "content_type": "text/html", "checked_at": timezone.now()},
                    {
                        "url": "https://news.example/acme",
                        "content": f"<p>Acme coverage.</p>{probe}",
                        "content_type": "text/html",
                        "checked_at": timezone.now(),
                    },
                ]

                result = research(
                    self.vacancy, domain="acme.example", provider=provider,
                    fetcher=fetcher, gateway=self.ValidGateway(), refresh=True,
                )

                self.assertEqual(result.coverage["external_pages"], 0)
                self.assertEqual(result.coverage["identity_rejected_pages"], 1)
                self.assertIn(result.status, {Research.Status.PARTIAL, Research.Status.UNAVAILABLE})

    def test_oversized_identity_url_strings_are_not_evidence(self):
        long_url = "https://acme.example/" + "x" * 3000
        for content in (f'Acme coverage. <a href="{long_url}">link</a>', f"Acme coverage. {long_url}"):
            with self.subTest(structured="href" in content):
                provider = FixtureSearchProvider([self.hits, self.hits, self.hits])
                fetcher = mock.Mock()
                fetcher.fetch.side_effect = [
                    {"url": "https://acme.example/about", "content": "Official Acme page", "content_type": "text/html", "checked_at": timezone.now()},
                    {"url": "https://news.example/acme", "content": content, "content_type": "text/html", "checked_at": timezone.now()},
                ]

                result = research(
                    self.vacancy, domain="acme.example", provider=provider,
                    fetcher=fetcher, gateway=self.ValidGateway(), refresh=True,
                )

                self.assertEqual(result.coverage["external_pages"], 0)
                self.assertEqual(result.coverage["identity_rejected_pages"], 1)

    def test_domain_text_embedded_in_unsafe_url_positions_is_not_identity_evidence(self):
        probes = (
            "Acme profile: https://acme.example@evil.example/company",
            "Acme profile: https://evil.example/?next=https://acme.example/about",
            "Acme profile: https://evil.example/#https://acme.example/about",
            "Acme profile path: /companies/acme.example/about",
            "Acme profile: https://acme.example.evil/about",
        )
        for probe in probes:
            with self.subTest(probe=probe):
                provider = FixtureSearchProvider([self.hits, self.hits, self.hits])
                fetcher = mock.Mock()
                fetcher.fetch.side_effect = [
                    {"url": "https://acme.example/about", "content": "Official Acme page", "content_type": "text/html", "checked_at": timezone.now()},
                    {"url": "https://news.example/acme", "content": probe, "content_type": "text/html", "checked_at": timezone.now()},
                ]

                result = research(
                    self.vacancy, domain="acme.example", provider=provider,
                    fetcher=fetcher, gateway=self.ValidGateway(), refresh=True,
                )

                self.assertEqual(result.coverage["external_pages"], 0)
                self.assertEqual(result.coverage["identity_rejected_pages"], 1)
                self.assertNotEqual(result.status, Research.Status.COMPLETE)

    def test_synthesis_and_cache_use_resolved_company_domain_and_role_overrides(self):
        hits = [
            SearchHit("https://new.example/about", "New Co", "candidate"),
            SearchHit("https://news.example/new-co", "New Co news", "candidate"),
        ]
        provider = FixtureSearchProvider([hits, hits, hits])
        fetcher = mock.Mock()
        fetcher.fetch.side_effect = [
            {"url": "https://new.example/about", "content": "New Co operates a support platform.", "content_type": "text/html", "checked_at": timezone.now()},
            {"url": "https://news.example/new-co", "content": 'New Co expanded support tooling. <a href="https://docs.new.example">Docs</a>', "content_type": "text/html", "checked_at": timezone.now()},
        ]
        captured = {}

        class OverrideGateway(self.ValidGateway):
            def structured(inner_self, **kwargs):
                captured.update(json.loads(kwargs["input_text"]))
                return super().structured(**kwargs)

        result = research(
            self.vacancy,
            company="New Co",
            domain="new.example",
            role="Head of Support",
            provider=provider,
            fetcher=fetcher,
            gateway=OverrideGateway(),
        )

        self.assertEqual(captured["company"], "New Co")
        self.assertEqual(captured["confirmed_domain"], "new.example")
        self.assertEqual(captured["role"], "Head of Support")
        self.assertNotIn("Acme", json.dumps(captured))
        self.assertNotIn("Product Manager", json.dumps(captured))
        self.assertEqual((result.company, result.domain, result.role), ("New Co", "new.example", "Head of Support"))
        self.assertEqual(result.coverage["roles"], ["Head of Support"])

        cached = research(
            self.vacancy,
            company="New Co",
            domain="new.example",
            role="Head of Support",
            provider=mock.Mock(),
            fetcher=mock.Mock(),
        )
        self.assertEqual(cached.pk, result.pk)
        self.assertEqual(cached.coverage["roles"], ["Head of Support"])

    def test_shared_deadline_prevents_synthesis_after_slow_search(self):
        class Clock:
            value = 0.0
            def __call__(self): return self.value
        clock = Clock()
        class Provider(FixtureSearchProvider):
            def search(inner_self, query, **kwargs):
                clock.value = 91.0
                return self.hits
        gateway = mock.Mock()
        result = research(self.vacancy, domain="acme.example", provider=Provider([self.hits]), fetcher=self.fetcher, gateway=gateway, clock=clock)
        self.assertEqual(result.status, Research.Status.UNAVAILABLE)
        gateway.structured.assert_not_called()

    def test_synthesis_that_crosses_deadline_yields_no_facts(self):
        class Clock:
            value = 0.0
            def __call__(self): return self.value
        clock = Clock()
        class Gateway(self.ValidGateway):
            def structured(inner_self, **kwargs):
                clock.value = 91.0
                return super().structured(**kwargs)
        result = research(self.vacancy, domain="acme.example", provider=self.provider, fetcher=self.fetcher, gateway=Gateway(), clock=clock)
        self.assertEqual(result.status, Research.Status.PARTIAL)
        self.assertEqual(result.facts, [])
        self.assertEqual(result.coverage["synthesis_error"], "time_budget")

    def test_24h_cache_avoids_web_and_profile_version_only_reselects_cases(self):
        first = research(self.vacancy, domain="acme.example", role="Product Manager", profile=self.profile, provider=self.provider, fetcher=self.fetcher, gateway=self.ValidGateway())
        self.provider.queries.clear()
        self.fetcher.reset_mock()
        ProfileFact.objects.create(profile=self.profile, text="Scaled support SLA", kind="case", source="CV", page=3, profile_version=4, confirmed=True)
        self.profile.version = 4
        self.profile.confirmed_version = 4
        self.profile.save(update_fields=["version", "confirmed_version"])
        cached = research(self.vacancy, domain="acme.example", role="Product Manager", profile=self.profile, provider=self.provider, fetcher=self.fetcher)
        self.assertEqual(cached.pk, first.pk)
        self.assertEqual(cached.coverage["profile_version"], 4)
        self.assertFalse(self.provider.queries)
        self.assertFalse(self.fetcher.fetch.called)

    def test_case_selection_prefers_role_relevant_confirmed_fact(self):
        ProfileFact.objects.create(profile=self.profile, text="Designed brand campaigns", kind="case", source="CV", profile_version=3, confirmed=True)
        relevant = ProfileFact.objects.create(profile=self.profile, text="Owned product roadmap and discovery", kind="case", source="CV", profile_version=2, confirmed=True)
        result = research(self.vacancy, domain="acme.example", role="Product Manager", profile=self.profile, provider=self.provider, fetcher=self.fetcher, gateway=self.ValidGateway())
        self.assertEqual(result.coverage["cases"][0]["id"], relevant.pk)

    def test_refresh_and_domain_change_invalidate_cache(self):
        first = research(self.vacancy, domain="acme.example", role="Product Manager", profile=self.profile, provider=self.provider, fetcher=self.fetcher)
        self.fetcher.side_effect = None
        self.fetcher.fetch.side_effect = [
            {"url": "https://acme.example/about", "content": "Fresh official passage", "content_type": "text/html", "checked_at": timezone.now()},
            {"url": "https://news.example/acme-launch", "content": "Fresh external passage", "content_type": "text/html", "checked_at": timezone.now()},
        ]
        refreshed = research(self.vacancy, domain="acme.example", role="Product Manager", profile=self.profile, provider=self.provider, fetcher=self.fetcher, refresh=True)
        self.assertNotEqual(refreshed.pk, first.pk)

    def test_timeout_or_no_evidence_is_honest_partial_or_unavailable(self):
        self.fetcher.fetch.side_effect = FetchError("timeout", "page unavailable")
        result = research(self.vacancy, domain="acme.example", role="Product Manager", provider=self.provider, fetcher=self.fetcher)
        self.assertEqual(result.status, Research.Status.UNAVAILABLE)
        self.assertEqual(result.facts, [])
        self.assertGreater(result.coverage["failed_pages"], 0)
        self.assertNotIn("news", str(result.facts).lower())

    def test_expired_or_role_insufficient_cache_repeats_web(self):
        Research.objects.create(
            vacancy=self.vacancy, company="Acme", domain="acme.example", role="Designer",
            coverage={"roles": ["Designer"], "profile_version": 3}, status=Research.Status.COMPLETE,
            facts=[{"text": "old"}], sources=[], expires_at=timezone.now() + timedelta(hours=1),
        )
        research(self.vacancy, domain="acme.example", role="Product Manager", profile=self.profile, provider=self.provider, fetcher=self.fetcher)
        self.assertTrue(self.provider.queries)
