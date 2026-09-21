import io
import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib import error

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from jobs.matching.services import Assessment
from jobs.models.models import Lease, NotificationOutbox, Profile, Run, Source, SourceRecord, Vacancy
from jobs.monitoring.schedule import schedule_decision
from jobs.monitoring.service import CycleBusy, run_cycle
from jobs.notifications.service import DeliveryResult, TelegramBotTransport, deliver_digest, retry_delivery


def report(records=0):
    return SimpleNamespace(succeeded=1, failed=0, skipped=0, records=records)


class SentTransport:
    configured = True

    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return DeliveryResult("sent")


class MonitoringReviewTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="test-password")

    def vacancy(self, *, title="Product Manager"):
        vacancy = Vacancy.objects.create(owner=self.owner, title=title, company="Acme")
        source, _ = Source.objects.get_or_create(owner=self.owner, slug="fixture", defaults={"name": "Fixture", "kind": "site", "adapter": "fixture"})
        SourceRecord.objects.create(source=source, vacancy=vacancy, external_id=str(vacancy.pk), canonical_url=f"https://jobs.example/{vacancy.pk}", raw_hash="hash")
        return vacancy

    def test_command_uses_real_profile_settings_shape(self):
        Profile.objects.create(owner=self.owner, criteria={"timezone": "Europe/Moscow"}, preferences={"schedule": ["13:00"], "schedule_enabled": True})
        observed = {}

        def fake_cycle(owner, *, schedule_slot):
            observed.update(owner=owner, schedule_slot=schedule_slot)
            return SimpleNamespace(pk=7, status="complete")

        with patch("jobs.monitoring.management.commands.run_monitoring.timezone.now", return_value=datetime(2026, 9, 21, 11, 5, tzinfo=UTC)), patch("jobs.monitoring.management.commands.run_monitoring.run_cycle", side_effect=fake_cycle):
            call_command("run_monitoring", stdout=io.StringIO())
        self.assertEqual(observed["owner"], self.owner)
        self.assertIn("13:00@Europe/Moscow", observed["schedule_slot"])

    def test_running_crashed_slot_is_due_and_expired_lease_recovers(self):
        now = datetime(2026, 9, 21, 11, 5, tzinfo=UTC)
        slot = schedule_decision(now=now, timezone_name="Europe/Moscow", slots=["13:00"]).slot_key
        stale = Run.objects.create(status="running", summary={"owner_id": self.owner.pk, "holder": "dead", "schedule_slot": slot})
        Lease.objects.create(name="monitoring:global", holder="dead", expires_at=timezone.now() - timedelta(seconds=1))
        self.assertTrue(schedule_decision(now=now, timezone_name="Europe/Moscow", slots=["13:00"]).due)
        recovered = run_cycle(self.owner, collector=lambda *args, **kwargs: report(), schedule_slot=slot)
        stale.refresh_from_db()
        self.assertEqual(stale.status, "interrupted")
        self.assertTrue(recovered.summary["recovered"])

    def test_takeover_during_matching_fences_old_holder_before_outbox(self):
        self.vacancy()

        def steal(vacancy, criteria):
            Lease.objects.filter(name="monitoring:global").update(holder="new", expires_at=timezone.now() + timedelta(minutes=5))
            return Assessment("fit", ("Да",), ())

        with self.assertRaises(CycleBusy):
            run_cycle(self.owner, collector=lambda *args, **kwargs: report(), evaluator=steal)
        self.assertFalse(NotificationOutbox.objects.exists())
        self.assertEqual(Run.objects.get().status, "error")

    @override_settings(JOB_SITE_URL="https://private.example")
    def test_old_pending_vacancy_is_reconsidered_until_durable_assessment(self):
        vacancy = self.vacancy()
        Vacancy.objects.filter(pk=vacancy.pk).update(first_seen_at=timezone.now() - timedelta(days=30))
        pending = run_cycle(self.owner, collector=lambda *args, **kwargs: report(), evaluator=lambda vacancy, criteria: Assessment("pending", ("Позже",), ()))
        self.assertEqual(pending.summary["notification"], "empty")
        transport = SentTransport()
        completed = run_cycle(self.owner, collector=lambda *args, **kwargs: report(), evaluator=lambda vacancy, criteria: Assessment("fit", ("Да",), ()), transport=transport)
        self.assertEqual(completed.summary["digest_count"], 1)
        self.assertEqual(len(transport.messages), 1)

    def test_atomic_claim_blocks_concurrent_retry_and_crash_becomes_uncertain(self):
        run = Run.objects.create(status="complete")
        outbox = NotificationOutbox.objects.create(run=run, payload={"text": "digest", "vacancy_ids": []})
        nested = SentTransport()

        class ReentrantTransport(SentTransport):
            def send(inner_self, text):
                retry_delivery(outbox.pk, transport=nested)
                return super().send(text)

        delivered = deliver_digest(run, transport=ReentrantTransport())
        self.assertEqual(delivered.status, "sent")
        self.assertEqual(nested.messages, [])

        crash_run = Run.objects.create(status="complete")
        crash_box = NotificationOutbox.objects.create(run=crash_run, payload={"text": "digest", "vacancy_ids": []})

        class CrashAfterSend(SentTransport):
            def send(inner_self, text):
                inner_self.messages.append(text)
                raise RuntimeError("process died after send")

        started = timezone.now()
        with self.assertRaises(RuntimeError):
            deliver_digest(crash_run, transport=CrashAfterSend(), now=started)
        uncertain = retry_delivery(crash_box.pk, transport=SentTransport(), now=started + timedelta(minutes=6))
        self.assertEqual(uncertain.status, "uncertain")
        self.assertEqual(uncertain.last_error_code, "claim_expired")

    def test_timeout_wrapped_by_urlerror_and_malformed_response_are_uncertain(self):
        def timeout_open(request, timeout):
            raise error.URLError(socket.timeout())

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"not-json"

        timeout_result = TelegramBotTransport(token="token", chat_id="owner", opener=timeout_open).send("digest")
        malformed_result = TelegramBotTransport(token="token", chat_id="owner", opener=lambda request, timeout: Response()).send("digest")
        self.assertEqual(timeout_result.status, "uncertain")
        self.assertEqual(malformed_result.status, "uncertain")

    @override_settings(JOB_SITE_URL="https://private.example")
    def test_digest_is_bounded_and_always_links_full_list(self):
        self.vacancy(title="P" * 1000)
        transport = SentTransport()
        run_cycle(self.owner, collector=lambda *args, **kwargs: report(), evaluator=lambda vacancy, criteria: Assessment("fit", ("R" * 5000,), ()), transport=transport)
        self.assertLessEqual(len(transport.messages[0]), 3900)
        self.assertIn("Полный список: https://private.example/", transport.messages[0])
