import ipaddress
import inspect
import json
import socket
import subprocess
import sys
import time
from functools import partial
from urllib.parse import urljoin, urlsplit

from django.utils import timezone

from jobs.intelligence.http_process import HTTPProcessError, minimal_subprocess_env, run_http_exchange


class FetchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    # is_global also rejects documentation, benchmark, multicast and unspecified ranges.
    return ip.is_global and not any((
        ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_reserved,
        ip.is_multicast, ip.is_unspecified,
    ))


def _deadline_aware(function, names):
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.kind == parameter.VAR_KEYWORD for parameter in parameters) or names <= {
        parameter.name for parameter in parameters
    }


def _network_transport(*, url, approved_ips, headers, timeout, max_bytes, deadline, clock, process_factory=None):
    try:
        result = run_http_exchange({
            "url": url,
            "method": "GET",
            "headers": headers,
            "max_bytes": max_bytes,
            "socket_timeout": min(timeout, max(deadline - clock(), 0.1)),
            "pinned_ips": list(approved_ips),
        }, deadline=deadline, clock=clock, process_factory=process_factory)
        return {
            "status": result["status"],
            "headers": result["headers"],
            "body": result["body"],
        }
    except HTTPProcessError as exc:
        if exc.code == "deadline_exceeded":
            raise FetchError("timeout", "Истёк общий лимит времени чтения страницы.") from None
        if exc.code == "too_large":
            raise FetchError("too_large", "Страница превышает лимит размера.") from None
        raise FetchError("network_error", "Страница временно недоступна.") from exc


class PublicFetcher:
    allowed_content_types = ("text/html", "text/plain", "application/xhtml+xml", "application/json")

    def __init__(
        self, *, timeout=10, max_bytes=1_000_000, max_redirects=3,
        resolver=None, transport=None, clock=None, dns_process_factory=None, http_process_factory=None,
    ):
        self.timeout = min(max(float(timeout), 0.1), 30.0)
        self.max_bytes = min(max(int(max_bytes), 1), 5_000_000)
        self.max_redirects = min(max(int(max_redirects), 0), 5)
        self.resolver = resolver
        self.transport = transport or partial(_network_transport, process_factory=http_process_factory)
        self.clock = clock or time.monotonic
        self.dns_process_factory = dns_process_factory or subprocess.Popen

    def _system_resolve(self, host, port, *, deadline):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise FetchError("timeout", "Истёк общий лимит времени DNS.")
        script = (
            "import json,socket,sys; "
            "print(json.dumps([x[4][0] for x in socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)]))"
        )
        process = self.dns_process_factory(
            [sys.executable, "-c", script, host, str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=minimal_subprocess_env(),
        )
        try:
            stdout, _ = process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise FetchError("timeout", "Истёк общий лимит времени DNS.") from exc
        if process.returncode != 0 or self.clock() > deadline:
            raise FetchError("dns_error", "Домен страницы не разрешается.")
        try:
            addresses = json.loads(stdout)
        except (TypeError, ValueError) as exc:
            raise FetchError("dns_error", "Домен страницы не разрешается.") from exc
        return [(0, socket.SOCK_STREAM, 0, "", (address, port)) for address in addresses]

    def _resolve(self, host, port, *, deadline):
        try:
            if self.resolver is None:
                answers = self._system_resolve(host, port, deadline=deadline)
            else:
                if not _deadline_aware(self.resolver, {"deadline", "clock"}):
                    raise FetchError("unsafe_transport", "DNS resolver не поддерживает отменяемый deadline.")
                answers = self.resolver(host, port, deadline=deadline, clock=self.clock)
                if self.clock() > deadline:
                    raise FetchError("timeout", "Истёк общий лимит времени DNS.")
        except (OSError, socket.gaierror) as exc:
            raise FetchError("dns_error", "Домен страницы не разрешается.") from exc
        addresses = tuple(dict.fromkeys(answer[4][0] for answer in answers if answer and len(answer) > 4))
        if not addresses or not all(_is_public(address) for address in addresses):
            raise FetchError("blocked_address", "Внутренний или зарезервированный адрес запрещён.")
        return addresses

    def fetch(self, url: str, *, deadline=None):
        started = self.clock()
        deadline = min(deadline, started + self.timeout) if deadline is not None else started + self.timeout
        current = str(url)
        for redirect_count in range(self.max_redirects + 1):
            parsed = urlsplit(current)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise FetchError("invalid_url", "Разрешены только публичные HTTP(S) URL без credentials.")
            try:
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
            except ValueError as exc:
                raise FetchError("invalid_url", "Порт URL задан некорректно.") from exc
            approved_ips = self._resolve(parsed.hostname, port, deadline=deadline)
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise FetchError("timeout", "Истёк лимит времени чтения страницы.")
            try:
                if not _deadline_aware(self.transport, {"deadline", "clock"}):
                    raise FetchError("unsafe_transport", "HTTP transport не поддерживает отменяемый deadline.")
                response = self.transport(
                    url=current,
                    approved_ips=approved_ips,
                    headers={"Accept": "text/html,text/plain,application/xhtml+xml,application/json", "User-Agent": "JobSystemResearch/1.0"},
                    timeout=remaining,
                    max_bytes=self.max_bytes,
                    deadline=deadline,
                    clock=self.clock,
                )
                if self.clock() > deadline:
                    raise FetchError("timeout", "Истёк общий лимит времени чтения страницы.")
            except FetchError:
                raise
            except (OSError, TimeoutError) as exc:
                raise FetchError("timeout", "Страница временно недоступна.") from exc
            status = int(response.get("status", 0))
            headers = {str(k).lower(): str(v) for k, v in (response.get("headers") or {}).items()}
            body = response.get("body") or b""
            if status in {301, 302, 303, 307, 308}:
                if redirect_count >= self.max_redirects:
                    raise FetchError("too_many_redirects", "Превышен лимит перенаправлений.")
                location = headers.get("location", "")
                if not location:
                    raise FetchError("invalid_redirect", "Перенаправление без адреса.")
                current = urljoin(current, location)
                continue
            if status < 200 or status >= 300:
                raise FetchError("http_error", "Страница вернула ошибку.")
            if len(body) > self.max_bytes:
                raise FetchError("too_large", "Страница превышает лимит размера.")
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in self.allowed_content_types:
                raise FetchError("content_type", "Формат страницы не поддерживается.")
            encoding = "utf-8"
            if "charset=" in headers.get("content-type", ""):
                encoding = headers["content-type"].split("charset=", 1)[1].split(";", 1)[0].strip()
            try:
                content = body.decode(encoding, errors="replace")
            except LookupError:
                content = body.decode("utf-8", errors="replace")
            return {"url": current, "content": content, "content_type": content_type, "checked_at": timezone.now()}
        raise FetchError("too_many_redirects", "Превышен лимит перенаправлений.")
