from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from jobs.models.models import Profile, Source, SourceRecord, TemporarySourceContent, Vacancy


class SharedSchemaTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner")

    def test_profile_versions_and_structured_settings(self):
        profile = Profile.objects.create(owner=self.owner, criteria={"roles": ["Product Manager"]}, preferences={"language": "ru"})
        self.assertEqual((profile.version, profile.confirmed_version), (1, 0))
        self.assertEqual(profile.criteria["roles"], ["Product Manager"])

    def test_vacancy_keeps_money_components_and_long_lived_states(self):
        vacancy = Vacancy.objects.create(owner=self.owner, title="PM", company="Example", salary_min=Decimal("5000.00"), salary_currency="USD", salary_period="month", salary_fixed={"min": "5000.00"}, salary_bonus={"description": "unknown"})
        self.assertEqual(vacancy.user_status, "new")
        self.assertEqual(vacancy.availability, "unknown")
        self.assertEqual(vacancy.salary_min, Decimal("5000.00"))
        self.assertEqual(vacancy.salary_fixed["min"], "5000.00")

    def test_source_record_identity_is_unique_per_source(self):
        source = Source.objects.create(owner=self.owner, slug="feed", name="Feed", kind="api", adapter="fixture")
        vacancy = Vacancy.objects.create(owner=self.owner, title="PM", company="Example")
        SourceRecord.objects.create(source=source, vacancy=vacancy, external_id="42", canonical_url="https://example.test/42", raw_hash="abc")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceRecord.objects.create(source=source, vacancy=vacancy, external_id="42", canonical_url="https://example.test/other", raw_hash="def")

    def test_temporary_content_uses_segregated_storage(self):
        with TemporaryDirectory() as private, TemporaryDirectory() as temporary, override_settings(PRIVATE_ROOT=Path(private), TEMPORARY_ROOT=Path(temporary)):
            field = TemporarySourceContent._meta.get_field("content")
            self.assertEqual(Path(field.storage.location), Path(temporary))
            self.assertNotEqual(Path(field.storage.location), Path(private))
