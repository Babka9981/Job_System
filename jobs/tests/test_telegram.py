import hashlib
import os
from io import StringIO
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils import timezone

from jobs.models.models import Source, SourceRecord, Vacancy
from jobs.matching.services import evaluate
from jobs.sources.core.registry import seed_sources
from jobs.sources.telegram.adapter import (
    HistoryAccessError,
    HistoryMessage,
    HistoryPage,
    TelegramReader,
    TelegramUpdate,
    sync_sources,
)
from jobs.sources.telegram.links import TelegramLinkError, normalize_source_link, post_url
from jobs.sources.telegram.permissions import (
    PermissionBasis,
    apply_source_permission,
    permission_for_message,
    store_independent_jd,
    with_independent_jd,
)
from jobs.sources.telegram.registry import add_or_enable_source, set_source_enabled
from jobs.sources.telegram.session import (
    TelegramConfigurationError,
    TelethonHistoryClient,
    private_session_path,
    telethon_runtime_status,
)


class FakeHistoryClient:
    def __init__(self, pages=None, updates=None, failure=None, authorized=True):
        self.pages = list(pages or [])
        self.updates = list(updates or [])
        self.failure = failure
        self.authorized = authorized
        self.calls = []

    def is_authorized(self):
        return self.authorized

    def fetch_history(self, peer, *, after_id, offset_id, limit):
        self.calls.append((peer, after_id, offset_id, limit))
        if self.failure:
            raise self.failure
        return self.pages.pop(0) if self.pages else HistoryPage(())

    def fetch_updates(self, peer, *, checkpoint):
        return tuple(self.updates)


class TelegramRegistryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="password")

    def test_normalizes_supported_public_source_links_and_rejects_post_or_invite_links(self):
        self.assertEqual(normalize_source_link("HTTPS://T.ME/s/RemoteIT/"), "https://t.me/remoteit")
        self.assertEqual(normalize_source_link("@RemoteIT"), "https://t.me/remoteit")
        for unsafe in ("https://evil.test/remoteit", "https://t.me/remoteit/42", "https://t.me/+invite"):
            with self.subTest(unsafe=unsafe), self.assertRaises(TelegramLinkError):
                normalize_source_link(unsafe)

    def test_add_and_disable_use_normalized_link_without_storing_credentials(self):
        source, created = add_or_enable_source(self.owner, "https://telegram.me/New_Channel")
        same, second_created = add_or_enable_source(self.owner, "@new_channel")

        self.assertTrue(created)
        self.assertFalse(second_created)
        self.assertEqual(source.pk, same.pk)
        self.assertEqual(source.config["url"], "https://t.me/new_channel")
        self.assertTrue(same.enabled)
        self.assertFalse({"phone", "api_id", "api_hash", "session"} & set(source.config))

        disabled = set_source_enabled(self.owner, "HTTPS://T.ME/NEW_CHANNEL", False)
        self.assertFalse(disabled.enabled)

    def test_seeded_41_addresses_can_all_be_resolved_by_normalized_link(self):
        seed_sources(self.owner)
        telegram = Source.objects.filter(owner=self.owner, kind="telegram")
        self.assertEqual(telegram.count(), 41)
        for source in telegram:
            self.assertEqual(normalize_source_link(source.config["url"]), source.config["url"].lower())


class TelegramPermissionTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="password")
        self.source, _ = add_or_enable_source(self.owner, "https://t.me/example_jobs")

    def test_permission_requires_literal_true_documented_basis_and_exact_scope(self):
        invalid = PermissionBasis(True, "channel_owner_consent", "", "https://t.me/example_jobs")
        wrong_scope = PermissionBasis(True, "channel_owner_consent", "consent-42", "https://t.me/other_jobs")
        self.assertFalse(apply_source_permission(self.source, invalid))
        self.assertFalse(apply_source_permission(self.source, wrong_scope))

        valid = PermissionBasis(True, "channel_owner_consent", "consent-42", "https://t.me/example_jobs")
        self.assertTrue(apply_source_permission(self.source, valid))
        self.source.refresh_from_db()
        self.assertTrue(self.source.llm_permission)
        self.assertEqual(self.source.config["llm_permission_basis"]["reference"], "consent-42")

    def test_forward_and_manual_paste_never_inherit_source_permission(self):
        basis = PermissionBasis(True, "channel_owner_consent", "consent-42", self.source.config["url"])
        apply_source_permission(self.source, basis)
        self.source.refresh_from_db()

        self.assertTrue(permission_for_message(self.source, origin="api"))
        self.assertFalse(permission_for_message(self.source, origin="forward"))
        self.assertFalse(permission_for_message(self.source, origin="manual"))

    def test_independent_permitted_jd_replaces_unlicensed_telegram_text_with_exact_hash(self):
        record = {"description": "Telegram text", "raw_hash": "old", "description_permission": False, "attribution": {}}
        basis = PermissionBasis(True, "direct_employer_jd", "email-7", "https://employer.example/jobs/7")

        supplemented = with_independent_jd(record, "<p>Allowed job description</p>", basis)

        self.assertEqual(supplemented["description"], "Allowed job description")
        self.assertEqual(supplemented["raw_hash"], hashlib.sha256(b"Allowed job description").hexdigest())
        self.assertTrue(supplemented["description_permission"])
        self.assertEqual(supplemented["source_slug"], "independent-permitted-jd")
        self.assertEqual(supplemented["attribution"]["description_origin"], "independent_jd")

    def test_independent_jd_creates_distinct_manual_evidence_accepted_by_matching(self):
        self.source.status = Source.Status.READY
        self.source.save(update_fields=["status"])
        message = HistoryMessage(
            7,
            "Vacancy: Product Manager\nCompany: Acme\nShort Telegram summary",
            timezone.now(),
            public_handle="example_jobs",
        )
        TelegramReader(FakeHistoryClient([HistoryPage((message,))])).sync_source(self.source)
        telegram_record = SourceRecord.objects.get()
        self.assertFalse(telegram_record.description_permission)

        basis = PermissionBasis(True, "direct_employer_jd", "email-7", "https://employer.example/jobs/7")
        manual_record = store_independent_jd(telegram_record, "Allowed full product roadmap description", basis)

        telegram_record.refresh_from_db()
        self.assertNotEqual(manual_record.source_id, telegram_record.source_id)
        self.assertEqual(manual_record.source.kind, "manual")
        self.assertTrue(manual_record.description_permission)
        self.assertFalse(telegram_record.description_permission)
        self.assertEqual(
            manual_record.raw_hash,
            hashlib.sha256(manual_record.vacancy.description.encode("utf-8")).hexdigest(),
        )

        class Gateway:
            calls = 0

            def structured(self, **kwargs):
                self.calls += 1
                return {"role_match": "yes", "role_direction": "product", "industry_match": "yes", "reasons": ["match"], "questions": []}

        gateway = Gateway()
        assessment = evaluate(manual_record.vacancy, {"roles": ["Product Manager"]}, gateway=gateway, force=True)
        self.assertEqual(gateway.calls, 1)
        self.assertNotEqual(assessment.error_code, "permission_required")


class TelegramReaderTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="password")
        self.source, _ = add_or_enable_source(self.owner, "https://t.me/example_jobs")
        self.source.status = Source.Status.READY
        self.source.save(update_fields=["status"])

    def message(self, message_id=10, text=None, *, edited_at=None, peer_id=None):
        return HistoryMessage(
            message_id=message_id,
            text=text or "Vacancy: Product Manager\nCompany: Acme\nOwn roadmap and discovery",
            published_at=timezone.now(),
            edited_at=edited_at,
            peer_id=peer_id,
            public_handle="example_jobs" if peer_id is None else None,
        )

    def test_multi_vacancy_post_creates_two_exact_identities_and_exact_links(self):
        text = (
            "Vacancy: Product Manager\nCompany: Acme\nOwn roadmap\n---\n"
            "Vacancy: Head of Support\nCompany: Beta\nOwn SLA and escalations"
        )
        client = FakeHistoryClient([HistoryPage((self.message(text=text),))])

        report = TelegramReader(client).sync_source(self.source)

        self.assertEqual((report.created, report.review), (2, 0))
        records = list(SourceRecord.objects.order_by("external_id"))
        self.assertEqual(len({record.external_id for record in records}), 2)
        self.assertTrue(all(record.external_id.startswith("10:") for record in records))
        self.assertTrue(all(len(record.external_id.split(":", 1)[1]) == 24 for record in records))
        self.assertTrue(all(record.canonical_url == "https://t.me/example_jobs/10" for record in records))
        self.assertTrue(all(record.attribution["message_id"] == 10 for record in records))

    def test_ambiguous_split_creates_no_vacancy_and_records_review_without_invention(self):
        message = self.message(text="Product Manager / Project Manager\nMaybe two roles, details below")

        report = TelegramReader(FakeHistoryClient([HistoryPage((message,))])).sync_source(self.source)

        self.assertEqual((report.created, report.review), (0, 1))
        self.assertFalse(Vacancy.objects.exists())
        self.source.refresh_from_db()
        self.assertEqual(self.source.config["telegram_review"][0]["message_id"], 10)

    def test_retry_resumes_after_committed_page_and_is_idempotent(self):
        first_page = HistoryPage((self.message(10),), next_offset_id=10)
        first = TelegramReader(FakeHistoryClient([first_page], failure=None), max_pages=1).sync_source(self.source)
        self.assertEqual(first.created, 1)
        self.source.refresh_from_db()
        self.assertEqual(self.source.config["telegram_checkpoint"]["message_id"], 0)
        self.assertEqual(self.source.config["telegram_checkpoint"]["offset_id"], 10)

        second_client = FakeHistoryClient([HistoryPage((self.message(11),))])
        second = TelegramReader(second_client).sync_source(self.source)
        self.assertEqual(second.created, 1)
        self.assertEqual(SourceRecord.objects.count(), 2)
        self.assertEqual(second_client.calls[0][1:], (0, 10, 100))
        self.source.refresh_from_db()
        self.assertEqual(self.source.config["telegram_checkpoint"]["message_id"], 11)
        self.assertEqual(self.source.config["telegram_checkpoint"]["offset_id"], 0)

    def test_descending_window_crash_resumes_lower_ids_before_advancing_high_water(self):
        first_page = HistoryPage((self.message(30), self.message(29)), next_offset_id=29, raw_count=2)

        class CrashAfterPage(FakeHistoryClient):
            def fetch_history(inner, peer, *, after_id, offset_id, limit):
                inner.calls.append((peer, after_id, offset_id, limit))
                if len(inner.calls) == 1:
                    return first_page
                raise HistoryAccessError("history_unavailable", "История временно недоступна.", retryable=True)

        TelegramReader(CrashAfterPage()).sync_source(self.source)
        self.source.refresh_from_db()
        self.assertEqual(self.source.config["telegram_checkpoint"], {"message_id": 0, "offset_id": 29, "window_max_id": 30})

        resume = FakeHistoryClient([HistoryPage((self.message(28), self.message(27)), raw_count=2)])
        TelegramReader(resume).sync_source(self.source)
        self.assertEqual(resume.calls[0][1:], (0, 29, 100))
        self.assertEqual(SourceRecord.objects.count(), 4)
        self.source.refresh_from_db()
        self.assertEqual(self.source.config["telegram_checkpoint"]["message_id"], 30)
        self.assertEqual(self.source.config["telegram_checkpoint"]["offset_id"], 0)

    def test_edit_updates_same_vacancy_and_delete_event_marks_all_post_parts_removed(self):
        TelegramReader(FakeHistoryClient([HistoryPage((self.message(10),))])).sync_source(self.source)
        record = SourceRecord.objects.get()
        vacancy_id = record.vacancy_id

        edited = self.message(10, "Vacancy: Senior Product Manager\nCompany: Acme\nOwn roadmap", edited_at=timezone.now())
        edit_report = TelegramReader(FakeHistoryClient([HistoryPage((edited,))])).sync_source(self.source)
        self.assertEqual(edit_report.updated, 1)
        record.refresh_from_db()
        self.assertEqual(record.vacancy_id, vacancy_id)
        self.assertEqual(record.vacancy.title, "Senior Product Manager")

        delete = TelegramUpdate(kind="deleted", message_id=10)
        TelegramReader(FakeHistoryClient([HistoryPage(())], updates=[delete])).sync_source(self.source)
        record.vacancy.refresh_from_db()
        self.assertEqual(record.vacancy.availability, Vacancy.Availability.REMOVED)

    def test_part_identity_survives_reorder_and_deleting_a_sibling_preserves_user_state(self):
        initial = self.message(text=(
            "Vacancy: Product Manager\nCompany: Acme\nOwn roadmap\n"
            "Vacancy: Head of Support\nCompany: Beta\nOwn SLA and escalations"
        ))
        TelegramReader(FakeHistoryClient([HistoryPage((initial,))])).sync_source(self.source)
        head = Vacancy.objects.get(title="Head of Support")
        head.user_status = Vacancy.UserStatus.SAVED
        head.user_note = "priority lead"
        head.save(update_fields=["user_status", "user_note"])
        head_id = head.pk

        reordered = self.message(text=(
            "Vacancy: Head of Support\nCompany: Beta\nOwn SLA and escalations\n"
            "Vacancy: Product Manager\nCompany: Acme\nOwn roadmap"
        ), edited_at=timezone.now())
        TelegramReader(FakeHistoryClient([HistoryPage((reordered,))])).sync_source(self.source)
        head.refresh_from_db()
        self.assertEqual((head.pk, head.title, head.user_status, head.user_note), (head_id, "Head of Support", "saved", "priority lead"))

        only_head = self.message(text="Vacancy: Head of Support\nCompany: Beta\nOwn SLA and escalations", edited_at=timezone.now())
        TelegramReader(FakeHistoryClient([HistoryPage((only_head,))])).sync_source(self.source)
        head.refresh_from_db()
        self.assertEqual((head.pk, head.user_status, head.user_note), (head_id, "saved", "priority lead"))
        self.assertEqual(Vacancy.objects.get(title="Product Manager").availability, Vacancy.Availability.REMOVED)

    def test_part_identity_survives_unique_company_correction_without_cross_assigning_siblings(self):
        initial = self.message(text=(
            "Vacancy: Product Manager\nCompany: Acme\nOwn roadmap\n"
            "Vacancy: Head of Support\nCompany: Beta\nOwn SLA"
        ))
        TelegramReader(FakeHistoryClient([HistoryPage((initial,))])).sync_source(self.source)
        product = Vacancy.objects.get(title="Product Manager")
        support = Vacancy.objects.get(title="Head of Support")
        product.user_status = Vacancy.UserStatus.SAVED
        product.user_note = "product-state"
        product.save(update_fields=["user_status", "user_note"])
        support.user_status = Vacancy.UserStatus.APPLIED
        support.user_note = "support-state"
        support.save(update_fields=["user_status", "user_note"])

        corrected = self.message(text=(
            "Vacancy: Head of Support\nCompany: Beta Holdings\nOwn SLA\n"
            "Vacancy: Product Manager\nCompany: Acme Ltd\nOwn roadmap"
        ), edited_at=timezone.now())
        TelegramReader(FakeHistoryClient([HistoryPage((corrected,))])).sync_source(self.source)

        product.refresh_from_db()
        support.refresh_from_db()
        self.assertEqual((product.company, product.user_status, product.user_note), ("Acme Ltd", "saved", "product-state"))
        self.assertEqual((support.company, support.user_status, support.user_note), ("Beta Holdings", "applied", "support-state"))
        self.assertEqual(Vacancy.objects.count(), 2)

    def test_same_title_replacement_with_incompatible_company_and_body_gets_new_identity(self):
        initial = self.message(text=(
            "Vacancy: Product Manager\nCompany: Acme\nOwn payments roadmap and merchant discovery"
        ))
        TelegramReader(FakeHistoryClient([HistoryPage((initial,))])).sync_source(self.source)
        old = Vacancy.objects.get()
        old.user_status = Vacancy.UserStatus.APPLIED
        old.user_note = "Acme application state"
        old.save(update_fields=["user_status", "user_note"])

        replacement = self.message(text=(
            "Vacancy: Product Manager\nCompany: Contoso\nLead healthcare scheduling and clinical operations"
        ), edited_at=timezone.now())
        TelegramReader(FakeHistoryClient([HistoryPage((replacement,))])).sync_source(self.source)

        old.refresh_from_db()
        new = Vacancy.objects.exclude(pk=old.pk).get()
        self.assertEqual(old.availability, Vacancy.Availability.REMOVED)
        self.assertEqual((old.user_status, old.user_note), (Vacancy.UserStatus.APPLIED, "Acme application state"))
        self.assertEqual((new.company, new.user_status, new.user_note), ("Contoso", Vacancy.UserStatus.NEW, ""))
        self.assertEqual(SourceRecord.objects.filter(source=self.source).count(), 2)
        self.assertEqual(
            SourceRecord.objects.get(vacancy=old).attribution["deleted"],
            True,
        )
        self.assertEqual(
            SourceRecord.objects.get(vacancy=new).attribution["deleted"],
            False,
        )

    def test_incompatible_companies_never_merge_even_when_boilerplate_body_is_identical(self):
        initial = self.message(text=(
            "Vacancy: Product Manager\nCompany: Acme\nOwn roadmap and discovery"
        ))
        TelegramReader(FakeHistoryClient([HistoryPage((initial,))])).sync_source(self.source)
        old = Vacancy.objects.get()
        old.user_status = Vacancy.UserStatus.SAVED
        old.user_note = "Acme-only state"
        old.save(update_fields=["user_status", "user_note"])

        replacement = self.message(text=(
            "Vacancy: Product Manager\nCompany: Contoso\nOwn roadmap and discovery"
        ), edited_at=timezone.now())
        TelegramReader(FakeHistoryClient([HistoryPage((replacement,))])).sync_source(self.source)

        old.refresh_from_db()
        new = Vacancy.objects.exclude(pk=old.pk).get()
        self.assertEqual((old.availability, old.user_status, old.user_note), (
            Vacancy.Availability.REMOVED,
            Vacancy.UserStatus.SAVED,
            "Acme-only state",
        ))
        self.assertEqual((new.company, new.user_status, new.user_note), (
            "Contoso",
            Vacancy.UserStatus.NEW,
            "",
        ))

    def test_duplicate_titles_use_unique_fuzzy_company_and_never_swap_user_state(self):
        initial = self.message(text=(
            "Vacancy: Product Manager\nCompany: Alpha Labs\nPayments roadmap\n"
            "Vacancy: Product Manager\nCompany: Beta Systems\nCrypto roadmap"
        ))
        TelegramReader(FakeHistoryClient([HistoryPage((initial,))])).sync_source(self.source)
        alpha = Vacancy.objects.get(company="Alpha Labs")
        beta = Vacancy.objects.get(company="Beta Systems")
        alpha.user_note = "alpha-state"
        beta.user_note = "beta-state"
        alpha.save(update_fields=["user_note"])
        beta.save(update_fields=["user_note"])

        edited = self.message(text=(
            "Vacancy: Product Manager\nCompany: Beta Systems Ltd\nCrypto roadmap\n"
            "Vacancy: Product Manager\nCompany: Alpha Labs Ltd\nPayments roadmap"
        ), edited_at=timezone.now())
        TelegramReader(FakeHistoryClient([HistoryPage((edited,))])).sync_source(self.source)

        alpha.refresh_from_db()
        beta.refresh_from_db()
        self.assertEqual((alpha.company, alpha.user_note), ("Alpha Labs Ltd", "alpha-state"))
        self.assertEqual((beta.company, beta.user_note), ("Beta Systems Ltd", "beta-state"))
        self.assertEqual(Vacancy.objects.count(), 2)

    def test_private_post_link_has_access_hint_and_never_claims_public_permalink(self):
        private = self.message(77, peer_id=-1001234567890)
        TelegramReader(FakeHistoryClient([HistoryPage((private,))])).sync_source(self.source)
        record = SourceRecord.objects.get()

        self.assertEqual(record.canonical_url, "https://t.me/c/1234567890/77")
        self.assertEqual(record.attribution["access_hint"], "Требуется доступ аккаунта к приватному каналу")
        self.assertFalse(record.adapter_confirmed_permalink)
        self.assertEqual(post_url(private), "https://t.me/c/1234567890/77")

    def test_missing_history_is_not_treated_as_deleted(self):
        TelegramReader(FakeHistoryClient([HistoryPage((self.message(10),))])).sync_source(self.source)
        TelegramReader(FakeHistoryClient([HistoryPage(())])).sync_source(self.source)
        vacancy = Vacancy.objects.get()
        self.assertNotEqual(vacancy.availability, Vacancy.Availability.REMOVED)

    def test_forwarded_message_never_receives_channel_llm_permission(self):
        basis = PermissionBasis(True, "channel_owner_consent", "consent-42", self.source.config["url"])
        apply_source_permission(self.source, basis)
        self.source.refresh_from_db()
        forwarded = HistoryMessage(
            message_id=10,
            text="Vacancy: Product Manager\nCompany: Acme\nOwn roadmap",
            published_at=timezone.now(),
            public_handle="example_jobs",
            origin="forward",
        )
        TelegramReader(FakeHistoryClient([HistoryPage((forwarded,))])).sync_source(self.source)
        self.assertFalse(SourceRecord.objects.get().description_permission)

    def test_edit_and_delete_updates_do_not_advance_new_message_checkpoint(self):
        TelegramReader(FakeHistoryClient([HistoryPage((self.message(10),))])).sync_source(self.source)
        edit = TelegramUpdate("edited", 50, self.message(50, edited_at=timezone.now()))
        delete = TelegramUpdate("deleted", 60)
        TelegramReader(FakeHistoryClient([HistoryPage(())], updates=[edit, delete])).sync_source(self.source)
        self.source.refresh_from_db()
        self.assertEqual(self.source.config["telegram_checkpoint"]["message_id"], 10)

    def test_sequential_deletes_recompute_availability_from_all_record_evidence(self):
        second, _ = add_or_enable_source(self.owner, "https://t.me/other_jobs")
        second.status = Source.Status.READY
        second.save(update_fields=["status"])
        first_message = self.message(10)
        second_message = HistoryMessage(20, first_message.text, timezone.now(), public_handle="other_jobs")
        TelegramReader(FakeHistoryClient([HistoryPage((first_message,))])).sync_source(self.source)
        TelegramReader(FakeHistoryClient([HistoryPage((second_message,))])).sync_source(second)
        self.assertEqual(Vacancy.objects.count(), 1)

        TelegramReader(FakeHistoryClient([HistoryPage(())], updates=[TelegramUpdate("deleted", 10)])).sync_source(self.source)
        vacancy = Vacancy.objects.get()
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.availability, Vacancy.Availability.ACTIVE)

        TelegramReader(FakeHistoryClient([HistoryPage(())], updates=[TelegramUpdate("deleted", 20)])).sync_source(second)
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.availability, Vacancy.Availability.REMOVED)

    def test_sync_sources_isolates_factory_and_authorization_exceptions(self):
        factory_broken, _ = add_or_enable_source(self.owner, "https://t.me/factory_broken")
        connect_broken, _ = add_or_enable_source(self.owner, "https://t.me/connect_broken")
        auth_broken, _ = add_or_enable_source(self.owner, "https://t.me/auth_broken")
        healthy, _ = add_or_enable_source(self.owner, "https://t.me/healthy_jobs")

        class AuthBroken(FakeHistoryClient):
            def is_authorized(self):
                raise RuntimeError("sensitive provider detail")

        class ConnectBroken(FakeHistoryClient):
            def connect(self):
                raise RuntimeError("connect detail")

        def factory(source):
            if source == factory_broken:
                raise RuntimeError("factory detail")
            if source == connect_broken:
                return ConnectBroken()
            if source == auth_broken:
                return AuthBroken()
            message = HistoryMessage(1, "Vacancy: Product Manager\nCompany: Acme\nOwn roadmap", timezone.now(), public_handle="healthy_jobs")
            return FakeHistoryClient([HistoryPage((message,))])

        reports = sync_sources([factory_broken, connect_broken, auth_broken, healthy], factory)

        self.assertEqual(set(reports), {factory_broken.slug, connect_broken.slug, auth_broken.slug, healthy.slug})
        self.assertEqual(reports[factory_broken.slug].error_code, "client_unavailable")
        self.assertEqual(reports[connect_broken.slug].error_code, "client_unavailable")
        self.assertEqual(reports[auth_broken.slug].error_code, "session_check_failed")
        self.assertEqual(reports[healthy.slug].created, 1)
        factory_broken.refresh_from_db()
        self.assertNotIn("detail", str(factory_broken.config))

    def test_malformed_checkpoint_and_unexpected_sync_exception_are_safe_and_do_not_stop_following_channels(self):
        malformed, _ = add_or_enable_source(self.owner, "https://t.me/malformed_checkpoint")
        malformed.config = {**malformed.config, "telegram_checkpoint": {"message_id": "raw-secret-marker", "offset_id": -1}}
        malformed.save(update_fields=["config"])
        unexpected, _ = add_or_enable_source(self.owner, "https://t.me/unexpected_sync")
        healthy, _ = add_or_enable_source(self.owner, "https://t.me/following_healthy")

        original = TelegramReader.sync_source

        def sometimes_raises(reader, source):
            if source.pk == unexpected.pk:
                raise RuntimeError("provider raw-secret-marker")
            return original(reader, source)

        def factory(source):
            message = HistoryMessage(1, "Vacancy: Product Manager\nCompany: Acme\nOwn roadmap", timezone.now(), public_handle="following_healthy")
            return FakeHistoryClient([HistoryPage((message,))])

        with patch("jobs.sources.telegram.adapter.TelegramReader.sync_source", new=sometimes_raises):
            reports = sync_sources([malformed, unexpected, healthy], factory)

        self.assertEqual(reports[malformed.slug].error_code, "invalid_checkpoint")
        self.assertEqual(reports[unexpected.slug].error_code, "source_sync_failed")
        self.assertEqual(reports[healthy.slug].created, 1)
        for source in (malformed, unexpected):
            source.refresh_from_db()
            last_error = source.config["last_error"]
            self.assertNotIn("raw-secret-marker", str(last_error))
            self.assertNotIn("raw-secret-marker", str(source.config))

    def test_malformed_checkpoint_value_domains_fail_closed(self):
        invalid_values = (
            {"message_id": True, "offset_id": 0},
            {"message_id": -1, "offset_id": 0},
            {"message_id": 1.5, "offset_id": 0},
            {"message_id": 5, "offset_id": -2},
            {"message_id": 5, "offset_id": 2, "window_max_id": 4},
        )
        for index, checkpoint in enumerate(invalid_values):
            source, _ = add_or_enable_source(self.owner, f"https://t.me/bad_checkpoint_{index}")
            source.config = {**source.config, "telegram_checkpoint": checkpoint}
            source.save(update_fields=["config"])
            with self.subTest(checkpoint=checkpoint):
                report = TelegramReader(FakeHistoryClient()).sync_source(source)
                self.assertEqual(report.error_code, "invalid_checkpoint")

    def test_independent_jd_remains_distinct_and_authoritative_after_telegram_edit(self):
        message = self.message(70)
        TelegramReader(FakeHistoryClient([HistoryPage((message,))])).sync_source(self.source)
        telegram_record = SourceRecord.objects.get()
        basis = PermissionBasis(True, "direct_employer_jd", "employer-70", "https://employer.example/jobs/70")
        manual_record = store_independent_jd(telegram_record, "Allowed independent roadmap and discovery", basis)

        edited = self.message(70, "Vacancy: Product Manager\nCompany: Acme Ltd\nEdited Telegram summary", edited_at=timezone.now())
        TelegramReader(FakeHistoryClient([HistoryPage((edited,))])).sync_source(self.source)

        telegram_record.refresh_from_db()
        manual_record.refresh_from_db()
        manual_record.vacancy.refresh_from_db()
        self.assertNotEqual(telegram_record.source_id, manual_record.source_id)
        self.assertEqual(telegram_record.vacancy_id, manual_record.vacancy_id)
        self.assertEqual(Vacancy.objects.count(), 1)
        self.assertEqual(SourceRecord.objects.filter(source=self.source, external_id__startswith="70:").count(), 1)
        self.assertFalse(telegram_record.description_permission)
        self.assertEqual(manual_record.vacancy.description, "Allowed independent roadmap and discovery")
        self.assertEqual(manual_record.raw_hash, hashlib.sha256(manual_record.vacancy.description.encode()).hexdigest())

    def test_sync_sources_disconnects_clients_after_success_and_failure(self):
        failed, _ = add_or_enable_source(self.owner, "https://t.me/cleanup_failed")
        healthy, _ = add_or_enable_source(self.owner, "https://t.me/cleanup_healthy")
        clients = {}

        class Closable(FakeHistoryClient):
            def __init__(self, *, fail=False):
                message = HistoryMessage(1, "Vacancy: Product Manager\nCompany: Acme\nOwn roadmap", timezone.now(), public_handle="cleanup_healthy")
                super().__init__([HistoryPage((message,))], failure=HistoryAccessError("history_unavailable", "Недоступно") if fail else None)
                self.disconnected = 0

            def disconnect(self):
                self.disconnected += 1

        def factory(source):
            clients[source.slug] = Closable(fail=source.pk == failed.pk)
            return clients[source.slug]

        sync_sources([failed, healthy], factory)
        self.assertEqual(clients[failed.slug].disconnected, 1)
        self.assertEqual(clients[healthy.slug].disconnected, 1)


class TelegramPaginationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="password")
        self.source, _ = add_or_enable_source(self.owner, "https://t.me/example_jobs")
        self.source.status = Source.Status.READY
        self.source.save(update_fields=["status"])

    def message(self, message_id=10):
        return HistoryMessage(
            message_id,
            "Vacancy: Product Manager\nCompany: Acme\nOwn roadmap",
            timezone.now(),
            public_handle="example_jobs",
        )

    def test_telethon_cursor_uses_raw_items_including_media_and_service_messages(self):
        class Item:
            def __init__(self, item_id, message=""):
                self.id = item_id
                self.message = message
                self.date = timezone.now()
                self.edit_date = None
                self.chat_id = -10042
                self.chat = None
                self.fwd_from = None

        class RawClient:
            def iter_messages(self, *args, **kwargs):
                self.kwargs = kwargs
                return [Item(9, ""), Item(8, "Vacancy: PM"), Item(7, "")]

        raw = RawClient()
        page = TelethonHistoryClient(raw).fetch_history("https://t.me/jobs", after_id=5, offset_id=0, limit=3)

        self.assertEqual(page.raw_count, 3)
        self.assertEqual(page.next_offset_id, 7)
        self.assertEqual([message.message_id for message in page.messages], [8])
        self.assertEqual(raw.kwargs["min_id"], 5)

    def test_access_failure_is_safe_and_an_unauthorized_session_requests_reconnect(self):
        unauthorized = TelegramReader(FakeHistoryClient(authorized=False)).sync_source(self.source)
        self.assertEqual(unauthorized.error_code, "session_unauthorized")
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, Source.Status.NEEDS_ACCESS)
        self.assertNotIn("phone", str(self.source.config).lower())

        self.source.status = Source.Status.READY
        self.source.save(update_fields=["status"])
        flood = HistoryAccessError("flood_wait", "Telegram просит повторить позже.", retryable=True, retry_after=37)
        report = TelegramReader(FakeHistoryClient(failure=flood)).sync_source(self.source)
        self.assertEqual(report.error_code, "flood_wait")
        self.source.refresh_from_db()
        self.assertEqual(self.source.config["last_error"]["retry_after"], 37)

    def test_initial_sync_ignores_known_posts_older_than_seven_days(self):
        old = self.message(9)
        old = HistoryMessage(**{**old.__dict__, "published_at": timezone.now() - timedelta(days=8)})
        recent = self.message(10)
        report = TelegramReader(FakeHistoryClient([HistoryPage((old, recent),)])).sync_source(self.source)
        self.assertEqual(report.created, 1)
        self.assertTrue(SourceRecord.objects.get().external_id.startswith("10:"))


class TelegramSessionTests(TestCase):
    def test_session_path_must_be_private_and_created_with_restrictive_permissions(self):
        with TemporaryDirectory() as directory:
            private_root = Path(directory) / "private"
            with override_settings(PRIVATE_ROOT=private_root):
                path = private_session_path()
                self.assertTrue(path.parent.is_dir())
                self.assertTrue(path.is_relative_to(private_root.resolve()))
                if os.name != "nt":
                    self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                with self.assertRaises(TelegramConfigurationError):
                    private_session_path(Path(directory) / "outside" / "session")

    def test_runtime_status_is_honest_when_dependency_or_credentials_are_missing(self):
        with patch("jobs.sources.telegram.session.telethon_available", return_value=False):
            self.assertEqual(telethon_runtime_status({})["code"], "dependency_unavailable")
        with patch("jobs.sources.telegram.session.telethon_available", return_value=True):
            status = telethon_runtime_status({})
        self.assertEqual(status["code"], "config_unavailable")
        self.assertNotIn("secret", str(status).lower())

    def test_runtime_status_fails_closed_for_session_path_outside_private_root(self):
        with TemporaryDirectory() as directory, override_settings(PRIVATE_ROOT=Path(directory) / "private"):
            environ = {"TELEGRAM_API_ID": "42", "TELEGRAM_API_HASH": "configured", "TELEGRAM_SESSION_PATH": str(Path(directory) / "outside")}
            with patch("jobs.sources.telegram.session.telethon_available", return_value=True):
                status = telethon_runtime_status(environ)
        self.assertEqual(status["code"], "unsafe_session_path")


class TelegramAuthorizeCommandTests(TestCase):
    @patch("jobs.sources.telegram.management.commands.telegram_authorize.TelethonHistoryClient.from_environment")
    def test_command_runs_local_interactive_authorization_and_disconnects_without_echoing_values(self, from_environment):
        client = from_environment.return_value
        client.authorize_interactive.return_value = True
        output = StringIO()

        call_command("telegram_authorize", stdout=output)

        client.connect.assert_called_once_with()
        client.authorize_interactive.assert_called_once()
        callbacks = client.authorize_interactive.call_args.kwargs
        self.assertEqual(set(callbacks), {"phone_reader", "code_reader", "password_reader"})
        self.assertTrue(all(callable(callback) for callback in callbacks.values()))
        client.disconnect.assert_called_once_with()
        self.assertNotIn("api_hash", output.getvalue().casefold())
        self.assertEqual(output.getvalue(), "Telegram user-session авторизована локально.\n")

    @patch("jobs.sources.telegram.management.commands.telegram_authorize.TelethonHistoryClient.from_environment")
    def test_command_reports_only_safe_configuration_or_runtime_errors(self, from_environment):
        from_environment.side_effect = TelegramConfigurationError(
            "config_unavailable", "Локальные параметры Telegram API не настроены."
        )
        with self.assertRaises(CommandError) as configuration:
            call_command("telegram_authorize")
        self.assertIn("Локальные параметры", str(configuration.exception))

        client = from_environment.return_value = Mock()
        from_environment.side_effect = None
        client.authorize_interactive.side_effect = RuntimeError("phone code api_hash secret-marker")
        with self.assertRaises(CommandError) as runtime:
            call_command("telegram_authorize")
        self.assertNotIn("secret-marker", str(runtime.exception))
        client.disconnect.assert_called_once_with()


@override_settings(ROOT_URLCONF="jobs.tests.test_telegram", OWNER_USERNAME="owner")
class TelegramScreenTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="password")
        self.client.force_login(self.owner)

    def test_screen_adds_and_disables_source_and_reports_unverified_live_auth(self):
        with patch("jobs.sources.telegram.views.telethon_runtime_status", return_value={"code": "config_unavailable", "label": "Не настроено", "message": "Добавьте локальные параметры."}):
            response = self.client.post(reverse("manage"), {"action": "add", "link": "https://t.me/new_jobs"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "@new_jobs")
        self.assertContains(response, "Не настроено")
        self.assertContains(response, "не проверена")

        response = self.client.post(reverse("manage"), {"action": "disable", "link": "https://t.me/new_jobs"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Отключён")

    def test_invalid_link_is_local_accessible_error(self):
        response = self.client.post(reverse("manage"), {"action": "add", "link": "https://evil.test/channel"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Ссылка Telegram", status_code=400)
        self.assertContains(response, 'role="alert"', status_code=400)

    def test_malformed_runtime_fields_render_safely_without_leaking_or_500(self):
        source, _ = add_or_enable_source(self.owner, "https://t.me/malformed_ui")
        source.config = {
            **source.config,
            "telegram_checkpoint": ["raw-secret-marker"],
            "telegram_review": {"raw-secret-marker": True},
            "last_error": ["raw-secret-marker"],
        }
        source.save(update_fields=["config"])

        with patch("jobs.sources.telegram.views.telethon_runtime_status", return_value={
            "code": "config_unavailable", "label": "Не настроено", "message": "Добавьте локальные параметры."
        }):
            response = self.client.get(reverse("manage"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "raw-secret-marker")
        self.assertContains(response, "Ещё нет")


def _placeholder_view(request):
    return HttpResponse("ok")


from jobs.sources.telegram.views import telegram_sources_view

urlpatterns = [
    path("", _placeholder_view, name="vacancies"),
    path("profile/", _placeholder_view, name="profile"),
    path("sources/", _placeholder_view, name="sources"),
    path("accounts/logout/", _placeholder_view, name="logout"),
    path("sources/telegram/", telegram_sources_view, name="manage"),
]
