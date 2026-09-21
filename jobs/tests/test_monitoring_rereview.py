import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from jobs.matching.services import Assessment
from jobs.models.models import NotificationOutbox, Profile, Run, Source, SourceRecord, Vacancy
from jobs.monitoring.service import run_cycle
from jobs.notifications.service import TelegramBotTransport, deliver_digest


def report():
    return SimpleNamespace(succeeded=1, failed=0, skipped=0, records=0)


class SentTransport:
    configured = True

    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return SimpleNamespace(status="sent", code="", message="")


class MonitoringScopedRereviewTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="test-password")

    def test_command_uses_criteria_timezone_and_preference_schedule_shape(self):
        Profile.objects.create(
            owner=self.owner,
            criteria={"timezone": "Europe/Moscow"},
            preferences={"schedule": ["13:00"], "schedule_enabled": True},
        )
        observed = {}

        def fake_cycle(owner, *, schedule_slot):
            observed["schedule_slot"] = schedule_slot
            return SimpleNamespace(pk=1, status="complete")

        with patch("jobs.monitoring.management.commands.run_monitoring.timezone.now", return_value=datetime(2026, 9, 21, 11, 5, tzinfo=UTC)), patch("jobs.monitoring.management.commands.run_monitoring.run_cycle", side_effect=fake_cycle):
            call_command("run_monitoring", stdout=io.StringIO())
        self.assertIn("13:00@Europe/Moscow", observed["schedule_slot"])

    def test_terminal_delivery_is_not_downgraded_when_credentials_disappear(self):
        transport = TelegramBotTransport(token="", chat_id="")
        for status in (NotificationOutbox.Status.SENT, NotificationOutbox.Status.UNCERTAIN):
            with self.subTest(status=status):
                run = Run.objects.create(status="complete")
                outbox = NotificationOutbox.objects.create(run=run, payload={"text": "digest"}, status=status, attempts=1)
                result = deliver_digest(run, transport=transport)
                result.refresh_from_db()
                self.assertEqual(result.status, status)
                self.assertEqual(result.attempts, 1)
                self.assertEqual(result.last_error_code, "")

    @override_settings(JOB_SITE_URL="https://private.example")
    def test_bounding_omits_oversized_row_without_mutating_exact_urls(self):
        source = Source.objects.create(owner=self.owner, slug="fixture", name="Fixture", kind="site", adapter="fixture")
        exact_url = "https://jobs.example/exact?ref=" + ("x" * 900)
        vacancy = Vacancy.objects.create(owner=self.owner, title="Product Manager", company="Acme")
        SourceRecord.objects.create(source=source, vacancy=vacancy, external_id="1", canonical_url=exact_url, raw_hash="hash")
        transport = SentTransport()
        run = run_cycle(
            self.owner,
            collector=lambda *args, **kwargs: report(),
            evaluator=lambda vacancy, criteria: Assessment("fit", ("R" * 5000,), ()),
            transport=transport,
        )
        text = transport.messages[0]
        self.assertLessEqual(len(text), 3900)
        self.assertIn(exact_url, text)
        self.assertNotIn(exact_url[:500] + "…", text)
        self.assertIn(run.notifications.get().payload["list_url"], text)
