import importlib.util
import os
from pathlib import Path

from django.conf import settings

from jobs.sources.telegram.adapter import HistoryAccessError, HistoryMessage, HistoryPage, TelegramUpdate


class TelegramConfigurationError(RuntimeError):
    def __init__(self, code, safe_message):
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


def telethon_available():
    return importlib.util.find_spec("telethon") is not None


def private_session_path(requested=None):
    root = Path(settings.PRIVATE_ROOT).resolve()
    path = Path(requested).resolve() if requested else (root / "telegram" / "user-account").resolve()
    if not path.is_relative_to(root):
        raise TelegramConfigurationError("unsafe_session_path", "Telegram session должна храниться внутри private root.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    return path


def telethon_runtime_status(environ=None):
    environ = os.environ if environ is None else environ
    if not telethon_available():
        return {"code": "dependency_unavailable", "label": "Компонент не установлен", "message": "Установите серверный MTProto-клиент."}
    if not str(environ.get("TELEGRAM_API_ID", "")).isdigit() or not environ.get("TELEGRAM_API_HASH"):
        return {"code": "config_unavailable", "label": "Не настроено", "message": "Добавьте локальные параметры Telegram API."}
    try:
        session = private_session_path(environ.get("TELEGRAM_SESSION_PATH") or None)
    except TelegramConfigurationError as error:
        return {"code": error.code, "label": "Небезопасная настройка", "message": error.safe_message}
    exists = session.with_suffix(".session").exists() or session.exists()
    return {
        "code": "session_present" if exists else "auth_required",
        "label": "Session найдена" if exists else "Нужен локальный вход",
        "message": "Живая авторизация не проверена." if exists else "Выполните локальную интерактивную авторизацию аккаунта.",
    }


class TelethonHistoryClient:
    """Optional production gateway for the user session, never the notification bot."""

    def __init__(self, client):
        self.client = client
        self._updates = []

    @classmethod
    def from_environment(cls, environ=None):
        environ = os.environ if environ is None else environ
        if not telethon_available():
            raise TelegramConfigurationError("dependency_unavailable", "Серверный MTProto-клиент не установлен.")
        raw_id = environ.get("TELEGRAM_API_ID", "")
        api_hash = environ.get("TELEGRAM_API_HASH", "")
        if not str(raw_id).isdigit() or not api_hash:
            raise TelegramConfigurationError("config_unavailable", "Локальные параметры Telegram API не настроены.")
        from telethon.sync import TelegramClient

        session = private_session_path(environ.get("TELEGRAM_SESSION_PATH") or None)
        client = TelegramClient(str(session), int(raw_id), api_hash)
        return cls(client)

    def connect(self):
        self.client.connect()
        self._harden_session_file()
        self._install_update_handlers()
        return self

    def disconnect(self):
        self.client.disconnect()

    def _harden_session_file(self):
        if os.name == "nt":
            return
        path = Path(getattr(self.client.session, "filename", "") or "")
        if path.is_file():
            path.chmod(0o600)

    def _install_update_handlers(self):
        try:
            from telethon import events

            @self.client.on(events.MessageEdited)
            def edited(event):
                message = self._message(event.message)
                self._updates.append(TelegramUpdate("edited", message.message_id, message, getattr(event, "chat_id", None)))

            @self.client.on(events.MessageDeleted)
            def deleted(event):
                for message_id in event.deleted_ids:
                    self._updates.append(TelegramUpdate("deleted", int(message_id), peer_id=getattr(event, "chat_id", None)))
        except Exception:
            # Live update subscription is supplementary; history remains available.
            return

    def authorize_interactive(self, *, phone_reader, code_reader, password_reader):
        self.client.start(phone=phone_reader, code_callback=code_reader, password=password_reader)
        self._harden_session_file()
        return bool(self.client.is_user_authorized())

    def is_authorized(self):
        try:
            return bool(self.client.is_user_authorized())
        except Exception:
            return False

    def _message(self, item):
        entity = getattr(item, "chat", None)
        handle = getattr(entity, "username", None)
        peer_id = getattr(item, "chat_id", None)
        return HistoryMessage(
            message_id=int(item.id),
            text=getattr(item, "message", "") or "",
            published_at=getattr(item, "date", None),
            edited_at=getattr(item, "edit_date", None),
            peer_id=peer_id,
            public_handle=handle,
            origin="forward" if getattr(item, "fwd_from", None) is not None else "api",
        )

    def fetch_history(self, peer, *, after_id, offset_id, limit):
        try:
            minimum = max(int(after_id), 0)
            items = list(self.client.iter_messages(
                peer, min_id=minimum, offset_id=int(offset_id or 0), limit=limit
            ))
            messages = tuple(self._message(item) for item in items if getattr(item, "message", None))
            raw_ids = [int(item.id) for item in items if getattr(item, "id", None) is not None]
            next_offset = min(raw_ids) if len(items) == limit and raw_ids else None
            return HistoryPage(
                messages,
                next_offset,
                raw_count=len(items),
                raw_max_id=max(raw_ids, default=0),
            )
        except Exception as error:
            raise self._safe_error(error) from error

    def fetch_updates(self, peer, *, checkpoint):
        try:
            entity_id = int(getattr(self.client.get_entity(peer), "id"))
        except Exception:
            return ()
        matched = []
        remaining = []
        for update in self._updates:
            raw = str(abs(int(update.peer_id or 0)))
            normalized = int(raw[3:] if raw.startswith("100") else raw or 0)
            (matched if normalized == entity_id else remaining).append(update)
        self._updates = remaining
        return tuple(matched)

    @staticmethod
    def _safe_error(error):
        name = type(error).__name__
        if name in {"FloodWaitError", "FloodError"}:
            retry_after = getattr(error, "seconds", None)
            return HistoryAccessError("flood_wait", "Telegram просит повторить позже.", retryable=True, retry_after=retry_after)
        if name in {"ChannelPrivateError", "ChatAdminRequiredError", "UserBannedInChannelError"}:
            return HistoryAccessError("access_denied", "У аккаунта нет доступа к этому источнику.")
        if name in {"AuthKeyUnregisteredError", "SessionRevokedError", "UnauthorizedError"}:
            return HistoryAccessError("session_unauthorized", "Пользовательская Telegram-сессия требует повторного подключения.")
        return HistoryAccessError("history_unavailable", "История Telegram временно недоступна.", retryable=True)
