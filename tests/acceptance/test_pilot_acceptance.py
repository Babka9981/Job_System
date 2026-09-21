import hashlib
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.drafts.services import COVER_LETTER, RECRUITER_MESSAGE, generate, save_edit
from jobs.intelligence.research import research
from jobs.intelligence.search import FixtureSearchProvider, SearchHit
from jobs.matching.services import Assessment, evaluate
from jobs.models.models import Draft, Profile, ProfileFact, Research, Source, Vacancy
from jobs.monitoring.service import run_cycle
from jobs.notifications.service import DeliveryResult
from jobs.profile.services import confirm_profile, extract_profile, upload_resume
from jobs.sources.core.collector import collect_sources
from jobs.sources.core.contracts import Batch, Coverage
from jobs.sources.core.registry import seed_sources
from jobs.vacancies.services import change_status


class FixtureAdapter:
    def __init__(self, record):
        self.record = record

    def collect(self, source, cursor):
        return Batch((self.record,), coverage=Coverage(window_start=timezone.now()))


class MatchGateway:
    def structured(self, **kwargs):
        return {
            "role_match": "yes", "role_direction": "product", "industry_match": "yes",
            "reasons": ["Обязанности соответствуют Product Manager."], "questions": [],
        }


class ResearchGateway:
    def structured(self, **kwargs):
        pages = json.loads(kwargs["input_text"])["pages"]
        return {
            "facts": [{
                "page_index": index, "claim": page["content"].split(".")[0],
                "passage": page["content"], "event_date": "",
            } for index, page in enumerate(pages)],
            "conflicts": [], "hypotheses": [],
        }


class DraftGateway:
    def __init__(self, fact_id):
        self.fact_id = fact_id

    def structured(self, **kwargs):
        return {
            "profile_fact_ids": [self.fact_id],
            "company_fact_indices": [0] if kwargs["operation"] == "application_cover_letter" else [],
            "include_contact": False,
        }


class ProfileExtractionGateway:
    def structured(self, **kwargs):
        return {
            "about": "Product leader",
            "facts": [{
                "kind": "case",
                "text": "Led a B2B fintech launch",
                "source_reference": "Блок 2",
                "page": 0,
            }],
            "questions": [],
        }


def make_docx(*paragraphs):
    document = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{document}</w:body></w:document>",
        )
    return payload.getvalue()


class SentTransport:
    configured = True

    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return DeliveryResult("sent")


@override_settings(ROOT_URLCONF="config.urls", OWNER_USERNAME="owner", JOB_SITE_URL="https://pilot.example")
class PilotJourneyAcceptanceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="secret")

    def test_exact_catalog_and_repeat_collection_are_honest_and_idempotent(self):
        seed_sources(self.owner)
        self.assertEqual(Source.objects.filter(owner=self.owner).count(), 50)
        self.assertEqual(Source.objects.filter(owner=self.owner, kind="site").count(), 9)
        self.assertEqual(Source.objects.filter(owner=self.owner, kind="telegram").count(), 41)
        self.assertFalse(Source.objects.filter(owner=self.owner, status=Source.Status.READY).exists())
        self.assertTrue(all((source.config or {}).get("reason") for source in Source.objects.filter(owner=self.owner)))

        Source.objects.filter(owner=self.owner).exclude(slug="remote-ok").update(enabled=False)
        source = Source.objects.get(owner=self.owner, slug="remote-ok")
        description = "Own product discovery and B2B payments roadmap."
        record = {
            "source_slug": "remote-ok", "external_id": "acceptance-1",
            "canonical_url": "https://jobs.example/acceptance-1",
            "apply_url": "https://jobs.example/acceptance-1/apply",
            "title": "Product Manager", "company": "Acme", "company_domain": "acme.example",
            "description": description, "description_permission": True,
            "role": "Product Manager", "industry": "fintech", "work_arrangement": "remote",
            "country_restrictions": ["Portugal"],
            "raw_hash": hashlib.sha256(description.encode()).hexdigest(),
            "attribution": {"name": "Fixture"},
        }
        adapter = FixtureAdapter(record)
        first = collect_sources(self.owner, adapters={"remote_ok": adapter}, holder="acceptance-1")
        second = collect_sources(self.owner, adapters={"remote_ok": adapter}, holder="acceptance-2")
        source.refresh_from_db()
        self.assertEqual((first.records, second.records), (1, 1))
        self.assertEqual(Vacancy.objects.filter(owner=self.owner).count(), 1)
        self.assertEqual(source.status, Source.Status.READY)

    def test_mocked_owner_journey_from_confirmed_profile_to_digest(self):
        profile = Profile.objects.create(
            owner=self.owner,
            criteria={
                "roles": ["Product Manager"], "industries": ["fintech"],
                "residence_country": "Portugal", "hiring_countries": ["Portugal"],
                "work_authorized_countries": ["Portugal"], "timezone": "Europe/Moscow",
                "salary": {"target": "5000", "currency": "USD", "period": "month", "basis": "gross"},
            },
            preferences={"daily_budget_usd": "5.00", "openai_model": "fixture"},
        )
        upload = SimpleUploadedFile(
            "cv.docx",
            make_docx("Product Manager", "Led a B2B fintech launch"),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        with tempfile.TemporaryDirectory() as private_dir, override_settings(PRIVATE_ROOT=Path(private_dir)):
            resume = upload_resume(profile, upload)
            self.assertEqual(resume.extraction_state, "complete")
            self.assertIn("[Блок 2] Led a B2B fintech launch", resume.text)
            extracted = extract_profile(resume, gateway=ProfileExtractionGateway(), daily_limit="5.00")
            profile = confirm_profile(extracted, expected_version=profile.version)
        self.assertEqual(profile.confirmed_version, profile.version)
        fact = ProfileFact.objects.get(profile=profile, kind="case")
        self.assertTrue(fact.confirmed)
        self.assertIn("resume:", fact.source)
        seed_sources(self.owner)
        Source.objects.filter(owner=self.owner).exclude(slug="remote-ok").update(enabled=False)
        source = Source.objects.get(owner=self.owner, slug="remote-ok")
        source.llm_permission = True
        source.save(update_fields=["llm_permission"])
        description = "Own product discovery and B2B payments roadmap."
        record = {
            "source_slug": source.slug, "external_id": "journey-1",
            "canonical_url": "https://jobs.example/journey-1",
            "title": "Product Manager", "company": "Acme", "company_domain": "acme.example",
            "description": description, "description_permission": True,
            "role": "Product Manager", "industry": "fintech", "work_arrangement": "remote",
            "country_restrictions": ["Portugal"], "timezone_restrictions": ["Europe/Moscow"],
            "salary": {
                "min": "6000", "max": "7000", "currency": "USD", "period": "month",
                "basis": "gross", "component": "fixed",
            },
            "raw_hash": hashlib.sha256(description.encode()).hexdigest(),
        }
        collect_sources(self.owner, adapters={"remote_ok": FixtureAdapter(record)}, holder="journey")
        vacancy = Vacancy.objects.get(owner=self.owner)
        assessment = evaluate(vacancy, profile.criteria, gateway=MatchGateway())
        self.assertEqual(assessment.status, "fit")

        hits = [
            SearchHit("https://acme.example/about", "About", "candidate only"),
            SearchHit("https://news.example/acme", "News", "candidate only"),
        ]
        fetcher = mock.Mock()
        fetcher.fetch.side_effect = [
            {"url": hits[0].url, "content": "Acme builds B2B payment infrastructure.", "checked_at": timezone.now()},
            {"url": hits[1].url, "content": "Acme launched analytics. Details https://acme.example/news.", "checked_at": timezone.now()},
        ]
        research_result = research(
            vacancy, domain="acme.example", role="Product Manager", profile=profile,
            provider=FixtureSearchProvider([hits, hits, hits]), fetcher=fetcher,
            gateway=ResearchGateway(),
        )
        self.assertIn(research_result.status, {Research.Status.COMPLETE, Research.Status.PARTIAL})
        self.assertTrue(research_result.sources)

        draft_gateway = DraftGateway(fact.pk)
        cover = generate(vacancy, profile, COVER_LETTER, gateway=draft_gateway)
        recruiter = generate(vacancy, profile, RECRUITER_MESSAGE, gateway=draft_gateway)
        edited = save_edit(
            vacancy, profile, COVER_LETTER, cover.text + "\nEdited by owner",
            expected_version=cover.version,
        )
        self.assertIn("Edited by owner", edited.text)
        self.assertEqual(recruiter.research_id, research_result.pk)

        self.client.force_login(self.owner)
        page = self.client.get(reverse("vacancy-drafts", args=[vacancy.pk]))
        self.assertContains(page, "data-copy-target")
        vacancy = change_status(vacancy.pk, vacancy.version, Vacancy.UserStatus.APPLIED, owner=self.owner)
        self.assertEqual(vacancy.user_status, Vacancy.UserStatus.APPLIED)

        transport = SentTransport()
        run = run_cycle(
            self.owner,
            collector=lambda *args, **kwargs: SimpleNamespace(succeeded=1, failed=0, skipped=0, records=0),
            evaluator=lambda item, criteria: Assessment("fit", ("Подходит по обязанностям.",), ()),
            transport=transport,
        )
        self.assertEqual(run.summary["notification"], "sent")
        self.assertEqual(len(transport.messages), 1)
        self.assertIn("Product Manager", transport.messages[0])
        self.assertEqual(Draft.objects.filter(vacancy=vacancy).count(), 2)


class DesignSourceAcceptanceTests(SimpleTestCase):
    root = Path(__file__).resolve().parents[2]

    def read(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")

    def test_shared_ui_has_theme_drawer_focus_and_overflow_contracts(self):
        css = self.read("jobs/static/ui/app.css")
        app = self.read("jobs/static/ui/app.js")
        base = self.read("jobs/templates/shared/base.html")
        shell = self.read("jobs/templates/shared/app_shell.html")
        tokens = self.read("jobs/static/ui/tokens.css")
        for evidence in (
            "--sidebar-width:16rem", "--topbar-height:4.25rem", "@media(max-width:62rem)",
            "@media(max-width:42rem)", "overflow-x:hidden", "focus-visible", ".table-container",
        ):
            self.assertIn(evidence, tokens + css)
        for evidence in ("localStorage", 'addEventListener("storage"', 'event.key === "Escape"'):
            self.assertIn(evidence, app)
        self.assertIn("skip-link", base)
        self.assertIn('aria-expanded="false"', shell)
        self.assertIn('aria-controls="primary-navigation"', shell)
        self.assertIn('role="status"', shell)

    def test_core_text_token_contrast_is_at_least_4_5_to_1(self):
        tokens = self.read("jobs/static/ui/tokens.css")
        blocks = [
            tokens.split(":root[data-theme=dark]", 1)[0],
            tokens.split(":root[data-theme=dark]", 1)[1].split("@media", 1)[0],
        ]

        def color(block, name):
            value = re.search(rf"--{name}:(#[0-9a-fA-F]{{3,6}})", block).group(1)
            if len(value) == 4:
                value = "#" + "".join(character * 2 for character in value[1:])
            return tuple(int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))

        def luminance(rgb):
            values = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb]
            return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]

        for block in blocks:
            background = luminance(color(block, "surface-raised"))
            for name in ("text-primary", "text-secondary", "text-muted"):
                foreground = luminance(color(block, name))
                ratio = (max(background, foreground) + 0.05) / (min(background, foreground) + 0.05)
                self.assertGreaterEqual(ratio, 4.5, name)
