import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.utils import timezone

from jobs.models.models import Draft, Profile, ProfileFact, Research, Resume, SourceRecord, Vacancy
from jobs.sources.core.registry import seed_sources


User = get_user_model()
owner = User.objects.create_user("owner", password="browser-acceptance-password")
profile = Profile.objects.create(
    owner=owner,
    version=2,
    confirmed_version=2,
    about="Product leader",
    criteria={"roles": ["Product Manager"], "industries": ["fintech"]},
    preferences={"daily_budget_usd": "5.00", "openai_model": "fixture"},
)
Resume.objects.create(
    profile=profile,
    private_path="private/browser-fixture.docx",
    extraction_state=Resume.ExtractionState.COMPLETE,
    text=(
        "Browser fixture introduction\n\n"
        "[Блок 1] Product leadership\n"
        "[UNKNOWN] preserved fixture line\n"
        "[Страница 2] https://example.test/" + "long-unbroken-segment-" * 20
    ),
)
fact = ProfileFact.objects.create(
    profile=profile,
    text="Led a B2B fintech launch",
    kind="case",
    source="CV browser fixture",
    page=2,
    profile_version=2,
    confirmed=True,
)
seed_sources(owner)
source = owner.job_sources.get(slug="remote-ok")
source.llm_permission = True
source.save(update_fields=["llm_permission"])
description = "Own product discovery and a B2B payments roadmap."
vacancy = Vacancy.objects.create(
    owner=owner,
    title="Product Manager",
    company="Acme",
    company_domain="acme.example",
    description=description,
    role="Product Manager",
    industry="fintech",
    work_arrangement="remote",
    country_restrictions=["Portugal"],
)
SourceRecord.objects.create(
    source=source,
    vacancy=vacancy,
    external_id="browser-1",
    canonical_url="https://jobs.example/browser-1",
    raw_hash=hashlib.sha256(description.encode()).hexdigest(),
    description_permission=True,
)
research = Research.objects.create(
    vacancy=vacancy,
    company="Acme",
    domain="acme.example",
    role="Product Manager",
    status=Research.Status.PARTIAL,
    facts=[{
        "text": "Acme builds payment infrastructure",
        "url": "https://acme.example/about",
        "passage": "Acme builds payment infrastructure.",
        "checked_at": timezone.now().isoformat(),
    }],
    sources=[{"url": "https://acme.example/about", "title": "About Acme"}],
    coverage={"profile_version": 2, "cases": [{"id": fact.pk}]},
    expires_at=timezone.now() + timezone.timedelta(hours=1),
)
provenance = {
    "profile_facts": [{"id": fact.pk, "profile_version": 2, "source": fact.source, "page": 2}],
    "company_sources": research.sources,
    "company_facts": research.facts,
    "research_id": research.pk,
}
Draft.objects.create(
    vacancy=vacancy,
    profile=profile,
    research=research,
    kind="cover_letter",
    text="Initial cover letter",
    profile_version=2,
    status=Draft.Status.GENERATED,
    provenance=provenance,
)
Draft.objects.create(
    vacancy=vacancy,
    profile=profile,
    research=research,
    kind="recruiter_message",
    text="Initial recruiter message",
    profile_version=2,
    status=Draft.Status.GENERATED,
    provenance=provenance,
)

with open(os.environ["JOB_ACCEPTANCE_META"], "w", encoding="utf-8") as handle:
    json.dump({"vacancy_id": vacancy.pk}, handle)
