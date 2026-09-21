import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from jobs.matching.services import Assessment
from jobs.models.models import Lease, NotificationOutbox, Run, Source, SourceRecord, Vacancy
from jobs.monitoring.schedule import schedule_decision
from jobs.monitoring.service import CycleBusy, run_cycle
from jobs.notifications.service import DeliveryResult, TelegramBotTransport, reconcile_delivery, retry_delivery


class FakeTransport:
    def __init__(self, result=DeliveryResult("sent")):
        self.result = result
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return self.result


class MonitoringTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="secret-test-password")

    def report(self, **overrides):
        values = {"succeeded": 1, "failed": 0, "skipped": 0, "records": 1, **overrides}
        return SimpleNamespace(**values)

    def collector_with(self, count=1):
        def collect(owner, **kwargs):
            source, _ = Source.objects.get_or_create(owner=owner, slug="fixture", defaults={"name": "Fixture", "kind": "site", "adapter": "fixture"})
            for index in range(count):
                vacancy, created = Vacancy.objects.get_or_create(owner=owner, title=f"Product {index}", company="Acme")
                if created:
                    SourceRecord.objects.create(source=source, vacancy=vacancy, external_id=str(index), canonical_url=f"https://jobs.example/{index}", raw_hash="hash")
            return self.report(records=count)
        return collect

    @override_settings(JOB_SITE_URL="https://private.example")
    def test_cycle_sends_one_fit_clarify_digest_and_does_not_repeat(self):
        transport = FakeTransport()
        evaluator = lambda vacancy, criteria: Assessment("fit" if vacancy.title.endswith("0") else "clarify", ("Короткая причина",), ())
        started = timezone.now()
        first = run_cycle(self.owner, collector=self.collector_with(2), evaluator=evaluator, transport=transport, now=started)
        second = run_cycle(self.owner, collector=self.collector_with(2), evaluator=evaluator, transport=transport, now=started)
        self.assertEqual(first.notifications.get().status, NotificationOutbox.Status.SENT)
        self.assertEqual(len(transport.messages), 1)
        self.assertIn("https://jobs.example/0", transport.messages[0])
        self.assertIn("Короткая причина", transport.messages[0])
        self.assertEqual(second.summary["notification"], "empty")

    @override_settings(JOB_SITE_URL="https://private.example")
    def test_digest_caps_items_at_ten_but_tracks_every_vacancy(self):
        transport = FakeTransport()
        run = run_cycle(self.owner, collector=self.collector_with(11), evaluator=lambda vacancy, criteria: Assessment("fit", ("Да",), ()), transport=transport, now=timezone.now())
        payload = run.notifications.get().payload
        self.assertEqual(payload["total"], 11)
        self.assertEqual(len(payload["vacancy_ids"]), 11)
        self.assertEqual(transport.messages[0].count("https://jobs.example/"), 10)
        self.assertIn("Ещё 1: https://private.example/", transport.messages[0])

    @override_settings(JOB_SITE_URL="https://private.example")
    def test_unconfigured_bot_is_failed_without_attempt_or_reader_credentials(self):
        run = run_cycle(self.owner, collector=self.collector_with(), evaluator=lambda vacancy, criteria: Assessment("fit", ("Да",), ()), now=timezone.now())
        outbox = run.notifications.get()
        self.assertEqual(outbox.status, NotificationOutbox.Status.FAILED)
        self.assertEqual(outbox.attempts, 0)
        self.assertEqual(outbox.last_error_code, "bot_unconfigured")

    def test_missing_site_url_disables_delivery_but_not_collection(self):
        run = run_cycle(self.owner, collector=self.collector_with(), evaluator=lambda vacancy, criteria: Assessment("fit", ("Да",), ()), now=timezone.now())
        self.assertEqual(run.status, "complete")
        self.assertEqual(run.summary["notification"], "disabled")
        self.assertEqual(run.summary["notification_error"], "site_url_unconfigured")
        self.assertFalse(run.notifications.exists())

    def test_active_global_lease_rejects_concurrent_cycle(self):
        Lease.objects.create(name="monitoring:global", holder="other", expires_at=timezone.now() + timedelta(minutes=5))
        with self.assertRaises(CycleBusy):
            run_cycle(self.owner, collector=lambda *args, **kwargs: self.report())
        self.assertFalse(Run.objects.exists())

    def test_expired_lease_marks_stale_run_interrupted_and_recovers(self):
        now = timezone.now()
        stale = Run.objects.create(status="running", started_at=now - timedelta(hours=1), summary={"owner_id": self.owner.pk, "holder": "dead"})
        Lease.objects.create(name="monitoring:global", holder="dead", expires_at=now - timedelta(seconds=1))
        recovered = run_cycle(self.owner, collector=lambda *args, **kwargs: self.report(records=0), now=now)
        stale.refresh_from_db()
        self.assertEqual(stale.status, "interrupted")
        self.assertEqual(stale.summary["error"]["code"], "lease_expired")
        self.assertTrue(recovered.summary["recovered"])


class ScheduleTests(TestCase):
    def test_timezone_missing_disables_and_restart_runs_latest_slot_once(self):
        self.assertEqual(schedule_decision(now=datetime(2026, 9, 21, 10, tzinfo=UTC), timezone_name="").reason, "timezone_not_configured")
        decision = schedule_decision(now=datetime(2026, 9, 21, 11, tzinfo=UTC), timezone_name="Europe/Moscow")
        self.assertTrue(decision.due)
        self.assertIn("13:00", decision.slot_key)
        Run.objects.create(status="complete", summary={"schedule_slot": decision.slot_key})
        self.assertFalse(schedule_decision(now=datetime(2026, 9, 21, 11, tzinfo=UTC), timezone_name="Europe/Moscow").due)

    def test_dst_nonexistent_slot_is_not_run_and_ambiguous_slot_runs_once(self):
        spring = schedule_decision(now=datetime(2026, 3, 29, 2, 45, tzinfo=UTC), timezone_name="Europe/Berlin", slots="02:30")
        self.assertFalse(spring.due)
        autumn = schedule_decision(now=datetime(2026, 10, 25, 1, 45, tzinfo=UTC), timezone_name="Europe/Berlin", slots="02:30")
        self.assertTrue(autumn.due)
    @override_settings(MONITORING_ENABLED=False)
    def test_explicit_disabled_state_wins_over_due_slot(self):
        decision = schedule_decision(now=datetime(2026, 9, 21, 11, tzinfo=UTC), timezone_name="Europe/Moscow")
        self.assertFalse(decision.enabled)
        self.assertEqual(decision.reason, "monitoring_disabled")
        autumn = schedule_decision(now=datetime(2026, 10, 25, 1, 45, tzinfo=UTC), timezone_name="Europe/Berlin", slots="02:30")
        Run.objects.create(status="complete", summary={"schedule_slot": autumn.slot_key})
        self.assertFalse(schedule_decision(now=datetime(2026, 10, 25, 2, 0, tzinfo=UTC), timezone_name="Europe/Berlin", slots="02:30").due)


class DeliveryTests(TestCase):
    def test_timeout_is_uncertain_until_explicit_reconcile(self):
        class TimeoutOpener:
            def __call__(self, request, timeout):
                raise socket.timeout()

        result = TelegramBotTransport(token="configured", chat_id="owner", opener=TimeoutOpener()).send("digest")
        self.assertEqual(result.status, "uncertain")

        run = Run.objects.create(status="complete")
        outbox = NotificationOutbox.objects.create(run=run, payload={"text": "digest"}, status="uncertain", attempts=1)
        untouched = retry_delivery(outbox.pk, transport=FakeTransport())
        self.assertEqual(untouched.status, "uncertain")
        reconciled = reconcile_delivery(outbox.pk, delivered=False)
        self.assertEqual(reconciled.status, "failed")
        sent = retry_delivery(outbox.pk, transport=FakeTransport())
        self.assertEqual(sent.status, "sent")
