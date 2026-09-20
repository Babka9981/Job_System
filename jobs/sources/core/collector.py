from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from jobs.models.models import Lease, Source
from jobs.sources.core.contracts import SourceCollectionError
from jobs.sources.keyed.crypto_jobs_list import CryptoJobsListAdapter
from jobs.sources.keyed.remote_rocketship import RemoteRocketshipAdapter, TemporaryRocketshipStore
from jobs.sources.keyed.web3_career import Web3CareerAdapter
from jobs.sources.public.himalayas import HimalayasAdapter
from jobs.sources.public.http import JsonHttpClient
from jobs.sources.public.jobicy import JobicyAdapter
from jobs.sources.public.remote_ok import RemoteOkAdapter
from jobs.sources.rvc.adapter import RvcAdapter, RvcMcpClient, RvcStreamableHttpToolCaller
from jobs.vacancies.services import upsert_record


class CollectorBusy(Exception):
    pass


@dataclass(frozen=True)
class CollectionReport:
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    records: int = 0


def default_adapters(http=None, *, keyed_http=None, rvc_call_tool=None):
    client = http or JsonHttpClient()
    rvc_transport = rvc_call_tool or RvcStreamableHttpToolCaller()
    return {
        "remote_ok": RemoteOkAdapter(http=client),
        "himalayas": HimalayasAdapter(http=client),
        "jobicy": JobicyAdapter(http=client),
        "web3_career": Web3CareerAdapter(http=keyed_http),
        "crypto_jobs_list": CryptoJobsListAdapter(http=keyed_http),
        "remote_rocketship": RemoteRocketshipAdapter(http=keyed_http),
        "rvc": RvcAdapter(client=RvcMcpClient(call_tool=rvc_transport)),
    }


def _acquire_lease(name, holder, now, ttl_seconds=300):
    try:
        with transaction.atomic():
            lease = Lease.objects.select_for_update().filter(name=name).first()
            if lease and lease.expires_at > now:
                raise CollectorBusy("Сбор источников уже выполняется.")
            if lease:
                lease.holder = holder
                lease.expires_at = now + timedelta(seconds=ttl_seconds)
                lease.save(update_fields=["holder", "expires_at", "updated_at"])
            else:
                Lease.objects.create(name=name, holder=holder, expires_at=now + timedelta(seconds=ttl_seconds))
    except IntegrityError as exc:
        raise CollectorBusy("Сбор источников уже выполняется.") from exc


def _release_lease(name, holder):
    Lease.objects.filter(name=name, holder=holder).delete()


def _renew_lease(name, holder, now, ttl_seconds=300):
    lease = Lease.objects.select_for_update().filter(name=name).first()
    if not lease or lease.holder != holder or lease.expires_at <= now:
        raise CollectorBusy("Право на сбор источников утрачено.")
    lease.expires_at = now + timedelta(seconds=ttl_seconds)
    lease.save(update_fields=["expires_at", "updated_at"])


def _is_due(source, now):
    if not isinstance(source.config, dict):
        raise SourceCollectionError("invalid_source_config", "Настройки источника имеют неверный формат.")
    try:
        raw_interval = source.config.get("service_interval_seconds") or 0
        if isinstance(raw_interval, bool) or (isinstance(raw_interval, float) and not raw_interval.is_integer()):
            raise ValueError
        interval = max(int(raw_interval), 0)
    except (TypeError, ValueError, OverflowError):
        raise SourceCollectionError("invalid_source_config", "Интервал источника задан неверно.")
    if source.cursor:
        return True
    return not interval or not source.last_success or source.last_success + timedelta(seconds=interval) <= now


def _safe_failure(source, now, code, message):
    config = dict(source.config) if isinstance(source.config, dict) else {}
    config.update({
        "last_check": now.isoformat(),
        "last_error": {"code": code, "message": message},
    })
    source.config = config
    source.status = Source.Status.ERROR
    source.save(update_fields=["config", "status", "updated_at"])


def _fenced_failure(source, lease_name, holder, now, code, message):
    with transaction.atomic():
        _renew_lease(lease_name, holder, now)
        _safe_failure(source, now, code, message)


def _checkpoint(source, batch, now, coverage):
    config = dict(source.config) if isinstance(source.config, dict) else {}
    config.update({"last_check": now.isoformat(), "last_error": None})
    source.config = config
    source.cursor = batch.next_cursor or ""
    source.coverage = coverage
    source.last_success = now
    source.status = Source.Status.LIMITED if coverage.get("truncated") else Source.Status.READY
    source.save(update_fields=["config", "cursor", "coverage", "last_success", "status", "updated_at"])


def _bind_record(source, record):
    bound = dict(record)
    if bound.get("source_slug") != source.slug:
        bound["description_permission"] = False
        bound["adapter_confirmed_permalink"] = False
    bound["source_slug"] = source.slug
    return bound


def _as_datetime(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if timezone.is_naive(value) else value
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is not None and timezone.is_naive(parsed):
            return parsed.replace(tzinfo=UTC)
        return parsed
    return None


def _initial_record_allowed(record, cutoff):
    published_at = _as_datetime(record.get("published_at"))
    return published_at is None or published_at >= cutoff


def _merge_coverage(current, batch):
    current = dict(current or {})
    starts = [
        value for value in (
            _as_datetime(current.get("window_start")),
            batch.coverage.window_start,
        ) if value is not None
    ]
    reasons = []
    for reason in (current.get("reason"), batch.coverage.reason):
        if reason and reason not in reasons:
            reasons.append(reason)
    updated_values = [
        value for value in (
            _as_datetime(current.get("provider_updated_at")),
            batch.provider_updated_at,
        ) if value is not None
    ]
    return {
        "window_start": min(starts).isoformat() if starts else None,
        "truncated": bool(current.get("truncated") or batch.coverage.truncated),
        "reason": " · ".join(reasons),
        "provider_updated_at": max(updated_values).isoformat() if updated_values else None,
    }


def collect_sources(
    owner,
    *,
    adapters=None,
    holder,
    now=None,
    source_slugs=None,
    max_pages=100,
    clock=None,
    rvc_call_tool=None,
    temporary_store=None,
):
    """Collect enabled sources under one lease; checkpoint only committed pages."""
    clock = clock or timezone.now
    now = now or clock()
    adapters = default_adapters(rvc_call_tool=rvc_call_tool) if adapters is None else adapters
    temporary_store = temporary_store or TemporaryRocketshipStore(clock=clock)
    lease_name = f"source-collector:{owner.pk}"
    _acquire_lease(lease_name, holder, now)
    succeeded = failed = skipped = records = 0
    try:
        queryset = Source.objects.filter(owner=owner, enabled=True).order_by("id")
        if source_slugs is not None:
            queryset = queryset.filter(slug__in=source_slugs)
        for source in queryset:
            adapter = adapters.get(source.adapter)
            if adapter is None:
                skipped += 1
                continue
            try:
                if not _is_due(source, now):
                    skipped += 1
                    continue
                pages = 0
                aggregate_coverage = dict(source.coverage or {}) if source.cursor else {}
                while pages < max_pages:
                    previous_cursor = source.cursor
                    batch = adapter.collect(source, previous_cursor or None)
                    page_now = clock()
                    records_to_store = tuple(
                        record for record in batch.records
                        if _initial_record_allowed(record, page_now - timedelta(days=7))
                    )
                    next_coverage = _merge_coverage(aggregate_coverage, batch)
                    if source.adapter == "remote_rocketship":
                        with transaction.atomic():
                            _renew_lease(lease_name, holder, page_now)
                        receipt = temporary_store.store_page(
                            owner,
                            source,
                            replace(batch, records=records_to_store),
                            page_key=(
                                f"{previous_cursor or 'initial'}:"
                                f"{source.last_success.isoformat() if source.last_success else 'never'}"
                            ),
                        )
                        try:
                            checkpoint_now = clock()
                            with transaction.atomic():
                                _renew_lease(lease_name, holder, checkpoint_now)
                                _checkpoint(source, batch, checkpoint_now, next_coverage)
                        except Exception:
                            temporary_store.rollback_page(owner, source, receipt)
                            raise
                    else:
                        with transaction.atomic():
                            _renew_lease(lease_name, holder, page_now)
                            for record in records_to_store:
                                upsert_record(_bind_record(source, record), owner=owner)
                            _checkpoint(source, batch, page_now, next_coverage)
                    aggregate_coverage = next_coverage
                    records += len(records_to_store)
                    pages += 1
                    if not batch.next_cursor:
                        break
                    if batch.next_cursor == previous_cursor:
                        raise SourceCollectionError("cursor_stalled", "Источник вернул повторяющуюся позицию.")
                else:
                    raise SourceCollectionError("page_limit", "Достигнут безопасный предел страниц.")
            except CollectorBusy:
                raise
            except SourceCollectionError as exc:
                _fenced_failure(source, lease_name, holder, clock(), exc.code, exc.safe_message)
                failed += 1
            except Exception:
                _fenced_failure(
                    source, lease_name, holder, clock(),
                    "adapter_error", "Не удалось обработать ответ источника.",
                )
                failed += 1
            else:
                succeeded += 1
        return CollectionReport(succeeded=succeeded, failed=failed, skipped=skipped, records=records)
    finally:
        _release_lease(lease_name, holder)
