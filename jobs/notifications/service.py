import json
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib import error, parse, request

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from jobs.models.models import NotificationOutbox


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    code: str = ""
    message: str = ""


class TelegramBotTransport:
    def __init__(self, *, token=None, chat_id=None, timeout=10, opener=None):
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")
        self.timeout = timeout
        self.opener = opener or request.urlopen

    @property
    def configured(self):
        return bool(str(self.token).strip() and str(self.chat_id).strip())

    def send(self, text):
        if not self.configured:
            return DeliveryResult("failed", "bot_unconfigured", "Telegram bot или owner chat не настроены.")
        body = parse.urlencode({"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
        req = request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with self.opener(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, socket.timeout):
            return DeliveryResult("uncertain", "send_timeout", "Telegram не подтвердил доставку до таймаута.")
        except error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return DeliveryResult("uncertain", "send_timeout", "Telegram не подтвердил доставку до таймаута.")
            return DeliveryResult("failed", "transport_error", "Не удалось доставить Telegram-подборку.")
        except OSError:
            return DeliveryResult("failed", "transport_error", "Не удалось доставить Telegram-подборку.")
        except (ValueError, UnicodeError):
            return DeliveryResult("uncertain", "ambiguous_response", "Ответ Telegram после отправки невозможно подтвердить.")
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            return DeliveryResult("uncertain", "ambiguous_response", "Ответ Telegram после отправки невозможно подтвердить.")
        if payload["ok"] is True:
            return DeliveryResult("sent")
        return DeliveryResult("failed", "telegram_rejected", "Telegram отклонил сообщение.")


def _max_attempts():
    try:
        value = int(getattr(settings, "NOTIFICATION_MAX_ATTEMPTS", 3))
    except (TypeError, ValueError):
        value = 3
    return max(1, min(value, 10))


def _claim_seconds():
    try:
        return max(30, int(getattr(settings, "NOTIFICATION_CLAIM_SECONDS", 300)))
    except (TypeError, ValueError):
        return 300


def _configured_failure(outbox, transport):
    if getattr(transport, "configured", True) is True:
        return False
    outbox.status = NotificationOutbox.Status.FAILED
    outbox.last_error_code = "bot_unconfigured"
    outbox.last_error_message = "Telegram bot или owner chat не настроены."
    outbox.save(update_fields=["status", "last_error_code", "last_error_message", "updated_at"])
    return True


def _claim(outbox_id, now):
    with transaction.atomic():
        outbox = NotificationOutbox.objects.select_for_update().select_related("run").get(pk=outbox_id)
        if outbox.status in {NotificationOutbox.Status.SENT, NotificationOutbox.Status.UNCERTAIN}:
            return outbox, ""
        payload = dict(outbox.payload or {})
        claim = payload.get("delivery_claim") if isinstance(payload.get("delivery_claim"), dict) else None
        if claim:
            expires_at = parse_datetime(str(claim.get("expires_at", "")))
            if expires_at and expires_at > now:
                return outbox, ""
            outbox.status = NotificationOutbox.Status.UNCERTAIN
            outbox.last_error_code = "claim_expired"
            outbox.last_error_message = "Предыдущая отправка не завершила подтверждение доставки."
            outbox.save(update_fields=["status", "last_error_code", "last_error_message", "updated_at"])
            return outbox, ""
        if outbox.attempts >= _max_attempts():
            return outbox, ""
        holder = uuid.uuid4().hex
        payload["delivery_claim"] = {"holder": holder, "expires_at": (now + timedelta(seconds=_claim_seconds())).isoformat()}
        outbox.payload = payload
        outbox.attempts += 1
        outbox.save(update_fields=["payload", "attempts", "updated_at"])
        return outbox, holder


def _finish(outbox_id, holder, result):
    with transaction.atomic():
        outbox = NotificationOutbox.objects.select_for_update().get(pk=outbox_id)
        payload = dict(outbox.payload or {})
        claim = payload.get("delivery_claim") if isinstance(payload.get("delivery_claim"), dict) else {}
        if claim.get("holder") != holder:
            outbox.status = NotificationOutbox.Status.UNCERTAIN
            outbox.last_error_code = "claim_lost"
            outbox.last_error_message = "Право завершить отправку было утрачено после сетевого вызова."
        else:
            payload.pop("delivery_claim", None)
            outbox.payload = payload
            outbox.status = result.status
            outbox.last_error_code = result.code
            outbox.last_error_message = result.message
        outbox.save(update_fields=["payload", "status", "last_error_code", "last_error_message", "updated_at"])
        return outbox


def deliver_digest(run, *, transport=None, now=None):
    outbox = run.notifications.order_by("id").first()
    if outbox is None:
        return None
    if outbox.status in {NotificationOutbox.Status.SENT, NotificationOutbox.Status.UNCERTAIN}:
        return outbox
    transport = transport or TelegramBotTransport()
    if _configured_failure(outbox, transport):
        return outbox
    outbox, holder = _claim(outbox.pk, now or timezone.now())
    if not holder:
        return outbox
    result = transport.send(outbox.payload.get("text", ""))
    return _finish(outbox.pk, holder, result)


def retry_delivery(outbox_id, *, transport=None, confirm_uncertain=False, now=None):
    with transaction.atomic():
        outbox = NotificationOutbox.objects.select_for_update().select_related("run").get(pk=outbox_id)
        if outbox.status == NotificationOutbox.Status.SENT:
            return outbox
        if outbox.status == NotificationOutbox.Status.UNCERTAIN and not confirm_uncertain:
            return outbox
        if outbox.status == NotificationOutbox.Status.UNCERTAIN:
            outbox.status = NotificationOutbox.Status.FAILED
            payload = dict(outbox.payload or {})
            payload.pop("delivery_claim", None)
            outbox.payload = payload
            outbox.save(update_fields=["status", "payload", "updated_at"])
    return deliver_digest(outbox.run, transport=transport, now=now)


def reconcile_delivery(outbox_id, *, delivered):
    with transaction.atomic():
        outbox = NotificationOutbox.objects.select_for_update().get(pk=outbox_id)
        if outbox.status != NotificationOutbox.Status.UNCERTAIN:
            return outbox
        payload = dict(outbox.payload or {})
        payload.pop("delivery_claim", None)
        outbox.payload = payload
        outbox.status = NotificationOutbox.Status.SENT if delivered else NotificationOutbox.Status.FAILED
        outbox.last_error_code = "" if delivered else "delivery_not_confirmed"
        outbox.last_error_message = "" if delivered else "Владелец подтвердил отсутствие доставки."
        outbox.save(update_fields=["payload", "status", "last_error_code", "last_error_message", "updated_at"])
        return outbox
