from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings

from jobs.models.models import Run


DEFAULT_SLOTS = (time(9), time(13), time(17), time(21))


@dataclass(frozen=True)
class ScheduleDecision:
    enabled: bool
    due: bool
    slot_key: str = ""
    reason: str = ""


def parse_slots(value):
    if isinstance(value, (list, tuple)):
        value = ",".join(str(item) for item in value)
    if value in (None, ""):
        return DEFAULT_SLOTS
    slots = []
    for raw in str(value).split(","):
        try:
            parsed = time.fromisoformat(raw.strip())
        except ValueError:
            return ()
        if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
            return ()
        slots.append(parsed)
    return tuple(sorted(set(slots)))


def _valid_instants(day, slot, zone):
    instants = []
    naive = datetime.combine(day, slot)
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        instant = local.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == naive and instant not in instants:
            instants.append(instant)
    return tuple(sorted(instants))


def schedule_decision(*, now=None, timezone_name=None, slots=None, enabled=None):
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    enabled = getattr(settings, "MONITORING_ENABLED", True) if enabled is None else enabled
    if enabled is not True:
        return ScheduleDecision(False, False, reason="monitoring_disabled")
    timezone_name = timezone_name if timezone_name is not None else getattr(settings, "USER_TIME_ZONE", "")
    if not str(timezone_name).strip():
        return ScheduleDecision(False, False, reason="timezone_not_configured")
    try:
        zone = ZoneInfo(str(timezone_name))
    except ZoneInfoNotFoundError:
        return ScheduleDecision(False, False, reason="timezone_invalid")
    parsed_slots = parse_slots(slots if slots is not None else getattr(settings, "MONITORING_SCHEDULE", ""))
    if not parsed_slots:
        return ScheduleDecision(False, False, reason="schedule_invalid")
    local_day = now.astimezone(zone).date()
    candidates = []
    for slot in parsed_slots:
        instants = _valid_instants(local_day, slot, zone)
        if instants and instants[0] <= now:
            candidates.append((instants[0], f"{local_day.isoformat()}T{slot.strftime('%H:%M')}@{zone.key}"))
    if not candidates:
        return ScheduleDecision(True, False, reason="before_first_slot")
    _, slot_key = max(candidates)
    already_ran = Run.objects.filter(
        summary__schedule_slot=slot_key, status__in=("complete", "partial")
    ).exists()
    return ScheduleDecision(True, not already_ran, slot_key=slot_key, reason="already_ran" if already_ran else "due")
