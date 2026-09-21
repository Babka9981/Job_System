from copy import deepcopy
from decimal import Decimal
import hashlib
import os
import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings

from jobs.intelligence.gateway import GatewayError, GatewayUnavailable
from jobs.matching.services import Assessment, evaluate
from jobs.models.models import Lease, Profile, Source, UsageReservation, Vacancy


def description_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class FixtureGateway:
    def __init__(self, data=None, error=None):
        self.data = data or {
            "role_match": "yes",
            "role_direction": "product",
            "industry_match": "yes",
            "reasons": ["Обязанности соответствуют управлению продуктом."],
            "questions": [],
        }
        self.error = error
        self.calls = []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.data


class MatchingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user("owner")
        self.profile = Profile.objects.create(
            owner=self.owner,
            version=3,
            criteria={
                "roles": ["Product Manager", "Project Manager", "Head of Support"],
                "industries": ["fintech", "crypto"],
                "salary": {"target": "5000", "currency": "USD", "period": "month", "basis": "gross"},
                "residence_country": "Portugal",
                "hiring_countries": ["Portugal"],
                "work_authorized_countries": ["Portugal"],
                "timezone": "Europe/Lisbon",
            },
            preferences={"daily_budget_usd": "2.50"},
        )
        self.source = Source.objects.create(
            owner=self.owner,
            slug="fixture",
            name="Fixture",
            kind="site",
            adapter="fixture",
            llm_permission=True,
        )

    def vacancy(self, **overrides):
        values = {
            "owner": self.owner,
            "title": "Senior Product Manager",
            "company": "Example",
            "description": "Own product discovery, roadmap and delivery for payment products.",
            "role": "Product Manager",
            "industry": "fintech",
            "work_arrangement": "remote",
            "salary_min": Decimal("60000"),
            "salary_max": Decimal("80000"),
            "salary_currency": "USD",
            "salary_period": "year",
            "salary_basis": "gross",
            "salary_component": "fixed",
            "country_restrictions": ["Portugal"],
            "timezone_restrictions": ["Europe/Lisbon"],
        }
        values.update(overrides)
        vacancy = Vacancy.objects.create(**values)
        vacancy.source_records.create(
            source=self.source,
            external_id=f"job-{vacancy.pk}",
            canonical_url=f"https://example.test/jobs/{vacancy.pk}",
            raw_hash=description_hash(vacancy.description),
            description_permission=True,
        )
        return vacancy

    def test_evaluate_returns_structured_fit_through_public_boundary(self):
        gateway = FixtureGateway()

        result = evaluate(self.vacancy(), self.profile.criteria, gateway=gateway)

        self.assertIsInstance(result, Assessment)
        self.assertEqual(result.status, "fit")
        self.assertEqual(result.role_direction, "product")
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(gateway.calls[0]["operation"], "vacancy_match")
        self.assertEqual(gateway.calls[0]["daily_limit"], "2.50")

    def test_obvious_backend_is_rejected_before_provider_can_turn_it_into_fit(self):
        gateway = FixtureGateway()
        vacancy = self.vacancy(
            title="Backend Engineer",
            role="Software Engineer",
            description="Implement Python services, database indexes and deployment tooling.",
        )

        result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertEqual(result.status, "reject")
        self.assertTrue(any("функц" in reason.lower() for reason in result.reasons))
        self.assertEqual(gateway.calls, [])

    def test_developer_platform_title_with_product_duties_reaches_llm(self):
        gateway = FixtureGateway()
        vacancy = self.vacancy(
            title="Product Manager - Developer Platform",
            role="Product Manager",
            description="Own the product vision, define customer outcomes and interview users.",
        )

        result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertEqual(result.status, "fit")
        self.assertEqual(len(gateway.calls), 1)

    def test_product_designer_title_with_product_management_duties_reaches_llm(self):
        gateway = FixtureGateway()
        vacancy = self.vacancy(
            title="Product Designer",
            role="Product Designer",
            description=(
                "Own product vision, lead user research and experiments, "
                "and coordinate feature launches around customer outcomes."
            ),
        )

        result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertEqual(result.status, "fit")
        self.assertEqual(len(gateway.calls), 1)

    def test_salesforce_product_owner_is_not_rejected_by_sales_substring(self):
        gateway = FixtureGateway()
        vacancy = self.vacancy(
            title="Salesforce Product Owner",
            role="Product Owner",
            description="Set quarterly goals and sequence customer problems with engineering.",
        )

        result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertEqual(result.status, "fit")
        self.assertEqual(len(gateway.calls), 1)

    def test_presales_product_owner_is_not_rejected_by_internal_sales_marker(self):
        gateway = FixtureGateway()
        vacancy = self.vacancy(
            title="Presales Product Owner",
            role="Product Owner",
            description="Set quarterly goals and sequence customer problems with engineering.",
        )

        result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertEqual(result.status, "fit")
        self.assertEqual(len(gateway.calls), 1)

    def test_unknown_team_lead_with_support_duties_reaches_llm_and_clarifies(self):
        gateway = FixtureGateway(data={
            "role_match": "unclear",
            "role_direction": "support",
            "industry_match": "yes",
            "reasons": ["Название неоднозначно, но есть управленческие обязанности."],
            "questions": [],
        })
        vacancy = self.vacancy(
            title="Team Lead",
            role="Team Lead",
            description="Manage support specialists, own SLA, escalations and service processes.",
        )

        result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertEqual(result.status, "clarify")
        self.assertEqual(result.role_direction, "support")
        self.assertEqual(len(gateway.calls), 1)

    def test_unclear_role_always_produces_a_question(self):
        gateway = FixtureGateway(data={
            "role_match": "unclear",
            "role_direction": "unknown",
            "industry_match": "yes",
            "reasons": ["Обязанности описаны недостаточно."],
            "questions": [f"Provider question {index}" for index in range(8)],
        })

        result = evaluate(self.vacancy(), self.profile.criteria, gateway=gateway)

        self.assertEqual(result.status, "clarify")
        self.assertTrue(any("обязанност" in question.lower() for question in result.questions))

    def test_revoked_permission_invalidates_cached_llm_assessment(self):
        vacancy = self.vacancy()
        first_gateway = FixtureGateway()
        self.assertEqual(evaluate(vacancy, self.profile.criteria, gateway=first_gateway).status, "fit")
        record = vacancy.source_records.get()
        record.description_permission = False
        record.save(update_fields=["description_permission"])
        second_gateway = FixtureGateway()

        result = evaluate(vacancy, self.profile.criteria, gateway=second_gateway)

        self.assertEqual(result.status, "clarify")
        self.assertEqual(result.error_code, "permission_required")
        self.assertEqual(second_gateway.calls, [])

    def test_comparable_salary_reject_keeps_hard_gate_reason(self):
        gateway = FixtureGateway(data={
            "role_match": "yes",
            "role_direction": "product",
            "industry_match": "yes",
            "reasons": ["Продуктовая стратегия.", "Работа с roadmap.", "Discovery процессов."],
            "questions": [],
        })
        vacancy = self.vacancy(salary_min=Decimal("40000"), salary_max=Decimal("50000"))

        result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertEqual(result.status, "reject")
        self.assertLessEqual(len(result.reasons), 3)
        self.assertTrue(any("ниже цели" in reason for reason in result.reasons))

    def test_adjacent_role_is_decided_by_duties_not_title_word(self):
        gateway = FixtureGateway(data={
            "role_match": "no",
            "role_direction": "other",
            "industry_match": "yes",
            "reasons": ["Обязанности относятся к performance marketing."],
            "questions": [],
        })
        vacancy = self.vacancy(
            title="Product Marketing Manager",
            role="Product Marketing",
            description="Run paid acquisition, conversion campaigns and sales enablement.",
        )

        result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertEqual(result.status, "reject")
        self.assertEqual(result.role_direction, "unknown")
        self.assertTrue(any("функц" in reason.lower() for reason in result.reasons))
        self.assertEqual(gateway.calls, [])

    def test_explicit_industry_mismatch_rejects_and_unknown_clarifies(self):
        vacancy = self.vacancy(industry="healthcare")
        mismatch = FixtureGateway(data={
            "role_match": "yes",
            "role_direction": "product",
            "industry_match": "no",
            "reasons": ["Продукт относится к медицинским устройствам."],
            "questions": [],
        })
        unknown = FixtureGateway(data={
            "role_match": "yes",
            "role_direction": "product",
            "industry_match": "unclear",
            "reasons": ["Отрасль не раскрыта."],
            "questions": [],
        })

        rejected = evaluate(vacancy, self.profile.criteria, gateway=mismatch)
        cache.clear()
        clarified = evaluate(vacancy, self.profile.criteria, gateway=unknown)

        self.assertEqual(rejected.status, "reject")
        self.assertTrue(any("отрасл" in reason.lower() for reason in rejected.reasons))
        self.assertEqual(clarified.status, "clarify")
        self.assertTrue(any("отрасл" in question.lower() for question in clarified.questions))

    def test_unknown_or_noncomparable_salary_clarifies_instead_of_rejecting(self):
        cases = [
            ({"salary_basis": ""}, None, "основу"),
            ({"salary_period": "hour", "salary_min": Decimal("50"), "salary_max": Decimal("80")}, None, "часов"),
            ({"salary_component": "total", "salary_fixed": {}, "salary_min": Decimal("90000")}, None, "фиксированной"),
            ({"salary_component": "", "salary_min": Decimal("90000"), "salary_max": Decimal("120000")}, None, "фиксированной"),
            ({"salary_component": "fixed", "salary_min": Decimal("40000"), "salary_max": None}, None, "верхнюю"),
            ({"salary_component": "fixed", "salary_min": Decimal("90000"), "salary_max": Decimal("60000")}, None, "корректность"),
            ({"salary_currency": "EUR"}, None, "датированному курсу"),
            ({"salary_min": Decimal("40000"), "salary_max": Decimal("70000")}, None, "переговорах"),
            ({}, "unknown", "gross"),
        ]
        for index, (vacancy_overrides, target_basis, expected) in enumerate(cases):
            with self.subTest(case=index):
                criteria = deepcopy(self.profile.criteria)
                if target_basis is not None:
                    criteria["salary"]["basis"] = target_basis
                result = evaluate(self.vacancy(**vacancy_overrides), criteria, gateway=FixtureGateway())
                self.assertEqual(result.status, "clarify")
                self.assertTrue(any(expected in question for question in result.questions))

    def test_non_usd_stays_clarify_even_when_target_uses_same_currency_without_fx(self):
        criteria = deepcopy(self.profile.criteria)
        criteria["salary"]["currency"] = "EUR"

        result = evaluate(
            self.vacancy(salary_currency="EUR", salary_min=Decimal("70000"), salary_max=Decimal("90000")),
            criteria,
            gateway=FixtureGateway(),
        )

        self.assertEqual(result.status, "clarify")
        self.assertTrue(any("датированному курсу" in question for question in result.questions))

    def test_country_hard_mismatch_rejects_but_worldwide_and_timezone_unknown_clarify(self):
        mismatch = evaluate(
            self.vacancy(country_restrictions=["United States"]),
            self.profile.criteria,
            gateway=FixtureGateway(),
        )
        worldwide = evaluate(
            self.vacancy(country_restrictions=["worldwide"]),
            self.profile.criteria,
            gateway=FixtureGateway(),
        )
        timezone = evaluate(
            self.vacancy(timezone_restrictions=["UTC-8"]),
            self.profile.criteria,
            gateway=FixtureGateway(),
        )

        self.assertEqual(mismatch.status, "reject")
        self.assertTrue(any("страны" in reason for reason in mismatch.reasons))
        self.assertFalse(any("ограничение страны" in question for question in mismatch.questions))
        self.assertEqual(worldwide.status, "clarify")
        self.assertTrue(any("право найма" in question for question in worldwide.questions))
        self.assertEqual(timezone.status, "clarify")
        self.assertTrue(any("часовым поясом" in question for question in timezone.questions))

    def test_europe_region_accepts_portugal_and_missing_geo_facts_are_separate_questions(self):
        europe = evaluate(
            self.vacancy(country_restrictions=["Europe"]),
            self.profile.criteria,
            gateway=FixtureGateway(),
        )
        criteria = deepcopy(self.profile.criteria)
        criteria["work_authorized_countries"] = []
        missing = evaluate(
            self.vacancy(country_restrictions=[], timezone_restrictions=[]),
            criteria,
            gateway=FixtureGateway(),
        )

        self.assertEqual(europe.status, "fit")
        self.assertEqual(missing.status, "clarify")
        self.assertTrue(any("стран" in question.lower() for question in missing.questions))
        self.assertTrue(any("часов" in question.lower() for question in missing.questions))
        self.assertTrue(any("право на работу" in question.lower() for question in missing.questions))

    def test_unchanged_content_criteria_and_profile_version_use_cache(self):
        vacancy = self.vacancy()
        gateway = FixtureGateway()

        first = evaluate(vacancy, self.profile.criteria, gateway=gateway)
        second = evaluate(vacancy, self.profile.criteria, gateway=gateway)
        changed_criteria = deepcopy(self.profile.criteria)
        changed_criteria["industries"] = ["crypto"]
        third = evaluate(vacancy, changed_criteria, gateway=gateway)
        fourth = evaluate(vacancy, changed_criteria, gateway=gateway, profile_version=self.profile.version + 1)
        vacancy.description += " Lead an additional payments squad."
        vacancy.save(update_fields=["description"])
        record = vacancy.source_records.get()
        record.raw_hash = description_hash(vacancy.description)
        record.save(update_fields=["raw_hash"])
        fifth = evaluate(vacancy, changed_criteria, gateway=gateway, profile_version=self.profile.version + 1)

        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertFalse(third.cached)
        self.assertFalse(fourth.cached)
        self.assertFalse(fifth.cached)
        self.assertEqual(len(gateway.calls), 4)

    def test_normal_contender_rereads_cache_after_acquiring_lease(self):
        vacancy = self.vacancy()
        gateway = FixtureGateway()
        cached = Assessment("fit", ("Результат лидера.",), (), "product", cache_key="leader")

        with patch.object(cache, "get", side_effect=[None, cached]):
            result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertTrue(result.cached)
        self.assertEqual(result.reasons, ("Результат лидера.",))
        self.assertEqual(gateway.calls, [])
        self.assertEqual(Lease.objects.count(), 0)

    def test_budget_leaves_pending_provider_leaves_error_and_neither_is_cached(self):
        vacancy = self.vacancy()
        budget_gateway = FixtureGateway(error=GatewayUnavailable("budget_pending", "internal provider detail"))

        first = evaluate(vacancy, self.profile.criteria, gateway=budget_gateway)
        second = evaluate(vacancy, self.profile.criteria, gateway=budget_gateway)

        self.assertEqual((first.status, first.error_code), ("pending", "budget_pending"))
        self.assertIn("ожидающей", first.reasons[0])
        self.assertNotIn("internal provider detail", first.reasons[0])
        self.assertEqual(second.status, "pending")
        self.assertEqual(len(budget_gateway.calls), 2)

        provider_gateway = FixtureGateway(error=GatewayError("provider_error", "raw upstream response"))
        provider = evaluate(vacancy, self.profile.criteria, gateway=provider_gateway)
        self.assertEqual((provider.status, provider.error_code), ("error", "provider_error"))
        self.assertNotIn("raw upstream response", provider.reasons[0])

    def test_permission_requires_literal_record_and_source_basis_before_gateway(self):
        vacancy = self.vacancy()
        record = vacancy.source_records.get()
        self.source.llm_permission = False
        self.source.save(update_fields=["llm_permission"])
        gateway = FixtureGateway()

        result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertEqual(result.status, "clarify")
        self.assertEqual(result.error_code, "permission_required")
        self.assertEqual(gateway.calls, [])

    @override_settings(
        OPENAI_API_KEY="configured-key",
        OPENAI_MODEL="configured-model",
        OPENAI_PRICES={"configured-model": {"input_per_million": "1.25", "output_per_million": "5.00"}},
    )
    @patch("jobs.matching.services.OpenAIGateway")
    def test_default_gateway_receives_configured_key_model_and_prices(self, gateway_class):
        gateway_class.return_value.structured.return_value = FixtureGateway().data
        self.profile.preferences = {**self.profile.preferences, "openai_model": "configured-model"}
        self.profile.save(update_fields=["preferences"])

        result = evaluate(self.vacancy(), self.profile.criteria)

        self.assertEqual(result.status, "fit")
        gateway_class.assert_called_once_with(
            api_key="configured-key",
            model="configured-model",
            prices={"configured-model": {"input_per_million": "1.25", "output_per_million": "5.00"}},
        )
        self.assertEqual(gateway_class.return_value.structured.call_args.kwargs["daily_limit"], "2.50")

    @override_settings(OPENAI_API_KEY="configured-key", OPENAI_MODEL="configured-model", OPENAI_PRICES="{broken")
    def test_malformed_prices_are_pending_config_error_before_paid_call(self):
        result = evaluate(self.vacancy(), self.profile.criteria)

        self.assertEqual((result.status, result.error_code), ("pending", "invalid_price_config"))
        self.assertEqual(UsageReservation.objects.count(), 0)

    def test_missing_prices_are_pending_without_reservation(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "key", "OPENAI_MODEL": "model", "OPENAI_PRICES_JSON": ""}, clear=False):
            result = evaluate(self.vacancy(), self.profile.criteria)

        self.assertEqual((result.status, result.error_code), ("pending", "unknown_price"))
        self.assertEqual(UsageReservation.objects.count(), 0)

    def test_single_flight_returns_pending_follower_then_cached_result(self):
        vacancy = self.vacancy()
        follower_gateway = FixtureGateway()

        class ReentrantGateway(FixtureGateway):
            def structured(inner_self, **kwargs):
                inner_self.calls.append(kwargs)
                inner_self.follower = evaluate(vacancy, self.profile.criteria, gateway=follower_gateway)
                return inner_self.data

        gateway = ReentrantGateway()
        leader = evaluate(vacancy, self.profile.criteria, gateway=gateway)
        follower_after = evaluate(vacancy, self.profile.criteria, gateway=follower_gateway)

        self.assertEqual(leader.status, "fit")
        self.assertEqual((gateway.follower.status, gateway.follower.error_code), ("pending", "evaluation_in_progress"))
        self.assertTrue(follower_after.cached)
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(follower_gateway.calls, [])
        self.assertEqual(Lease.objects.count(), 0)

    def test_force_bypasses_indefinite_cache_but_keeps_single_flight(self):
        vacancy = self.vacancy()
        gateway = FixtureGateway()
        with patch.object(cache, "set", wraps=cache.set) as cache_set:
            first = evaluate(vacancy, self.profile.criteria, gateway=gateway)
            second = evaluate(vacancy, self.profile.criteria, gateway=gateway, force=True)

        self.assertFalse(first.cached)
        self.assertFalse(second.cached)
        self.assertEqual(len(gateway.calls), 2)
        self.assertTrue(all(call.kwargs.get("timeout") is None for call in cache_set.call_args_list))

    def test_concurrent_force_returns_busy_not_stale_cache(self):
        vacancy = self.vacancy()
        stale_gateway = FixtureGateway()
        stale = evaluate(vacancy, self.profile.criteria, gateway=stale_gateway)
        self.assertEqual(stale.status, "fit")

        class ForceLeader(FixtureGateway):
            def structured(inner_self, **kwargs):
                inner_self.calls.append(kwargs)
                inner_self.follower = evaluate(vacancy, self.profile.criteria, gateway=FixtureGateway(), force=True)
                return inner_self.data

        leader_gateway = ForceLeader(data={
            "role_match": "yes",
            "role_direction": "product",
            "industry_match": "yes",
            "reasons": ["Новый результат force-лидера."],
            "questions": [],
        })
        leader = evaluate(vacancy, self.profile.criteria, gateway=leader_gateway, force=True)

        self.assertEqual((leader_gateway.follower.status, leader_gateway.follower.error_code), ("pending", "evaluation_in_progress"))
        self.assertFalse(leader_gateway.follower.cached)
        self.assertEqual(leader.reasons, ("Новый результат force-лидера.",))
        self.assertEqual(len(leader_gateway.calls), 1)

    def test_merged_vacancy_does_not_borrow_permission_from_older_source_text(self):
        vacancy = self.vacancy()
        other = Source.objects.create(
            owner=self.owner,
            slug="latest",
            name="Latest source",
            kind="telegram",
            adapter="fixture",
            llm_permission=False,
        )
        vacancy.description = "Product roadmap instructions from the newer unapproved source."
        vacancy.save(update_fields=["description"])
        vacancy.source_records.create(
            source=other,
            external_id="latest-record",
            canonical_url="https://example.test/jobs/latest",
            raw_hash=description_hash(vacancy.description),
            description_permission=False,
        )
        gateway = FixtureGateway()

        result = evaluate(vacancy, self.profile.criteria, gateway=gateway)

        self.assertEqual(result.error_code, "permission_required")
        self.assertEqual(gateway.calls, [])


class MatchingSQLiteConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user("concurrent-owner")
        self.profile = Profile.objects.create(
            owner=self.owner,
            version=1,
            criteria={
                "roles": ["Product Manager"],
                "industries": ["fintech"],
                "salary": {"target": "5000", "currency": "USD", "period": "month", "basis": "gross"},
                "residence_country": "Portugal",
                "hiring_countries": ["Portugal"],
                "work_authorized_countries": ["Portugal"],
                "timezone": "Europe/Lisbon",
            },
            preferences={"daily_budget_usd": "2.50"},
        )
        source = Source.objects.create(
            owner=self.owner,
            slug="concurrent-fixture",
            name="Concurrent fixture",
            kind="site",
            adapter="fixture",
            llm_permission=True,
        )
        self.vacancy = Vacancy.objects.create(
            owner=self.owner,
            title="Product Manager",
            company="Example",
            description="Own product discovery, roadmap and delivery for payment products.",
            role="Product Manager",
            industry="fintech",
            salary_min=Decimal("60000"),
            salary_max=Decimal("80000"),
            salary_currency="USD",
            salary_period="year",
            salary_basis="gross",
            salary_component="fixed",
            country_restrictions=["Portugal"],
            timezone_restrictions=["Europe/Lisbon"],
        )
        self.vacancy.source_records.create(
            source=source,
            external_id="concurrent-job",
            canonical_url="https://example.test/jobs/concurrent",
            raw_hash=description_hash(self.vacancy.description),
            description_permission=True,
        )

    def test_sqlite_write_contention_returns_pending_instead_of_raising(self):
        result = {}
        gateway = FixtureGateway()
        connection = connections["default"]
        connection.cursor().execute("BEGIN IMMEDIATE")

        def contend():
            connections.close_all()
            worker_connection = connections["default"]
            worker_connection.cursor().execute("PRAGMA busy_timeout = 50")
            vacancy = Vacancy.objects.get(pk=self.vacancy.pk)
            result["assessment"] = evaluate(vacancy, self.profile.criteria, gateway=gateway)
            worker_connection.close()

        worker = threading.Thread(target=contend)
        try:
            worker.start()
            worker.join(timeout=2)
        finally:
            connection.rollback()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(
            (result["assessment"].status, result["assessment"].error_code),
            ("pending", "evaluation_in_progress"),
        )
        self.assertEqual(gateway.calls, [])

    def test_two_connections_allow_only_one_paid_evaluation_leader(self):
        entered_gateway = threading.Event()
        release_gateway = threading.Event()
        results = {}

        class BlockingGateway(FixtureGateway):
            def structured(inner_self, **kwargs):
                inner_self.calls.append(kwargs)
                entered_gateway.set()
                release_gateway.wait(timeout=2)
                return inner_self.data

        leader_gateway = BlockingGateway()
        follower_gateway = FixtureGateway()

        def assess(label, gateway):
            connections.close_all()
            vacancy = Vacancy.objects.get(pk=self.vacancy.pk)
            criteria = Profile.objects.get(pk=self.profile.pk).criteria
            results[label] = evaluate(vacancy, criteria, gateway=gateway)
            connections["default"].close()

        leader = threading.Thread(target=assess, args=("leader", leader_gateway))
        follower = threading.Thread(target=assess, args=("follower", follower_gateway))
        try:
            leader.start()
            self.assertTrue(entered_gateway.wait(timeout=2))
            follower.start()
            follower.join(timeout=2)
        finally:
            release_gateway.set()
        leader.join(timeout=2)
        follower.join(timeout=2)

        self.assertFalse(leader.is_alive())
        self.assertFalse(follower.is_alive())
        self.assertEqual(results["leader"].status, "fit")
        self.assertEqual(
            (results["follower"].status, results["follower"].error_code),
            ("pending", "evaluation_in_progress"),
        )
        self.assertEqual(len(leader_gateway.calls), 1)
        self.assertEqual(follower_gateway.calls, [])
