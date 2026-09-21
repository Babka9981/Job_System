import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction
from django.urls import reverse
from django.utils import timezone

from jobs.matching.services import evaluate
from jobs.models.models import Lease, NotificationOutbox, Profile, Run, Vacancy
from jobs.notifications.service import deliver_digest
from jobs.sources.core.collector import CollectorBusy, collect_sources


class CycleBusy(Exception):
    pass


class CycleExecutionError(Exception):
    """Safe public failure for an unsuccessful monitoring cycle."""


def _lease_seconds():
    try:
        return max(60, int(getattr(settings, "MONITORING_LEASE_SECONDS", 1800)))
    except (TypeError, ValueError):
        return 1800


def _acquire(holder, now):
    recovered = False
    try:
        with transaction.atomic():
            lease = Lease.objects.select_for_update().filter(name="monitoring:global").first()
            if lease and lease.expires_at > now:
                raise CycleBusy("Цикл мониторинга уже выполняется.")
            if lease:
                recovered = True
                previous_holder = lease.holder
                lease.holder = holder
                lease.expires_at = now + timedelta(seconds=_lease_seconds())
                lease.save(update_fields=["holder", "expires_at", "updated_at"])
                for stale in Run.objects.filter(status="running", summary__holder=previous_holder):
                    stale.status = "interrupted"
                    stale.finished_at = now
                    stale.summary = {**stale.summary, "error": {"code": "lease_expired", "message": "Цикл прерван и восстановлен следующим запуском."}}
                    stale.save(update_fields=["status", "finished_at", "summary", "updated_at"])
            else:
                Lease.objects.create(name="monitoring:global", holder=holder, expires_at=now + timedelta(seconds=_lease_seconds()))
    except (IntegrityError, OperationalError) as exc:
        raise CycleBusy("Цикл мониторинга уже выполняется.") from exc
    return recovered


def _renew(holder, now=None):
    now = now or timezone.now()
    updated = Lease.objects.filter(name="monitoring:global", holder=holder, expires_at__gt=now).update(
        expires_at=now + timedelta(seconds=_lease_seconds())
    )
    if updated != 1:
        raise CycleBusy("Право на цикл мониторинга утрачено.")


def _release(holder):
    Lease.objects.filter(name="monitoring:global", holder=holder).delete()


def _already_notified_ids(owner):
    ids = set()
    rows = NotificationOutbox.objects.filter(run__summary__owner_id=owner.pk)
    for payload in rows.values_list("payload", flat=True):
        ids.update(payload.get("vacancy_ids", ()) if isinstance(payload, dict) else ())
    return ids


def _assessed_versions(owner):
    assessed = {}
    rows = Run.objects.filter(status__in=("complete", "partial"), summary__owner_id=owner.pk).order_by("id")
    for summary in rows.values_list("summary", flat=True):
        for item in summary.get("assessment_checkpoint", ()) if isinstance(summary, dict) else ():
            if isinstance(item, dict) and item.get("status") == "reject":
                assessed[item.get("vacancy_id")] = item.get("version")
    return assessed


def _short(value, limit):
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else f"{value[:limit - 1]}…"


def _digest_payload(assessments):
    site_url = str(getattr(settings, "JOB_SITE_URL", "") or os.environ.get("JOB_SITE_URL", "")).rstrip("/")
    if not site_url:
        return None, "site_url_unconfigured"
    list_url = f"{site_url}{reverse('vacancies')}"
    footer = f"\n\nПолный список: {list_url}"
    text = "Новые вакансии: подходят или требуют уточнения"
    vacancy_ids = [vacancy.pk for vacancy, _assessment in assessments]
    shown = 0
    for vacancy, assessment in assessments[:10]:
        record = vacancy.source_records.order_by("id").first()
        source_url = (record.apply_url or record.canonical_url) if record else ""
        reason = assessment.reasons[0] if assessment.reasons else "Нужна проверка условий."
        label = "Подходит" if assessment.status == "fit" else "Уточнить"
        entry = f"\n\n{label}: {_short(vacancy.title, 120)} — {_short(vacancy.company, 80)}\n{_short(reason, 240)}\n{source_url}"
        if len(text) + len(entry) + len(footer) > 3900:
            break
        text += entry
        shown += 1
    if len(assessments) > shown:
        count_line = f"\nЕщё {len(assessments) - shown}: {list_url}"
        if len(text) + len(count_line) + len(footer) <= 3900:
            text += count_line
    text += footer
    return {"text": text, "vacancy_ids": vacancy_ids, "total": len(assessments), "shown": shown, "list_url": list_url}, ""


def run_cycle(owner, *, adapters=None, evaluator=evaluate, collector=collect_sources, transport=None, now=None, schedule_slot=""):
    now = now or timezone.now()
    holder = uuid.uuid4().hex
    recovered = _acquire(holder, now)
    run = Run.objects.create(status="running", started_at=now, summary={"owner_id": owner.pk, "holder": holder, "schedule_slot": schedule_slot, "recovered": recovered})
    failure = None
    try:
        try:
            report = collector(owner, adapters=adapters, holder=f"cycle:{holder}", now=now)
        except CollectorBusy as exc:
            raise CycleBusy(str(exc)) from exc
        _renew(holder)
        profile = Profile.objects.filter(owner=owner).first()
        criteria = profile.criteria if profile else {}
        excluded = _already_notified_ids(owner)
        assessed_versions = _assessed_versions(owner)
        assessments = []
        checkpoint = []
        vacancies = Vacancy.objects.filter(owner=owner, hidden=False).order_by("first_seen_at", "id")
        for vacancy in vacancies:
            if vacancy.pk in excluded or assessed_versions.get(vacancy.pk) == vacancy.version:
                continue
            assessment = evaluator(vacancy, criteria)
            _renew(holder)
            if assessment.status in {"fit", "clarify"}:
                assessments.append((vacancy, assessment))
            elif assessment.status == "reject":
                checkpoint.append({"vacancy_id": vacancy.pk, "version": vacancy.version, "status": "reject"})
        payload, config_error = _digest_payload(assessments) if assessments else (None, "")
        outbox = None
        if payload:
            with transaction.atomic():
                _renew(holder)
                outbox = NotificationOutbox.objects.create(run=run, payload=payload)
            checkpoint.extend({"vacancy_id": vacancy.pk, "version": vacancy.version, "status": assessment.status} for vacancy, assessment in assessments)
            _renew(holder)
            deliver_digest(run, transport=transport)
            _renew(holder)
            outbox.refresh_from_db()
        run.summary = {
            **run.summary,
            "collection": {"succeeded": report.succeeded, "failed": report.failed, "skipped": report.skipped, "records": report.records},
            "assessment_checkpoint": checkpoint,
            "digest_count": len(assessments),
            "notification": outbox.status if outbox else ("disabled" if config_error else "empty"),
            "notification_error": config_error,
        }
        run.status = "partial" if report.failed else "complete"
    except Exception as exc:
        run.status = "error"
        run.summary = {
            **run.summary,
            "error": {
                "code": type(exc).__name__,
                "message": "Цикл мониторинга завершился ошибкой.",
            },
        }
        if isinstance(exc, CycleBusy):
            failure = CycleBusy("Цикл мониторинга уже выполняется или право на него утрачено.")
        else:
            failure = CycleExecutionError("Цикл мониторинга завершился ошибкой.")
    finally:
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "summary", "finished_at", "updated_at"])
        _release(holder)
    if failure is not None:
        raise failure
    return run
