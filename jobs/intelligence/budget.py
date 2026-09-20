from collections import defaultdict
from decimal import Decimal, InvalidOperation
from threading import Lock

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from jobs.models.models import UsageLedger, UsageReservation


class BudgetUnavailable(Exception):
    code = "budget_unavailable"


class BudgetPending(Exception):
    code = "budget_pending"


_owner_locks = defaultdict(Lock)


def _amount(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.0001"))
    except (InvalidOperation, TypeError, ValueError):
        raise BudgetUnavailable("Денежный лимит настроен некорректно.") from None
    if not amount.is_finite():
        raise BudgetUnavailable("Денежный лимит настроен некорректно.")
    return amount


def reserve_usage(owner, *, kind, max_cost, daily_limit):
    max_cost = _amount(max_cost)
    if daily_limit in (None, "") or _amount(daily_limit) <= 0:
        raise BudgetUnavailable("Положительный суточный лимит не настроен.")
    daily_limit = _amount(daily_limit)
    if max_cost <= 0:
        raise BudgetUnavailable("Не удалось определить максимальную стоимость вызова.")

    with _owner_locks[owner.pk], transaction.atomic():
        user_model = get_user_model()
        if connection.vendor == "sqlite":
            table = connection.ops.quote_name(user_model._meta.db_table)
            with connection.cursor() as cursor:
                cursor.execute(f"UPDATE {table} SET id = id WHERE id = %s", [owner.pk])
        else:
            user_model.objects.select_for_update().get(pk=owner.pk)
        today = timezone.localdate()
        spent = UsageLedger.objects.filter(owner=owner, created_at__date=today).aggregate(total=Sum("cost"))["total"] or Decimal("0")
        held = UsageReservation.objects.filter(
            owner=owner,
            created_at__date=today,
            status__in=["reserved", "pending"],
        ).aggregate(total=Sum("units"))["total"] or Decimal("0")
        if spent + held + max_cost > daily_limit:
            raise BudgetPending("Суточный лимит исчерпан; вызов ожидает следующего окна.")
        return UsageReservation.objects.create(owner=owner, kind=kind, units=max_cost, status="reserved")


@transaction.atomic
def settle_usage(reservation, *, actual_cost, units=0, metadata=None):
    locked = UsageReservation.objects.select_for_update().get(pk=reservation.pk)
    actual_cost = _amount(actual_cost)
    if locked.status != "reserved":
        raise ValueError("Резервирование уже завершено.")
    if actual_cost < 0 or actual_cost > locked.units:
        raise ValueError("Фактическая стоимость выходит за пределы резерва.")
    UsageLedger.objects.create(
        owner=locked.owner,
        kind=locked.kind,
        units=_amount(units),
        cost=actual_cost,
        metadata=metadata or {},
    )
    locked.status = "settled"
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def mark_usage_pending(reservation):
    locked = UsageReservation.objects.select_for_update().get(pk=reservation.pk)
    if locked.status == "reserved":
        locked.status = "pending"
        locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def release_usage(reservation):
    locked = UsageReservation.objects.select_for_update().get(pk=reservation.pk)
    if locked.status == "reserved":
        locked.status = "released"
        locked.save(update_fields=["status", "updated_at"])
    return locked
