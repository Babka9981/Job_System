"""Isolated, redirect-free HTTP worker. Input/output are JSON over stdio."""

import base64
import http.client
import json
import math
import socket
import ssl
import sys
from urllib.parse import urlsplit


_DEFAULT_SOCKET_TIMEOUT = 30.0
_MIN_SOCKET_TIMEOUT = 0.1
_MAX_SOCKET_TIMEOUT = 120.0


def _bounded_socket_timeout(value):
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid socket timeout") from exc
    if not math.isfinite(timeout):
        raise ValueError("invalid socket timeout")
    return min(max(timeout, _MIN_SOCKET_TIMEOUT), _MAX_SOCKET_TIMEOUT)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, pinned_ip, *, port, timeout):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def connect(self):
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, pinned_ip, *, port, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


def _exchange(spec):
    parsed = urlsplit(str(spec["url"]))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid URL")
    timeout = _bounded_socket_timeout(spec.get("socket_timeout", _DEFAULT_SOCKET_TIMEOUT))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    pinned_ips = spec.get("pinned_ips") or []
    if pinned_ips:
        cls = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
        connection = cls(parsed.hostname, str(pinned_ips[0]), port=port, timeout=timeout)
    else:
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = cls(parsed.hostname, port=port, timeout=timeout)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    body = base64.b64decode(spec.get("body_b64") or "", validate=True)
    max_bytes = min(max(int(spec.get("max_bytes", 1_000_000)), 1), 5_000_000)
    try:
        connection.request(str(spec.get("method") or "GET"), target, body=body or None, headers=spec.get("headers") or {})
        response = connection.getresponse()
        content = response.read(max_bytes + 1)
        if len(content) > max_bytes:
            return {"ok": False, "code": "too_large"}
        return {
            "ok": True,
            "status": response.status,
            "headers": dict(response.getheaders()),
            "body_b64": base64.b64encode(content).decode("ascii"),
        }
    finally:
        connection.close()


def main():
    try:
        spec = json.loads(sys.stdin.buffer.read(8_000_001))
        if not isinstance(spec, dict):
            raise ValueError("invalid request")
        result = _exchange(spec)
    except Exception:
        result = {"ok": False, "code": "network_error"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
