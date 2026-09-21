import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from jobs.models.models import Profile, ProfileFact, Resume
from jobs.profile.services import ProfileVersionConflict, ResumeValidationError, confirm_manual_profile, confirm_profile, extract_profile, load_profile_draft, set_manual_resume_text, upload_resume
from jobs.intelligence.gateway import GatewayUnavailable


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


def make_pdf(text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii"))
    return bytes(payload)


class ManualProfileConfirmationTests(TestCase):
    def test_confirmed_profile_can_be_created_without_resume(self):
        owner = get_user_model().objects.create_user("owner", password="secret")
        profile = Profile.objects.create(owner=owner)

        confirmed = confirm_manual_profile(
            profile,
            about="Руководил запуском B2B fintech продукта.",
            facts=[{"kind": "case", "text": "Сократил время запуска на 30%."}],
            expected_version=1,
        )

        self.assertEqual(confirmed.version, 2)
        self.assertEqual(confirmed.confirmed_version, 2)
        self.assertEqual(confirmed.resumes.count(), 0)
        self.assertTrue(confirmed.facts.get().confirmed)


class ResumeUploadTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("owner", password="secret")
        self.profile = Profile.objects.create(owner=owner)
        self.private_root = tempfile.TemporaryDirectory()
        self.addCleanup(self.private_root.cleanup)

    def test_docx_is_privately_saved_and_text_has_block_references(self):
        upload = SimpleUploadedFile(
            "Илья CV.docx",
            make_docx("Product manager", "Запустил платежный продукт"),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        with override_settings(PRIVATE_ROOT=Path(self.private_root.name)):
            resume = upload_resume(self.profile, upload)

        self.assertEqual(resume.extraction_state, "complete")
        self.assertIn("[Блок 1] Product manager", resume.text)
        self.assertIn("[Блок 2] Запустил платежный продукт", resume.text)
        self.assertNotIn("Илья CV", resume.private_path)
        self.assertTrue(Path(resume.private_path).is_file())

    def test_pdf_text_has_page_reference(self):
        upload = SimpleUploadedFile("cv.pdf", make_pdf("Product manager"), content_type="application/pdf")

        with override_settings(PRIVATE_ROOT=Path(self.private_root.name)):
            resume = upload_resume(self.profile, upload)

        self.assertEqual(resume.extraction_state, "complete")
        self.assertIn("[Страница 1] Product manager", resume.text)

    def test_empty_pdf_is_kept_for_manual_entry_without_fake_text(self):
        upload = SimpleUploadedFile("scan.pdf", make_pdf(""), content_type="application/pdf")

        with override_settings(PRIVATE_ROOT=Path(self.private_root.name)):
            resume = upload_resume(self.profile, upload)

        self.assertEqual(resume.extraction_state, "error")
        self.assertEqual(resume.text, "")
        self.assertTrue(Path(resume.private_path).is_file())

        original = Path(resume.private_path).read_bytes()
        updated = set_manual_resume_text(resume, "Product manager, 5 лет в fintech")
        self.assertEqual(updated.extraction_state, "complete")
        self.assertEqual(updated.text, "[Блок 1] Product manager, 5 лет в fintech")
        self.assertEqual(Path(updated.private_path).read_bytes(), original)

    def test_oversized_file_is_rejected_before_private_write(self):
        upload = SimpleUploadedFile("cv.pdf", b"%PDF-more-than-limit", content_type="application/pdf")

        with override_settings(PRIVATE_ROOT=Path(self.private_root.name)):
            with self.assertRaises(ResumeValidationError):
                upload_resume(self.profile, upload, max_bytes=8)

        self.assertEqual(self.profile.resumes.count(), 0)
        self.assertEqual(list(Path(self.private_root.name).rglob("*")), [])

    def test_encrypted_or_corrupt_docx_becomes_manual_fallback(self):
        upload = SimpleUploadedFile(
            "encrypted.docx", make_docx("secret"),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        with override_settings(PRIVATE_ROOT=Path(self.private_root.name)):
            with patch("jobs.profile.extractors.zipfile.ZipFile.read", side_effect=RuntimeError("encrypted secret")):
                resume = upload_resume(self.profile, upload)

        self.assertEqual(resume.extraction_state, "error")
        self.assertEqual(resume.text, "")
        self.assertTrue(Path(resume.private_path).is_file())

    def test_database_failure_cleans_up_private_orphan_file(self):
        upload = SimpleUploadedFile(
            "cv.docx", make_docx("Product manager"),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        with override_settings(PRIVATE_ROOT=Path(self.private_root.name)):
            with patch("jobs.profile.services.Resume.objects.create", side_effect=DatabaseError("db unavailable")):
                with self.assertRaises(DatabaseError):
                    upload_resume(self.profile, upload)

        self.assertEqual([path for path in Path(self.private_root.name).rglob("*") if path.is_file()], [])


class FakeProfileGateway:
    def structured(self, **kwargs):
        self.input_text = kwargs["input_text"]
        return {
            "about": "Product manager в fintech.",
            "facts": [{
                "kind": "case",
                "text": "Запустил платежный продукт",
                "source_reference": "Блок 2",
                "page": 0,
            }],
            "questions": ["Уточнить даты работы"],
        }


class InvalidProvenanceGateway(FakeProfileGateway):
    def structured(self, **kwargs):
        result = super().structured(**kwargs)
        result["facts"][0]["source_reference"] = "Страница 99"
        result["facts"][0]["page"] = 99
        return result


class MixedProvenanceGateway(FakeProfileGateway):
    def structured(self, **kwargs):
        result = super().structured(**kwargs)
        result["facts"].append({
            "kind": "case",
            "text": "Unsupported claim",
            "source_reference": "Page 99",
            "page": 99,
        })
        return result


class TwoFactsGateway(FakeProfileGateway):
    def structured(self, **kwargs):
        result = super().structured(**kwargs)
        result["facts"].append({
            "kind": "achievement",
            "text": "Увеличил конверсию",
            "source_reference": "Блок 2",
            "page": 0,
        })
        return result


class PendingGateway:
    def structured(self, **kwargs):
        raise GatewayUnavailable("budget_pending", "Суточный лимит исчерпан; вызов ожидает.")


class ProfileExtractionTests(TestCase):
    def test_extraction_creates_untrusted_draft_and_confirmation_is_explicit(self):
        owner = get_user_model().objects.create_user("owner", password="secret")
        profile = Profile.objects.create(owner=owner, version=2, confirmed_version=2, about="Старое подтверждённое")
        previous = ProfileFact.objects.create(
            profile=profile, text="Подтверждённый факт", kind="case", source="manual",
            profile_version=2, confirmed=True,
        )
        resume = Resume.objects.create(
            profile=profile, private_path="private/cv.docx",
            text="[Блок 2] Запустил платежный продукт", extraction_state="complete",
        )
        gateway = FakeProfileGateway()

        draft = extract_profile(resume, gateway=gateway, daily_limit="1.00")

        profile.refresh_from_db()
        previous.refresh_from_db()
        self.assertEqual(profile.about, "Старое подтверждённое")
        self.assertTrue(previous.confirmed)
        self.assertFalse(profile.facts.get(profile_version=3, kind="case").confirmed)
        self.assertEqual(gateway.input_text, resume.text)

        confirmed = confirm_profile(draft, expected_version=2)
        self.assertEqual(confirmed.confirmed_version, 3)
        self.assertEqual(confirmed.about, "Product manager в fintech.")
        self.assertTrue(profile.facts.get(profile_version=3, kind="case").confirmed)

    def test_replaced_draft_token_cannot_confirm_unseen_facts_and_confirm_clears_entire_draft(self):
        owner = get_user_model().objects.create_user("owner", password="secret")
        profile = Profile.objects.create(owner=owner)
        resume = Resume.objects.create(
            profile=profile, private_path="private/cv.docx",
            text="[Блок 2] Запустил платежный продукт", extraction_state="complete",
        )
        gateway = FakeProfileGateway()

        first = extract_profile(resume, gateway=gateway, daily_limit="1.00")
        second = extract_profile(resume, gateway=gateway, daily_limit="1.00")

        self.assertNotEqual(first.token, second.token)
        with self.assertRaises(ProfileVersionConflict):
            confirm_profile(first, expected_version=1)
        confirm_profile(second, expected_version=1)
        self.assertTrue(profile.facts.filter(kind="about", confirmed=True).exists())
        self.assertTrue(profile.facts.filter(kind="case", confirmed=True).exists())
        unresolved = profile.facts.filter(kind="question", confirmed=False)
        self.assertTrue(unresolved.exists())
        self.assertTrue(all(row.source.startswith(f"review:{second.token}|") for row in unresolved))
        with self.assertRaises(ValueError):
            load_profile_draft(profile, second.proposed_version, second.token)
        with self.assertRaises(ProfileVersionConflict):
            confirm_profile(second, expected_version=2)

    def test_edited_about_confirms_only_the_user_approved_text(self):
        owner = get_user_model().objects.create_user("owner", password="secret")
        profile = Profile.objects.create(owner=owner)
        resume = Resume.objects.create(
            profile=profile, private_path="private/cv.docx",
            text="[\u0411\u043b\u043e\u043a 2] Product launch", extraction_state="complete",
        )
        draft = extract_profile(resume, gateway=FakeProfileGateway(), daily_limit="1.00")

        confirm_profile(draft, expected_version=1, about="User approved text.")

        profile.refresh_from_db()
        self.assertEqual(profile.about, "User approved text.")
        self.assertSetEqual(
            set(profile.facts.filter(kind="about", confirmed=True).values_list("text", flat=True)),
            {"User approved text."},
        )
        original = profile.facts.get(kind="about", confirmed=False)
        self.assertEqual(original.text, draft.about)
        self.assertTrue(original.source.startswith(f"review:{draft.token}|"))
        edited = profile.facts.get(kind="about", confirmed=True)
        self.assertTrue(edited.source.startswith(f"user-edit:draft:{draft.token}"))
        with self.assertRaises(ValueError):
            load_profile_draft(profile, draft.proposed_version, draft.token)

    def test_cleared_fact_closes_draft_token_while_review_rows_survive(self):
        owner = get_user_model().objects.create_user("owner", password="secret")
        profile = Profile.objects.create(owner=owner)
        resume = Resume.objects.create(
            profile=profile, private_path="private/cv.docx",
            text="[Блок 2] Product launch", extraction_state="complete",
        )
        draft = extract_profile(resume, gateway=TwoFactsGateway(), daily_limit="1.00")
        cleared = next(fact for fact in draft.facts if fact.kind == "case")

        confirm_profile(draft, expected_version=1, fact_edits={cleared.pk: ""})

        profile.refresh_from_db()
        self.assertEqual(profile.about, draft.about)
        self.assertTrue(profile.facts.filter(kind="achievement", confirmed=True).exists())
        self.assertFalse(profile.facts.filter(source__startswith=f"draft:{draft.token}|").exists())
        self.assertTrue(profile.facts.filter(source__startswith=f"review:{draft.token}|").exists())
        with self.assertRaises(ValueError):
            load_profile_draft(profile, draft.proposed_version, draft.token)
        with self.assertRaises(ProfileVersionConflict):
            confirm_profile(draft, expected_version=2)

    def test_cleared_about_is_not_confirmed_and_review_questions_survive_next_extraction(self):
        owner = get_user_model().objects.create_user("owner", password="secret")
        profile = Profile.objects.create(owner=owner)
        resume = Resume.objects.create(
            profile=profile, private_path="private/cv.docx",
            text="[\u0411\u043b\u043e\u043a 2] Product launch", extraction_state="complete",
        )
        first = extract_profile(resume, gateway=MixedProvenanceGateway(), daily_limit="1.00")
        confirm_profile(first, expected_version=1, about="")

        profile.refresh_from_db()
        self.assertEqual(profile.about, "")
        self.assertFalse(profile.facts.filter(kind="about", confirmed=True).exists())
        first_review_texts = set(
            profile.facts.filter(confirmed=False, source__startswith=f"review:{first.token}|")
            .values_list("text", flat=True)
        )
        self.assertIn(first.about, first_review_texts)
        self.assertIn("\u0423\u0442\u043e\u0447\u043d\u0438\u0442\u044c \u0434\u0430\u0442\u044b \u0440\u0430\u0431\u043e\u0442\u044b", first_review_texts)
        self.assertTrue(any("Unsupported claim" in text and "99" in text for text in first_review_texts))

        extract_profile(resume, gateway=FakeProfileGateway(), daily_limit="1.00")

        self.assertSetEqual(
            set(profile.facts.filter(confirmed=False, source__startswith=f"review:{first.token}|")
                .values_list("text", flat=True)),
            first_review_texts,
        )

    def test_fact_with_nonexistent_cv_marker_is_rejected_for_review(self):
        owner = get_user_model().objects.create_user("owner", password="secret")
        profile = Profile.objects.create(owner=owner)
        resume = Resume.objects.create(
            profile=profile, private_path="private/cv.docx",
            text="[Блок 2] Запустил платежный продукт", extraction_state="complete",
        )

        draft = extract_profile(resume, gateway=InvalidProvenanceGateway(), daily_limit="1.00")

        self.assertEqual(draft.facts, ())
        self.assertTrue(any("не подтверждена ссылка" in question for question in draft.questions))
        self.assertFalse(profile.facts.filter(kind="case", profile_version=2).exists())


class ProfileHttpTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="secret")
        self.client.force_login(self.owner)

    def test_owner_can_confirm_complete_manual_profile_without_cv(self):
        response = self.client.get(reverse("profile"))
        profile = Profile.objects.get(owner=self.owner)
        self.assertContains(response, "Проверенный профиль вручную")

        response = self.client.post(reverse("profile-manual"), {
            "version": profile.version,
            "about": "Руководитель продукта",
            "experience": "5 лет в fintech",
            "achievements": "Увеличил конверсию на 20%",
            "cases": "Запустил B2B onboarding",
            "languages": "English C1",
        })

        self.assertRedirects(response, reverse("profile"))
        profile.refresh_from_db()
        self.assertEqual(profile.confirmed_version, 2)
        self.assertSetEqual(
            set(profile.facts.filter(profile_version=2).values_list("kind", flat=True)),
            {"experience", "achievement", "case", "language"},
        )

    def test_settings_are_versioned_and_stale_input_is_preserved(self):
        self.client.get(reverse("profile"))
        profile = Profile.objects.get(owner=self.owner)
        payload = {
            "version": profile.version,
            "roles": "Product Manager\nРуководитель поддержки",
            "industries": "fintech, crypto",
            "salary_target": "5000.00", "salary_currency": "usd", "salary_period": "month", "salary_basis": "unknown",
            "work_modes": ["remote", "relocation"], "residence_country": "",
            "hiring_countries": "DE, NL", "timezone": "", "languages": "RU, EN",
            "response_language": "ru", "tone": "professional", "length": "short", "emphasis": "B2B",
            "schedule": "09:00, 13:00", "daily_budget_usd": "1.2500",
            "openai_model": "gpt-custom-model", "search_provider": "brave",
        }

        response = self.client.post(reverse("profile-settings"), payload)
        self.assertRedirects(response, reverse("profile"))
        profile.refresh_from_db()
        self.assertEqual(profile.version, 2)
        self.assertFalse(profile.preferences["schedule_enabled"])
        self.assertEqual(profile.criteria["salary"]["basis"], "unknown")
        self.assertEqual(profile.preferences["openai_model"], "gpt-custom-model")
        self.assertEqual(profile.preferences["search_provider"], "brave")

        payload["emphasis"] = "Сохранить этот ввод"
        response = self.client.post(reverse("profile-settings"), payload)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Сохранить этот ввод", status_code=409)
        self.assertContains(response, "Профиль уже изменён", status_code=409)

    def test_profile_page_previews_resume_text_and_prefills_all_confirmed_fact_kinds(self):
        profile = Profile.objects.create(owner=self.owner, version=2, confirmed_version=2, about="Обо мне")
        for kind, text in [
            ("experience", "Опыт SkyPay"), ("achievement", "Рост конверсии"),
            ("case", "Запуск B2B"), ("language", "English C1"),
        ]:
            ProfileFact.objects.create(
                profile=profile, kind=kind, text=text, source="manual", profile_version=2, confirmed=True,
            )
        Resume.objects.create(
            profile=profile, private_path="private/cv.docx", text="[Блок 1] Проверяемый текст CV", extraction_state="complete",
        )

        response = self.client.get(reverse("profile"))

        for visible in ["[Блок 1] Проверяемый текст CV", "Опыт SkyPay", "Рост конверсии", "Запуск B2B", "English C1"]:
            self.assertContains(response, visible)

    def test_resume_preview_is_readable_and_keeps_an_exact_secondary_raw_view(self):
        profile = Profile.objects.create(owner=self.owner)
        original = (
            "Вводная строка без маркера\n\n"
            "[Блок 1] Product lead\nПродолжение блока\n"
            "[Неизвестно] не потерять\n"
            "[Страница 2] https://example.test/" + "very-long-segment-" * 12
        )
        resume = Resume.objects.create(
            profile=profile,
            private_path="private/cv.docx",
            text=original,
            extraction_state="complete",
        )

        response = self.client.get(reverse("profile"))

        self.assertContains(response, 'class="resume-preview"')
        self.assertContains(response, '<span class="resume-preview-label">Блок 1</span>', html=True)
        self.assertContains(response, '<span class="resume-preview-label">Страница 2</span>', html=True)
        self.assertContains(response, "Вводная строка без маркера")
        self.assertContains(response, "[Неизвестно] не потерять")
        self.assertContains(response, "Исходный извлечённый текст")
        self.assertContains(response, original)
        resume.refresh_from_db()
        self.assertEqual(resume.text, original)

    def test_invalid_timezone_is_rejected_without_saving_settings(self):
        self.client.get(reverse("profile"))
        profile = Profile.objects.get(owner=self.owner)
        response = self.client.post(reverse("profile-settings"), {
            "version": profile.version, "roles": "Product Manager", "industries": "fintech",
            "salary_target": "5000", "salary_currency": "USD", "salary_period": "month", "salary_basis": "unknown",
            "work_modes": ["remote"], "residence_country": "", "hiring_countries": "",
            "timezone": "Mars/Olympus", "languages": "", "response_language": "ru",
            "tone": "professional", "length": "short", "emphasis": "", "schedule": "09:00",
            "daily_budget_usd": "1.0000",
            "openai_model": "gpt-5.6-luna", "search_provider": "auto",
        })

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Неизвестный часовой пояс", status_code=400)
        profile.refresh_from_db()
        self.assertEqual(profile.version, 1)

    def test_ai_settings_defaults_are_visible_and_invalid_values_are_not_saved(self):
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Суточный бюджет, USD")
        self.assertContains(response, "gpt-5.6-luna")
        self.assertContains(response, "Автоматически")
        profile = Profile.objects.get(owner=self.owner)
        payload = {
            "version": profile.version, "roles": "Product Manager", "industries": "fintech",
            "salary_target": "5000", "salary_currency": "USD", "salary_period": "month", "salary_basis": "unknown",
            "work_modes": ["remote"], "residence_country": "", "hiring_countries": "",
            "timezone": "", "languages": "", "response_language": "ru", "tone": "professional",
            "length": "short", "emphasis": "", "schedule": "09:00", "daily_budget_usd": "1.0000",
            "openai_model": "bad model with spaces", "search_provider": "unknown",
        }
        response = self.client.post(reverse("profile-settings"), payload)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "корректное имя модели", status_code=400)
        profile.refresh_from_db()
        self.assertEqual(profile.version, 1)

    def test_upload_extract_confirm_enforce_owner_and_csrf_and_magic_mismatch_is_manual_fallback(self):
        profile = Profile.objects.create(owner=self.owner)
        resume = Resume.objects.create(
            profile=profile, private_path="private/cv.docx", text="[Блок 1] Product manager", extraction_state="complete",
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        csrf_client.get(reverse("profile"))
        for url in [reverse("profile-upload"), reverse("profile-extract", args=[resume.pk]), reverse("profile-confirm")]:
            self.assertEqual(csrf_client.post(url).status_code, 403)

        intruder = get_user_model().objects.create_user("intruder", password="secret")
        self.client.force_login(intruder)
        for url in [reverse("profile-upload"), reverse("profile-extract", args=[resume.pk]), reverse("profile-confirm")]:
            self.assertEqual(self.client.post(url).status_code, 403)

        self.client.force_login(self.owner)
        response = self.client.post(reverse("profile-upload"), {
            "resume": SimpleUploadedFile("spoofed.pdf", b"not-a-pdf", content_type="application/pdf"),
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Содержимое файла не соответствует", status_code=400)
        self.assertContains(response, "Проверенный профиль вручную", status_code=400)

    def test_budget_exhaustion_is_rendered_as_pending_warning_not_server_error(self):
        profile = Profile.objects.create(owner=self.owner, preferences={"daily_budget_usd": "1.0000"})
        resume = Resume.objects.create(
            profile=profile, private_path="private/cv.docx", text="[Блок 1] Product manager", extraction_state="complete",
        )

        with patch("jobs.profile.views._gateway", return_value=PendingGateway()) as gateway_factory:
            response = self.client.post(reverse("profile-extract", args=[resume.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Суточный лимит исчерпан")
        self.assertContains(response, "tone-warning")
        self.assertFalse(profile.facts.exists())
        gateway_factory.assert_called_once_with(model="gpt-5.6-luna")

    def test_unresolved_and_rejected_questions_remain_visible_after_confirm(self):
        profile = Profile.objects.create(owner=self.owner)
        resume = Resume.objects.create(
            profile=profile, private_path="private/cv.docx",
            text="[Блок 2] Запустил платежный продукт", extraction_state="complete",
        )
        draft = extract_profile(resume, gateway=InvalidProvenanceGateway(), daily_limit="1.00")
        confirm_profile(draft, expected_version=1)

        response = self.client.get(reverse("profile"))

        self.assertContains(response, "Требуют решения")
        self.assertContains(response, "Уточнить даты работы")
        self.assertContains(response, "не подтверждена ссылка")
        self.assertNotContains(response, "Неподтверждённый черновик")
