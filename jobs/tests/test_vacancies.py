from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models.models import Source, Vacancy
from jobs.vacancies.services import VersionConflict, change_status, upsert_record


class VacancyServiceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="password")
        self.source = Source.objects.create(
            owner=self.owner,
            slug="fixture",
            name="Fixture",
            kind="fixture",
            adapter="fixture",
            status=Source.Status.READY,
        )

    def record(self, **overrides):
        data = {
            "source_slug": "fixture",
            "external_id": "job-42",
            "canonical_url": "https://jobs.example.test/job-42",
            "apply_url": "https://jobs.example.test/job-42/apply",
            "title": "Product Manager",
            "company": "Example",
            "description": "Build a payments product",
            "description_permission": True,
            "published_at": None,
            "role": "Product Manager",
            "work_arrangement": "remote",
            "country_restrictions": ["worldwide"],
            "timezone_restrictions": [],
            "salary": {},
            "raw_hash": "record-hash-42",
            "attribution": {"label": "Fixture", "url": "https://jobs.example.test"},
        }
        data.update(overrides)
        return data

    def test_upsert_record_creates_normalized_vacancy_and_source_link(self):
        result = upsert_record(self.record(), owner=self.owner)

        vacancy = Vacancy.objects.get()
        self.assertEqual(result.vacancy, vacancy)
        self.assertTrue(result.created)
        self.assertEqual(vacancy.title, "Product Manager")
        self.assertEqual(vacancy.priority, Vacancy.Priority.REMOTE)
        self.assertIsNone(vacancy.published_at)
        self.assertEqual(vacancy.source_records.get().canonical_url, "https://jobs.example.test/job-42")

    def test_confident_cross_source_duplicate_keeps_user_state_and_all_links(self):
        first = upsert_record(self.record(), owner=self.owner).vacancy
        first.user_status = Vacancy.UserStatus.SAVED
        first.user_note = "Сильный продуктовый матч"
        first.hidden = True
        first.save()
        Source.objects.create(
            owner=self.owner,
            slug="second",
            name="Second",
            kind="fixture",
            adapter="fixture",
            status=Source.Status.READY,
        )

        result = upsert_record(
            self.record(
                source_slug="second",
                external_id="another-id",
                canonical_url="https://second.example.test/jobs/99",
            ),
            owner=self.owner,
        )

        self.assertFalse(result.created)
        self.assertTrue(result.linked)
        self.assertEqual(Vacancy.objects.count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.user_status, Vacancy.UserStatus.SAVED)
        self.assertEqual(first.user_note, "Сильный продуктовый матч")
        self.assertTrue(first.hidden)
        self.assertEqual(first.source_records.count(), 2)

    def test_company_title_or_shared_text_do_not_merge_different_countries(self):
        upsert_record(self.record(country_restrictions=["US"]), owner=self.owner)
        Source.objects.create(
            owner=self.owner,
            slug="second",
            name="Second",
            kind="fixture",
            adapter="fixture",
            status=Source.Status.READY,
        )

        upsert_record(
            self.record(
                source_slug="second",
                external_id="country-specific",
                canonical_url="https://second.example.test/jobs/country-specific",
                country_restrictions=["DE"],
            ),
            owner=self.owner,
        )

        self.assertEqual(Vacancy.objects.count(), 2)

    def test_same_source_without_external_id_is_idempotent_by_exact_url(self):
        record = self.record(external_id="", raw_hash="first-content")
        first = upsert_record(record, owner=self.owner)
        second = upsert_record({**record, "raw_hash": "changed-content"}, owner=self.owner)

        self.assertEqual(second.vacancy.id, first.vacancy.id)
        self.assertEqual(second.source_record.id, first.source_record.id)
        self.assertEqual((Vacancy.objects.count(), self.source.records.count()), (1, 1))

    def test_cross_source_exact_url_never_merges_without_structured_evidence(self):
        for index, url in enumerate(("https://jobs.example.test/jobs", "https://jobs.example.test/jobs/product-manager-42")):
            with self.subTest(url=url):
                first_source = Source.objects.create(owner=self.owner, slug=f"first-{index}", name="First", kind="fixture", adapter="fixture")
                second_source = Source.objects.create(owner=self.owner, slug=f"second-{index}", name="Second", kind="fixture", adapter="fixture")
                before = Vacancy.objects.count()
                upsert_record(self.record(source_slug=first_source.slug, external_id="one", canonical_url=url, apply_url="", raw_hash=f"one-{index}"), owner=self.owner)
                upsert_record(self.record(source_slug=second_source.slug, external_id="two", canonical_url=url, apply_url="", raw_hash=f"two-{index}"), owner=self.owner)
                self.assertEqual(Vacancy.objects.count() - before, 2)

    def test_adapter_confirmed_permalink_merges_only_when_both_adapters_confirm(self):
        first_source = Source.objects.create(owner=self.owner, slug="first", name="First", kind="fixture", adapter="fixture")
        second_source = Source.objects.create(owner=self.owner, slug="second", name="Second", kind="fixture", adapter="fixture")
        url = "https://jobs.example.test/jobs/product-manager-42"
        first = upsert_record(self.record(source_slug="first", external_id="one", canonical_url=url, raw_hash="one", adapter_confirmed_permalink=True), owner=self.owner)
        second = upsert_record(self.record(source_slug="second", external_id="two", canonical_url=url, raw_hash="two", adapter_confirmed_permalink=True), owner=self.owner)

        self.assertEqual(second.vacancy.id, first.vacancy.id)
        self.assertTrue(second.linked)
        self.assertTrue(first.source_record.adapter_confirmed_permalink)
        self.assertEqual(Vacancy.objects.count(), 1)

    def test_permalink_evidence_rejects_truthy_non_boolean_values(self):
        for index, untrusted_value in enumerate(("true", 1)):
            with self.subTest(value=untrusted_value):
                Source.objects.create(owner=self.owner, slug=f"first-{index}", name="First", kind="fixture", adapter="fixture")
                Source.objects.create(owner=self.owner, slug=f"second-{index}", name="Second", kind="fixture", adapter="fixture")
                url = f"https://jobs.example.test/jobs/product-manager-{index}"
                first = upsert_record(
                    self.record(source_slug=f"first-{index}", external_id="one", canonical_url=url, raw_hash=f"one-{index}", adapter_confirmed_permalink=True),
                    owner=self.owner,
                )
                second = upsert_record(
                    self.record(source_slug=f"second-{index}", external_id="two", canonical_url=url, raw_hash=f"two-{index}", adapter_confirmed_permalink=untrusted_value),
                    owner=self.owner,
                )

                self.assertNotEqual(second.vacancy.id, first.vacancy.id)
                self.assertFalse(second.source_record.adapter_confirmed_permalink)
        self.assertEqual(Vacancy.objects.count(), 4)

    def test_changed_canonical_url_drops_stale_permalink_evidence(self):
        first = upsert_record(self.record(adapter_confirmed_permalink=True), owner=self.owner)

        updated = upsert_record(
            self.record(canonical_url="https://jobs.example.test/new-url", adapter_confirmed_permalink="true"),
            owner=self.owner,
        )

        self.assertEqual(updated.source_record.id, first.source_record.id)
        self.assertEqual(updated.source_record.canonical_url, "https://jobs.example.test/new-url")
        self.assertFalse(updated.source_record.adapter_confirmed_permalink)

    def test_literal_true_reconfirms_permalink_for_changed_url(self):
        first = upsert_record(self.record(adapter_confirmed_permalink=True), owner=self.owner)

        updated = upsert_record(
            self.record(canonical_url="https://jobs.example.test/new-url", adapter_confirmed_permalink=True),
            owner=self.owner,
        )

        self.assertEqual(updated.source_record.id, first.source_record.id)
        self.assertEqual(updated.source_record.canonical_url, "https://jobs.example.test/new-url")
        self.assertTrue(updated.source_record.adapter_confirmed_permalink)

    def test_adapter_evidence_does_not_override_identity_or_geography_conflict(self):
        Source.objects.create(owner=self.owner, slug="first", name="First", kind="fixture", adapter="fixture")
        Source.objects.create(owner=self.owner, slug="second", name="Second", kind="fixture", adapter="fixture")
        url = "https://jobs.example.test/jobs/product-manager-42"
        upsert_record(self.record(source_slug="first", external_id="one", canonical_url=url, raw_hash="shared", adapter_confirmed_permalink=True, country_restrictions=["US"]), owner=self.owner)
        upsert_record(self.record(source_slug="second", external_id="two", canonical_url=url, raw_hash="shared", adapter_confirmed_permalink=True, country_restrictions=["DE"]), owner=self.owner)

        self.assertEqual(Vacancy.objects.count(), 2)

    def test_content_evidence_does_not_override_identity_conflict(self):
        upsert_record(self.record(raw_hash="shared-content"), owner=self.owner)
        Source.objects.create(owner=self.owner, slug="second", name="Second", kind="fixture", adapter="fixture")

        upsert_record(
            self.record(
                source_slug="second",
                external_id="other",
                canonical_url="https://second.example.test/other",
                raw_hash="shared-content",
                title="Engineering Manager",
            ),
            owner=self.owner,
        )

        self.assertEqual(Vacancy.objects.count(), 2)

    def test_ambiguous_content_evidence_never_merges(self):
        for index in range(2):
            source = Source.objects.create(owner=self.owner, slug=f"candidate-{index}", name="Candidate", kind="fixture", adapter="fixture")
            vacancy = Vacancy.objects.create(owner=self.owner, title="Product Manager", company="Example")
            vacancy.source_records.create(
                source=source,
                external_id=f"candidate-{index}",
                canonical_url=f"https://candidate-{index}.example.test/job",
                raw_hash="ambiguous-content",
            )
        Source.objects.create(owner=self.owner, slug="incoming", name="Incoming", kind="fixture", adapter="fixture")

        result = upsert_record(
            self.record(source_slug="incoming", external_id="incoming", raw_hash="ambiguous-content"),
            owner=self.owner,
        )

        self.assertTrue(result.created)
        self.assertFalse(result.linked)
        self.assertEqual(Vacancy.objects.count(), 3)

    def test_matching_raw_hash_merges_compatible_identity_without_matching_apply_url(self):
        first = upsert_record(self.record(), owner=self.owner).vacancy
        Source.objects.create(owner=self.owner, slug="second", name="Second", kind="fixture", adapter="fixture")

        result = upsert_record(
            self.record(
                source_slug="second",
                external_id="raw-match",
                canonical_url="https://second.example.test/raw-match",
                apply_url="https://second.example.test/apply/raw-match",
            ),
            owner=self.owner,
        )

        self.assertEqual(result.vacancy.id, first.id)
        self.assertTrue(result.linked)
        self.assertEqual(first.source_records.count(), 2)

    def test_unconfirmed_matching_apply_url_does_not_merge_with_changed_content(self):
        first = upsert_record(self.record(), owner=self.owner).vacancy
        Source.objects.create(owner=self.owner, slug="second", name="Second", kind="fixture", adapter="fixture")

        result = upsert_record(
            self.record(
                source_slug="second",
                external_id="apply-match",
                canonical_url="https://second.example.test/apply-match",
                raw_hash="new-content-version",
            ),
            owner=self.owner,
        )

        self.assertNotEqual(result.vacancy.id, first.id)
        self.assertFalse(result.linked)
        self.assertEqual(Vacancy.objects.count(), 2)

    def test_sparse_repeat_record_preserves_known_optional_fields(self):
        published = timezone.now() - timezone.timedelta(days=2)
        rich = self.record(
            description="Detailed role description",
            role="Product Manager",
            industry="Fintech",
            work_arrangement="remote",
            country_restrictions=["worldwide"],
            timezone_restrictions=["UTC+3"],
            salary={"min": "5000", "currency": "USD", "period": "month", "basis": "gross"},
            contact="@recruiter",
            language="EN",
            published_at=published,
        )
        vacancy = upsert_record(rich, owner=self.owner).vacancy

        sparse = {
            "source_slug": "fixture",
            "external_id": "job-42",
            "canonical_url": "https://jobs.example.test/job-42",
            "title": "Product Manager",
            "company": "Example",
            "description": "",
            "country_restrictions": [],
            "timezone_restrictions": [],
            "salary": {},
            "published_at": None,
            "raw_hash": "sparse-version",
        }
        upsert_record(sparse, owner=self.owner)

        vacancy.refresh_from_db()
        self.assertEqual(vacancy.description, "Detailed role description")
        self.assertEqual(vacancy.role, "Product Manager")
        self.assertEqual(vacancy.industry, "Fintech")
        self.assertEqual(vacancy.work_arrangement, "remote")
        self.assertEqual(vacancy.country_restrictions, ["worldwide"])
        self.assertEqual(vacancy.timezone_restrictions, ["UTC+3"])
        self.assertEqual(str(vacancy.salary_min), "5000.00")
        self.assertEqual(vacancy.salary_currency, "USD")
        self.assertEqual(vacancy.contact, "@recruiter")
        self.assertEqual(vacancy.language, "EN")
        self.assertEqual(vacancy.published_at, published)

    def test_sparse_repeat_preserves_known_source_record_metadata(self):
        expires = timezone.now() + timezone.timedelta(days=2)
        result = upsert_record(
            self.record(
                apply_url="https://employer.example.test/apply/42",
                description_permission=True,
                attribution={"label": "Required attribution", "url": "https://source.example.test"},
                expires_at=expires,
            ),
            owner=self.owner,
        )

        upsert_record(
            {
                "source_slug": "fixture",
                "external_id": "job-42",
                "canonical_url": "https://jobs.example.test/job-42",
                "title": "Product Manager",
                "company": "Example",
                "salary": {},
            },
            owner=self.owner,
        )

        result.source_record.refresh_from_db()
        self.assertEqual(result.source_record.apply_url, "https://employer.example.test/apply/42")
        self.assertTrue(result.source_record.description_permission)
        self.assertEqual(result.source_record.attribution["label"], "Required attribution")
        self.assertEqual(result.source_record.expires_at, expires)
        self.assertEqual(result.source_record.raw_hash, "record-hash-42")

    def test_description_permission_create_accepts_only_literal_true(self):
        untrusted_values = ("false", "true", "0", 1, ["true"])
        for index, value in enumerate(untrusted_values):
            with self.subTest(value=value):
                source = Source.objects.create(
                    owner=self.owner,
                    slug=f"permission-{index}",
                    name="Permission fixture",
                    kind="fixture",
                    adapter="fixture",
                )
                result = upsert_record(
                    self.record(
                        source_slug=source.slug,
                        external_id=f"permission-{index}",
                        canonical_url=f"https://jobs.example.test/permission-{index}",
                        raw_hash=f"permission-{index}",
                        description_permission=value,
                    ),
                    owner=self.owner,
                )
                self.assertFalse(result.source_record.description_permission)

    def test_description_permission_update_accepts_only_literal_true(self):
        result = upsert_record(self.record(description_permission=True), owner=self.owner)

        result = upsert_record(self.record(description_permission="true"), owner=self.owner)

        self.assertFalse(result.source_record.description_permission)

    def test_changed_raw_content_drops_permission_without_new_literal_true(self):
        result = upsert_record(self.record(description_permission=True), owner=self.owner)

        result = upsert_record(
            self.record(raw_hash="new-content", description="New description", description_permission="true"),
            owner=self.owner,
        )

        self.assertEqual(result.source_record.raw_hash, "new-content")
        self.assertFalse(result.source_record.description_permission)

    def test_changed_description_drops_permission_when_hash_is_absent(self):
        result = upsert_record(self.record(description_permission=True), owner=self.owner)
        sparse_change = self.record(description="Changed without a supplied hash")
        sparse_change.pop("raw_hash")
        sparse_change.pop("description_permission")

        result = upsert_record(sparse_change, owner=self.owner)

        self.assertFalse(result.source_record.description_permission)

    def test_status_change_requires_closing_note_and_rejects_stale_version(self):
        vacancy = upsert_record(self.record(), owner=self.owner).vacancy
        with self.assertRaises(ValidationError):
            change_status(vacancy.id, vacancy.version, Vacancy.UserStatus.CLOSED, owner=self.owner)

        changed = change_status(
            vacancy.id,
            vacancy.version,
            Vacancy.UserStatus.CLOSED,
            "Роль закрыта работодателем",
            owner=self.owner,
        )
        self.assertEqual(changed.user_status, Vacancy.UserStatus.CLOSED)
        self.assertEqual(changed.closing_note, "Роль закрыта работодателем")
        self.assertEqual(changed.version, vacancy.version + 1)

        with self.assertRaises(VersionConflict):
            change_status(vacancy.id, vacancy.version, Vacancy.UserStatus.SAVED, owner=self.owner)
        changed.refresh_from_db()
        self.assertEqual(changed.user_status, Vacancy.UserStatus.CLOSED)

    def test_upsert_rejects_non_http_source_urls(self):
        with self.assertRaises(ValidationError):
            upsert_record(self.record(canonical_url="ftp://jobs.example.test/job-42"), owner=self.owner)


@override_settings(ROOT_URLCONF="config.urls", OWNER_USERNAME="owner")
class VacancyHttpTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="password")
        self.client.force_login(self.owner)

    def filter_fixture(self):
        target = Vacancy.objects.create(
            owner=self.owner,
            title="Target vacancy",
            company="Example",
            role="Product Manager",
            priority=Vacancy.Priority.REMOTE,
            user_status=Vacancy.UserStatus.SAVED,
        )
        other = Vacancy.objects.create(
            owner=self.owner,
            title="Other vacancy",
            company="Another",
            role="Project Manager",
            priority=Vacancy.Priority.RELOCATION,
            user_status=Vacancy.UserStatus.NEW,
        )
        target_source = Source.objects.create(owner=self.owner, slug="target-source", name="Target source", kind="fixture", adapter="fixture")
        other_source = Source.objects.create(owner=self.owner, slug="other-source", name="Other source", kind="fixture", adapter="fixture")
        target.source_records.create(source=target_source, external_id="target", canonical_url="https://example.test/target", raw_hash="target")
        other.source_records.create(source=other_source, external_id="other", canonical_url="https://example.test/other", raw_hash="other")
        return target, other

    def test_manual_add_records_permission_provenance_and_sanitizes_html(self):
        response = self.client.post(
            reverse("vacancy-add"),
            {
                "url": "https://careers.example.test/product-manager",
                "text": "<p>Build payments</p><script>alert('x')</script><b>Worldwide</b>",
                "description_permission": "on",
                "title": "Product Manager",
                "company": "Example",
            },
        )

        vacancy = Vacancy.objects.get()
        self.assertRedirects(
            response,
            reverse("vacancy-detail", args=[vacancy.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(vacancy.description, "Build payments\nWorldwide")
        record = vacancy.source_records.get()
        self.assertTrue(record.description_permission)
        self.assertEqual(record.attribution["method"], "manual")
        self.assertNotIn("script", vacancy.description)

    def test_manual_url_only_is_saved_with_explicit_unknown_identity(self):
        response = self.client.post(
            reverse("vacancy-add"),
            {"url": "https://careers.example.test/unknown-role"},
        )

        vacancy = Vacancy.objects.get()
        self.assertRedirects(
            response,
            reverse("vacancy-detail", args=[vacancy.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(vacancy.title, "Название не указано")
        self.assertEqual(vacancy.company, "Компания не указана")
        self.assertEqual(vacancy.description, "")

    def test_empty_manual_descriptions_are_not_dedup_evidence(self):
        common = {
            "title": "Product Manager",
            "company": "Example",
            "text": "   \n\t ",
            "description_permission": "on",
        }

        first = self.client.post(reverse("vacancy-add"), {**common, "url": "https://example.test/jobs/one"})
        second = self.client.post(reverse("vacancy-add"), {**common, "url": "https://example.test/jobs/two"})

        self.assertEqual((first.status_code, second.status_code), (302, 302))
        self.assertEqual(Vacancy.objects.count(), 2)
        self.assertEqual({record.raw_hash for record in self.owner.job_sources.get(slug="manual").records.all()}, {"no-content"})

    def test_manual_url_does_not_merge_into_adapter_record(self):
        url = "https://careers.example.test/jobs/product-manager-42"
        self.client.post(reverse("vacancy-add"), {"url": url})
        manual = Vacancy.objects.get()
        Source.objects.create(owner=self.owner, slug="import", name="Import", kind="fixture", adapter="fixture")

        result = upsert_record(
            {
                "source_slug": "import",
                "external_id": "job-42",
                "canonical_url": url,
                "apply_url": "https://careers.example.test/apply/product-manager-42",
                "title": "Product Manager",
                "company": "Example",
                "description": "Own the payments roadmap",
                "role": "Product Manager",
                "country_restrictions": ["worldwide"],
                "timezone_restrictions": [],
                "salary": {},
                "raw_hash": "real-content-hash",
                "adapter_confirmed_permalink": True,
            },
            owner=self.owner,
        )

        self.assertNotEqual(result.vacancy.id, manual.id)
        self.assertEqual(Vacancy.objects.count(), 2)
        manual.refresh_from_db()
        self.assertEqual((manual.title, manual.company), ("Название не указано", "Компания не указана"))
        self.assertEqual(manual.description, "")
        self.assertEqual(manual.source_records.count(), 1)
        self.assertFalse(manual.source_records.get().adapter_confirmed_permalink)

    def test_manual_text_without_permission_is_rejected_as_valid_form_error(self):
        response = self.client.post(
            reverse("vacancy-add"),
            {
                "url": "https://t.me/example/42",
                "text": "Текст объявления без подтверждённого права обработки",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Подтвердите право обработки", status_code=400)
        self.assertEqual(Vacancy.objects.count(), 0)

    def test_manual_add_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)

        response = csrf_client.post(
            reverse("vacancy-add"),
            {"url": "https://careers.example.test/no-csrf"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Vacancy.objects.count(), 0)

    def test_list_searches_filters_paginates_and_orders_by_priority_then_freshness(self):
        old = timezone.now() - timezone.timedelta(days=10)
        recent = timezone.now() - timezone.timedelta(days=1)
        remote_old = Vacancy.objects.create(
            owner=self.owner,
            title="Remote old",
            company="A",
            role="Product Manager",
            priority=Vacancy.Priority.REMOTE,
            user_status=Vacancy.UserStatus.SAVED,
            published_at=old,
        )
        remote_new = Vacancy.objects.create(
            owner=self.owner,
            title="Remote newest",
            company="B",
            role="Product Manager",
            priority=Vacancy.Priority.REMOTE,
            user_status=Vacancy.UserStatus.SAVED,
            published_at=recent,
        )
        relocation = Vacancy.objects.create(
            owner=self.owner,
            title="Relocation newest",
            company="C",
            role="Project Manager",
            priority=Vacancy.Priority.RELOCATION,
            published_at=timezone.now(),
        )
        for vacancy, external_id in ((remote_old, "old"), (remote_new, "new"), (relocation, "relocation")):
            vacancy.source_records.create(
                source=Source.objects.create(
                    owner=self.owner,
                    slug=f"source-{external_id}",
                    name=f"Source {external_id}",
                    kind="fixture",
                    adapter="fixture",
                ),
                external_id=external_id,
                canonical_url=f"https://example.test/{external_id}",
                raw_hash=external_id,
            )

        response = self.client.get(reverse("vacancies"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertLess(body.index("Remote newest"), body.index("Remote old"))
        self.assertLess(body.index("Remote old"), body.index("Relocation newest"))

        filtered = self.client.get(
            reverse("vacancies"),
            {"q": "newest", "role": "Product Manager", "source": "source-new", "priority": "remote", "status": "saved"},
        )
        self.assertContains(filtered, "Remote newest")
        self.assertNotContains(filtered, "Relocation newest")

        Vacancy.objects.bulk_create(
            [Vacancy(owner=self.owner, title=f"Extra {index}", company="Bulk") for index in range(21)]
        )
        page_two = self.client.get(reverse("vacancies"), {"page": 2})
        self.assertEqual(page_two.context["page_obj"].paginator.per_page, 20)
        self.assertTrue(page_two.context["page_obj"].has_previous())

    def test_role_filter_is_applied_independently(self):
        self.filter_fixture()
        response = self.client.get(reverse("vacancies"), {"role": "Product Manager"})
        self.assertContains(response, "Target vacancy")
        self.assertNotContains(response, "Other vacancy")

    def test_source_filter_is_applied_independently(self):
        self.filter_fixture()
        response = self.client.get(reverse("vacancies"), {"source": "target-source"})
        self.assertContains(response, "Target vacancy")
        self.assertNotContains(response, "Other vacancy")

    def test_priority_filter_is_applied_independently(self):
        self.filter_fixture()
        response = self.client.get(reverse("vacancies"), {"priority": "remote"})
        self.assertContains(response, "Target vacancy")
        self.assertNotContains(response, "Other vacancy")

    def test_status_filter_is_applied_independently(self):
        self.filter_fixture()
        response = self.client.get(reverse("vacancies"), {"status": "saved"})
        self.assertContains(response, "Target vacancy")
        self.assertNotContains(response, "Other vacancy")

    def test_http_boundaries_isolate_owner_vacancies(self):
        other_owner = get_user_model().objects.create_user("other")
        foreign = Vacancy.objects.create(owner=other_owner, title="Foreign vacancy", company="Private")

        listing = self.client.get(reverse("vacancies"), {"visibility": "all"})
        self.assertNotContains(listing, "Foreign vacancy")
        self.assertEqual(self.client.get(reverse("vacancy-detail", args=[foreign.id])).status_code, 404)
        mutation = self.client.post(
            reverse("vacancy-status", args=[foreign.id]),
            {"version": foreign.version, "status": "saved"},
        )
        self.assertEqual(mutation.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.user_status, Vacancy.UserStatus.NEW)

    def test_status_visibility_availability_and_note_use_optimistic_versions(self):
        vacancy = Vacancy.objects.create(owner=self.owner, title="PM", company="Example")

        closed = self.client.post(
            reverse("vacancy-status", args=[vacancy.id]),
            {"version": vacancy.version, "status": "closed", "note": "Позиция закрыта"},
        )
        self.assertRedirects(closed, reverse("vacancy-detail", args=[vacancy.id]))
        vacancy.refresh_from_db()
        self.assertEqual((vacancy.user_status, vacancy.closing_note), ("closed", "Позиция закрыта"))

        conflict = self.client.post(
            reverse("vacancy-visibility", args=[vacancy.id]),
            {"version": 1, "hidden": "1"},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertContains(conflict, "Данные изменились", status_code=409)
        vacancy.refresh_from_db()
        self.assertFalse(vacancy.hidden)

        hidden = self.client.post(
            reverse("vacancy-visibility", args=[vacancy.id]),
            {"version": vacancy.version, "hidden": "1"},
        )
        self.assertRedirects(hidden, reverse("vacancy-detail", args=[vacancy.id]))
        vacancy.refresh_from_db()
        self.assertTrue(vacancy.hidden)

        available = self.client.post(
            reverse("vacancy-availability", args=[vacancy.id]),
            {"version": vacancy.version, "availability": "active"},
        )
        self.assertRedirects(available, reverse("vacancy-detail", args=[vacancy.id]))
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.availability, "active")
        self.assertEqual(vacancy.user_status, "closed")

        noted = self.client.post(
            reverse("vacancy-note", args=[vacancy.id]),
            {"version": vacancy.version, "user_note": "Уточнить условия релокации"},
        )
        self.assertRedirects(noted, reverse("vacancy-detail", args=[vacancy.id]))
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.user_note, "Уточнить условия релокации")

    def test_optimistic_errors_redisplay_unsaved_status_closing_note_and_user_note(self):
        vacancy = Vacancy.objects.create(owner=self.owner, title="PM", company="Example")

        invalid = self.client.post(
            reverse("vacancy-status", args=[vacancy.id]),
            {"version": "invalid", "status": "closed", "note": "Черновик причины закрытия"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertContains(invalid, 'value="closed" selected', status_code=400)
        self.assertContains(invalid, "Черновик причины закрытия", status_code=400)

        vacancy.version = 2
        vacancy.save(update_fields=["version"])
        conflict = self.client.post(
            reverse("vacancy-note", args=[vacancy.id]),
            {"version": 1, "user_note": "Несохранённая заметка пользователя"},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertContains(conflict, "Несохранённая заметка пользователя", status_code=409)

    def test_card_shows_structured_terms_exact_links_and_honest_unknowns(self):
        vacancy = Vacancy.objects.create(
            owner=self.owner,
            title="Product Lead",
            company="Example",
            role="Product Manager",
            industry="Fintech",
            work_arrangement="remote",
            country_restrictions=["worldwide"],
            timezone_restrictions=["UTC+3"],
            salary_min="5000",
            salary_currency="USD",
            salary_period="month",
            salary_basis="gross",
            salary_component="total",
            salary_fixed={"min": "4000", "currency": "USD"},
            salary_bonus={"description": "Bonus 20%"},
            salary_equity_tokens={"description": "Tokens"},
            language="EN C1",
        )
        source = Source.objects.create(owner=self.owner, slug="exact", name="Exact source", kind="fixture", adapter="fixture")
        vacancy.source_records.create(
            source=source,
            external_id="exact-1",
            canonical_url="https://jobs.example.test/exact-1?ref=source",
            apply_url="https://apply.example.test/exact-1",
            raw_hash="exact",
            attribution={"label": "Required attribution"},
        )

        response = self.client.get(reverse("vacancy-detail", args=[vacancy.id]))

        for expected in ("worldwide", "UTC+3", "EN C1", "Bonus 20%", "Tokens", "Required attribution"):
            self.assertContains(response, expected)
        self.assertContains(response, "https://jobs.example.test/exact-1?ref=source")
        self.assertContains(response, "https://apply.example.test/exact-1")
        self.assertContains(response, "Дата неизвестна")
        self.assertNotContains(response, "Контакт из объявления")

    @override_settings(USER_TIME_ZONE="Europe/Moscow")
    def test_list_and_detail_render_dates_in_configured_user_timezone(self):
        instant = datetime(2026, 1, 1, 21, 30, tzinfo=UTC)
        vacancy = Vacancy.objects.create(
            owner=self.owner,
            title="Timezone boundary",
            company="Example",
            published_at=instant,
            last_checked_at=instant,
        )
        Vacancy.objects.filter(pk=vacancy.pk).update(first_seen_at=instant)

        listing = self.client.get(reverse("vacancies"))
        detail = self.client.get(reverse("vacancy-detail", args=[vacancy.id]))

        self.assertContains(listing, "02.01.2026")
        self.assertNotContains(listing, "01.01.2026")
        self.assertContains(detail, "02.01.2026 00:30", count=3)

    def test_card_shows_compensation_components_without_total_range(self):
        vacancy = Vacancy.objects.create(
            owner=self.owner,
            title="Product Manager",
            company="Example",
            salary_fixed={"description": "Fixed negotiable"},
            salary_bonus={"description": "Annual bonus"},
            salary_equity_tokens={"description": "Token allocation"},
        )

        response = self.client.get(reverse("vacancy-detail", args=[vacancy.id]))

        self.assertContains(response, "Fixed negotiable")
        self.assertContains(response, "Annual bonus")
        self.assertContains(response, "Token allocation")
