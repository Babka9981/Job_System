import re
from dataclasses import dataclass
from urllib.parse import urlsplit


PUBLIC_HANDLE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{3,31}$")


class TelegramLinkError(ValueError):
    pass


def normalize_source_link(value: str) -> str:
    """Return the canonical public channel URL; invite and post URLs are not registry IDs."""
    value = (value or "").strip()
    if value.startswith("@"):
        handle = value[1:]
    else:
        candidate = value if "://" in value else f"https://{value}"
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise TelegramLinkError("Ссылка Telegram должна использовать HTTP(S).")
        if parsed.hostname and parsed.hostname.lower() not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
            raise TelegramLinkError("Ссылка Telegram должна вести на t.me.")
        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[0].lower() == "s":
            parts = parts[1:]
        if len(parts) != 1 or parsed.query or parsed.fragment:
            raise TelegramLinkError("Укажите ссылку на публичный Telegram-канал, не на пост или приглашение.")
        handle = parts[0] if parts else ""
    if not PUBLIC_HANDLE.fullmatch(handle):
        raise TelegramLinkError("Ссылка Telegram содержит недопустимое имя канала.")
    return f"https://t.me/{handle.lower()}"


def post_url(message) -> str:
    if message.public_handle:
        return f"https://t.me/{message.public_handle.lstrip('@')}/{message.message_id}"
    peer_id = str(abs(int(message.peer_id or 0)))
    if peer_id.startswith("100"):
        peer_id = peer_id[3:]
    if not peer_id:
        raise TelegramLinkError("Для приватной ссылки нужен идентификатор канала.")
    return f"https://t.me/c/{peer_id}/{message.message_id}"

