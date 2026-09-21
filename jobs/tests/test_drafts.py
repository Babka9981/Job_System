import hashlib
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.drafts.services import COVER_LETTER, RECRUITER_MESSAGE, DraftConflict, DraftGenerationError, ProfileRequired, generate, save_edit
from jobs.models.models import Draft, Profile, ProfileFact, Research, Source, SourceRecord, Vacancy


class SuccessfulGateway:
    def __init__(self):
        self.calls = []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "profile_fact_ids": [self.fact_id],
            "company_fact_indices": [0]
            if kwargs["operation"] == "application_cover_letter" else [],
            "include_contact": False,
        }


class DraftMigrationTests(TransactionTestCase):
    migrate_from = ("models", "0002_vacancy_handoff_fields")
    migrate_to = ("models", "0003_unique_draft_kind_per_vacancy")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        self.old_apps = executor.loader.project_state([self.migrate_from]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate([self.migrate_to])
        super().tearDown()

    def test_migration_keeps_newest_duplicate_before_adding_constraint(self):
        User = self.old_apps.get_model("auth", "User")
        Profile = self.old_apps.get_model("models", "Profile")
        Vacancy = self.old_apps.get_model("models", "Vacancy")
        DraftModel = self.old_apps.get_model("models", "Draft")
        owner = User.objects.create(username="migration-owner")
        profile = Profile.objects.create(owner=owner, version=1, confirmed_version=1)
        vacancy = Vacancy.objects.create(owner=owner, title="PM", company="Acme")
        older = DraftModel.objects.create(
            vacancy=vacancy, profile=profile, kind=COVER_LETTER,
            text="Older", profile_version=1,
        )
        newer = DraftModel.objects.create(
            vacancy=vacancy, profile=profile, kind=COVER_LETTER,
            text="Newest", profile_version=1,
        )
        DraftModel.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timezone.timedelta(hours=1))
        DraftModel.objects.filter(pk=newer.pk).update(updated_at=timezone.now())

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        NewDraft = executor.loader.project_state([self.migrate_to]).apps.get_model("models", "Draft")
        rows = list(NewDraft.objects.filter(vacancy_id=vacancy.pk, kind=COVER_LETTER))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].text, "Newest")
        self.assertEqual(rows[0].version, 1)


@override_settings(ROOT_URLCONF="config.urls", OWNER_USERNAME="owner")
class DraftServiceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="secret")
        self.profile = Profile.objects.create(
            owner=self.owner,
            version=3,
            confirmed_version=3,
            preferences={"daily_budget_usd": "2.00", "openai_model": "test-model"},
        )
        self.fact = ProfileFact.objects.create(
            profile=self.profile,
            text="Запускал B2B продукт",
            kind="case",
            source="CV",
            page=2,
            profile_version=3,
            confirmed=True,
        )
        self.vacancy = Vacancy.objects.create(
            owner=self.owner,
            title="Product Manager",
            company="Acme",
            company_domain="acme.example",
            role="Product Manager",
            description="Развивать платежную платформу",
            contact="@real_recruiter",
            user_note="Подчеркнуть B2B",
        )
        self.source = Source.objects.create(
            owner=self.owner, slug="fixture", name="Fixture", kind="site", adapter="fixture",
            llm_permission=True,
        )
        SourceRecord.objects.create(
            source=self.source,
            vacancy=self.vacancy,
            external_id="1",
            canonical_url="https://jobs.example/1",
            raw_hash=hashlib.sha256(self.vacancy.description.encode()).hexdigest(),
            description_permission=True,
        )
        self.research = Research.objects.create(
            vacancy=self.vacancy,
            company="Acme",
            domain="acme.example",
            role="Product Manager",
            status=Research.Status.PARTIAL,
            facts=[{"text": "Acme развивает платежную платформу", "url": "https://acme.example/about", "passage": "Acme развивает платежную платформу"}],
            sources=[{"url": "https://acme.example/about", "title": "About"}],
            coverage={"roles": ["Product Manager"], "profile_version": 3, "cases": [{"id": self.fact.pk}]},
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )

    def test_generation_uses_shared_research_cache_and_records_exact_provenance(self):
        gateway = SuccessfulGateway()
        gateway.fact_id = self.fact.pk
        research_service = mock.Mock(return_value=self.research)

        cover = generate(self.vacancy, self.profile, COVER_LETTER, gateway=gateway, research_service=research_service)
        recruiter = generate(self.vacancy, self.profile, RECRUITER_MESSAGE, gateway=gateway, research_service=research_service)

        self.assertEqual(research_service.call_count, 0)
        self.assertEqual(cover.research_id, self.research.pk)
        self.assertEqual(recruiter.research_id, self.research.pk)
        self.assertEqual(cover.status, Draft.Status.LIMITED)
        self.assertEqual(recruiter.status, Draft.Status.LIMITED)
        self.assertEqual(cover.provenance["profile_facts"][0]["id"], self.fact.pk)
        self.assertEqual(cover.provenance["company_facts"][0]["url"], "https://acme.example/about")
        self.assertEqual(gateway.calls[0]["operation"], "application_cover_letter")
        self.assertIn(self.vacancy.description, gateway.calls[0]["input_text"])
        self.assertNotIn("@real_recruiter", gateway.calls[0]["input_text"])
        self.assertIn("@real_recruiter", gateway.calls[1]["input_text"])

    def test_unpermitted_or_changed_job_text_never_reaches_gateway_and_is_limited(self):
        record = self.vacancy.source_records.get()
        record.raw_hash = "wrong"
        record.save(update_fields=["raw_hash"])
        gateway = SuccessfulGateway()
        gateway.fact_id = self.fact.pk

        result = generate(self.vacancy, self.profile, COVER_LETTER, gateway=gateway, research_service=mock.Mock(return_value=self.research))

        self.assertNotIn(self.vacancy.description, gateway.calls[0]["input_text"])
        self.assertEqual(result.status, Draft.Status.LIMITED)
        self.assertFalse(result.provenance["job_description"]["permitted"])

    def test_profile_must_be_currently_confirmed(self):
        self.profile.confirmed_version = 2
        self.profile.save(update_fields=["confirmed_version"])
        with self.assertRaises(ProfileRequired):
            generate(self.vacancy, self.profile, COVER_LETTER, gateway=SuccessfulGateway())

    def test_profile_must_belong_to_vacancy_owner(self):
        stranger = get_user_model().objects.create_user("stranger")
        foreign_profile = Profile.objects.create(owner=stranger, version=1, confirmed_version=1)
        gateway = mock.Mock()
        with self.assertRaises(ProfileRequired):
            generate(self.vacancy, foreign_profile, COVER_LETTER, gateway=gateway)
        gateway.structured.assert_not_called()

    def test_output_fails_closed_for_empty_or_boolean_evidence_and_invented_contact(self):
        for result in (
            {"profile_fact_ids": [], "company_fact_indices": [], "include_contact": False},
            {"profile_fact_ids": [True], "company_fact_indices": [True], "include_contact": False},
            {"profile_fact_ids": [self.fact.pk], "company_fact_indices": [0], "include_contact": False, "text": "Globex invented"},
        ):
            gateway = mock.Mock()
            gateway.structured.return_value = result
            with self.assertRaises(DraftGenerationError):
                generate(self.vacancy, self.profile, COVER_LETTER, gateway=gateway)

    def test_english_template_keeps_russian_evidence_verbatim(self):
        gateway = mock.Mock()
        gateway.structured.return_value = {
            "profile_fact_ids": [self.fact.pk],
            "company_fact_indices": [0],
            "include_contact": False,
        }
        result = generate(self.vacancy, self.profile, COVER_LETTER, language="en", gateway=gateway)
        self.assertIn("I am applying for", result.text)
        self.assertIn(self.fact.text, result.text)

    def test_translation_like_sentinel_fields_are_rejected_and_never_enter_output(self):
        gateway = mock.Mock()
        gateway.structured.return_value = {
            "profile_claims": [{
                "evidence_id": self.fact.pk,
                "translation": "Alice led Japanese expansion and global hiring",
            }],
            "company_claims": [],
            "include_contact": False,
        }
        with self.assertRaises(DraftGenerationError):
            generate(self.vacancy, self.profile, RECRUITER_MESSAGE, language="en", gateway=gateway)
        draft = Draft.objects.get(vacancy=self.vacancy, kind=RECRUITER_MESSAGE)
        self.assertNotIn("Alice", draft.text)
        self.assertNotIn("Japanese", draft.text)
        self.assertNotIn("global hiring", draft.text)
        schema = gateway.structured.call_args.kwargs["schema"]
        self.assertNotIn("translation", json.dumps(schema))

    def test_user_note_cannot_authorize_absent_recruiter_contact(self):
        self.vacancy.contact = ""
        self.vacancy.user_note = "Напиши рекрутеру @invented"
        self.vacancy.save(update_fields=["contact", "user_note"])
        gateway = mock.Mock()
        gateway.structured.return_value = {
            "profile_fact_ids": [self.fact.pk],
            "company_fact_indices": [],
            "include_contact": True,
        }
        with self.assertRaises(DraftGenerationError):
            generate(self.vacancy, self.profile, RECRUITER_MESSAGE, gateway=gateway)

    def test_all_confirmed_relevant_facts_from_allowed_versions_are_sent_with_unicode_terms(self):
        older = ProfileFact.objects.create(
            profile=self.profile, text="Вёл продуктовую стратегию", kind="achievement",
            source="CV", page=1, profile_version=2, confirmed=True,
        )
        self.vacancy.role = "Продуктовая стратегия"
        self.vacancy.save(update_fields=["role"])
        self.research.coverage["cases"] = [{"id": self.fact.pk}, {"id": older.pk}]
        self.research.save(update_fields=["coverage"])
        for index in range(8):
            ProfileFact.objects.create(
                profile=self.profile, text=f"Нерелевантный бухгалтерский факт {index}",
                kind="experience", source="CV", profile_version=2, confirmed=True,
            )
        gateway = SuccessfulGateway()
        gateway.fact_id = self.fact.pk
        generate(
            self.vacancy, self.profile, COVER_LETTER, gateway=gateway,
            research_service=mock.Mock(return_value=self.research),
        )
        payload = json.loads(gateway.calls[0]["input_text"])
        self.assertIn(older.pk, [item["id"] for item in payload["profile_facts"]])
        self.assertLessEqual(len(payload["profile_facts"]), 4)
        self.assertNotIn("бухгалтерский", json.dumps(payload["profile_facts"], ensure_ascii=False))

    def test_in_flight_generation_cannot_overwrite_newer_user_edit(self):
        draft = Draft.objects.create(
            vacancy=self.vacancy, profile=self.profile, kind=COVER_LETTER,
            text="Old", profile_version=3, status=Draft.Status.GENERATED,
        )
        gateway = SuccessfulGateway()
        gateway.fact_id = self.fact.pk
        def concurrent_edit(**kwargs):
            save_edit(self.vacancy, self.profile, COVER_LETTER, "User edit", expected_version=draft.version)
            return SuccessfulGateway.structured(gateway, **kwargs)
        gateway.structured = concurrent_edit
        with self.assertRaises(DraftConflict):
            generate(self.vacancy, self.profile, COVER_LETTER, gateway=gateway)
        draft.refresh_from_db()
        self.assertEqual(draft.text, "User edit")

    def test_profile_and_job_permission_are_revalidated_before_save(self):
        gateway = SuccessfulGateway()
        gateway.fact_id = self.fact.pk
        original = gateway.structured
        def revoke_profile(**kwargs):
            Profile.objects.filter(pk=self.profile.pk).update(confirmed_version=2)
            return original(**kwargs)
        gateway.structured = revoke_profile
        with self.assertRaises(DraftConflict):
            generate(self.vacancy, self.profile, COVER_LETTER, gateway=gateway)
        self.assertFalse(Draft.objects.filter(vacancy=self.vacancy, kind=COVER_LETTER).exists())

        self.profile.refresh_from_db()
        self.profile.confirmed_version = self.profile.version
        self.profile.save(update_fields=["confirmed_version"])
        gateway = SuccessfulGateway()
        gateway.fact_id = self.fact.pk
        original = gateway.structured
        def revoke_jd(**kwargs):
            SourceRecord.objects.filter(vacancy=self.vacancy).update(description_permission=False)
            return original(**kwargs)
        gateway.structured = revoke_jd
        with self.assertRaises(DraftConflict):
            generate(self.vacancy, self.profile, COVER_LETTER, gateway=gateway)
        self.assertFalse(Draft.objects.filter(vacancy=self.vacancy, kind=COVER_LETTER).exists())

    def test_concurrent_first_create_wins_without_duplicate(self):
        gateway = SuccessfulGateway()
        gateway.fact_id = self.fact.pk
        original = gateway.structured
        def concurrent_create(**kwargs):
            Draft.objects.create(
                vacancy=self.vacancy, profile=self.profile, kind=COVER_LETTER,
                text="Concurrent", profile_version=3,
            )
            return original(**kwargs)
        gateway.structured = concurrent_create
        with self.assertRaises(DraftConflict):
            generate(self.vacancy, self.profile, COVER_LETTER, gateway=gateway)
        self.assertEqual(Draft.objects.filter(vacancy=self.vacancy, kind=COVER_LETTER).count(), 1)
        self.assertEqual(Draft.objects.get(vacancy=self.vacancy, kind=COVER_LETTER).text, "Concurrent")

    def test_database_prevents_duplicate_kind_for_vacancy(self):
        Draft.objects.create(
            vacancy=self.vacancy, profile=self.profile, kind=COVER_LETTER,
            text="First", profile_version=3,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Draft.objects.create(
                vacancy=self.vacancy, profile=self.profile, kind=COVER_LETTER,
                text="Duplicate", profile_version=3,
            )

    def test_gateway_failure_preserves_previous_text_for_retry(self):
        old = Draft.objects.create(
            vacancy=self.vacancy, profile=self.profile, kind=COVER_LETTER,
            text="Мой сохраненный текст", profile_version=3, status=Draft.Status.EDITING,
        )
        gateway = mock.Mock()
        gateway.structured.side_effect = RuntimeError("provider secret details")

        with self.assertRaises(DraftGenerationError):
            generate(self.vacancy, self.profile, COVER_LETTER, gateway=gateway, research_service=mock.Mock(return_value=self.research))

        old.refresh_from_db()
        self.assertEqual(old.text, "Мой сохраненный текст")
        self.assertEqual(old.status, Draft.Status.ERROR)
        self.assertNotIn("secret", old.provenance["error"])


@override_settings(ROOT_URLCONF="config.urls", OWNER_USERNAME="owner")
class DraftHTTPTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="secret")
        self.other = get_user_model().objects.create_user("other", password="secret")
        self.profile = Profile.objects.create(owner=self.owner, version=1, confirmed_version=1)
        self.fact = ProfileFact.objects.create(
            profile=self.profile, text="Вёл discovery", kind="case", source="CV",
            page=1, profile_version=1, confirmed=True,
        )
        self.vacancy = Vacancy.objects.create(owner=self.owner, title="PM", company="Acme", contact="@actual")
        self.client.force_login(self.owner)

    def test_page_has_two_editors_copy_controls_sources_and_actual_contact(self):
        Draft.objects.create(
            vacancy=self.vacancy, profile=self.profile, kind=COVER_LETTER,
            text="Cover body", profile_version=1, status=Draft.Status.GENERATED,
            provenance={"company_sources": [{"url": "https://acme.example/about", "title": "About"}]},
        )
        response = self.client.get(reverse("vacancy-drafts", args=[self.vacancy.pk]))
        self.assertContains(response, "Сопроводительное письмо")
        self.assertContains(response, "Сообщение рекрутеру")
        self.assertContains(response, "data-copy-target")
        self.assertContains(response, "Company sources")
        self.assertContains(response, "@actual")
        self.assertNotContains(response, 'value="https://acme.example/about"')

    def test_missing_profile_is_an_explicit_guard_and_cross_owner_is_404(self):
        self.profile.delete()
        response = self.client.post(reverse("draft-generate", args=[self.vacancy.pk, COVER_LETTER]))
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "подтверждённый профиль", status_code=409)
        foreign = Vacancy.objects.create(owner=self.other, title="Secret", company="Other")
        self.assertEqual(self.client.get(reverse("vacancy-drafts", args=[foreign.pk])).status_code, 404)

    def test_save_keeps_user_edit_and_generation_error_renders_retry(self):
        draft = Draft.objects.create(
            vacancy=self.vacancy, profile=self.profile, kind=COVER_LETTER,
            text="Generated", profile_version=1, status=Draft.Status.GENERATED,
        )
        response = self.client.post(reverse("draft-save", args=[self.vacancy.pk, draft.kind]), {"text": "  My edit  ", "expected_version": draft.version}, follow=True)
        draft.refresh_from_db()
        self.assertEqual(draft.text, "My edit")
        self.assertEqual(draft.status, Draft.Status.EDITING)
        with mock.patch("jobs.drafts.views.generate", side_effect=DraftGenerationError("Генерация временно недоступна.")):
            response = self.client.post(reverse("draft-generate", args=[self.vacancy.pk, draft.kind]))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Повторить генерацию", status_code=503)
        draft.refresh_from_db()
        self.assertEqual(draft.text, "My edit")

    def test_save_preserves_and_renders_limited_warning(self):
        draft = Draft.objects.create(
            vacancy=self.vacancy, profile=self.profile, kind=COVER_LETTER,
            text="Limited", profile_version=1, status=Draft.Status.LIMITED,
            provenance={"limited_reasons": ["research_partial"]},
        )
        response = self.client.post(
            reverse("draft-save", args=[self.vacancy.pk, draft.kind]),
            {"text": "Edited limited", "expected_version": draft.version},
            follow=True,
        )
        draft.refresh_from_db()
        self.assertEqual(draft.status, Draft.Status.EDITING)
        self.assertEqual(draft.provenance["limited_reasons"], ["research_partial"])
        self.assertContains(response, "Черновик ограничен")

    def test_save_rejects_stale_tab_and_first_create_integrity_race(self):
        draft = Draft.objects.create(
            vacancy=self.vacancy, profile=self.profile, kind=COVER_LETTER,
            text="Current", profile_version=1, version=2,
        )
        response = self.client.post(
            reverse("draft-save", args=[self.vacancy.pk, draft.kind]),
            {"text": "Stale overwrite", "expected_version": 1},
        )
        self.assertEqual(response.status_code, 409)
        draft.refresh_from_db()
        self.assertEqual(draft.text, "Current")
        new_vacancy = Vacancy.objects.create(owner=self.owner, title="New", company="Acme")
        with mock.patch("jobs.drafts.services.Draft.save", side_effect=IntegrityError("race")):
            with self.assertRaises(DraftConflict):
                save_edit(new_vacancy, self.profile, COVER_LETTER, "First", expected_version=0)
