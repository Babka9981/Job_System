import base64
import binascii
import json
import os
import subprocess
import sys
import time
from pathlib import Path


class HTTPProcessError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_WORKER = Path(__file__).with_name("http_worker.py")
_MAX_BODY_BYTES = 5_000_000
_MAX_BODY_B64_BYTES = ((_MAX_BODY_BYTES + 2) // 3) * 4
_MAX_HEADER_COUNT = 200
_MAX_HEADER_BYTES = 256_000
_CHILD_ENV_ALLOWLIST = {
    "COMSPEC", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "PATHEXT",
    "SSL_CERT_DIR", "SSL_CERT_FILE", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR",
    "TZ", "WINDIR",
}


def minimal_subprocess_env(environ=None):
    """Return execution-only environment without application or proxy secrets."""
    environ = os.environ if environ is None else environ
    return {
        key: value
        for key, value in environ.items()
        if key.upper() in _CHILD_ENV_ALLOWLIST
    }


def _validate_success_envelope(result):
    status = result.get("status")
    headers = result.get("headers")
    body_b64 = result.get("body_b64")
    if type(status) is not int or not 100 <= status <= 599:
        raise HTTPProcessError("invalid_response")
    if not isinstance(headers, dict) or len(headers) > _MAX_HEADER_COUNT:
        raise HTTPProcessError("invalid_response")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items()):
        raise HTTPProcessError("invalid_response")
    header_bytes = sum(len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in headers.items())
    if header_bytes > _MAX_HEADER_BYTES:
        raise HTTPProcessError("invalid_response")
    if not isinstance(body_b64, str) or len(body_b64.encode("ascii", errors="ignore")) > _MAX_BODY_B64_BYTES:
        raise HTTPProcessError("invalid_response")
    try:
        body = base64.b64decode(body_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPProcessError("invalid_response") from exc
    if len(body) > _MAX_BODY_BYTES:
        raise HTTPProcessError("invalid_response")
    return body


def run_http_exchange(request, *, deadline, clock=None, process_factory=None):
    """Run one redirect-free HTTP exchange in a process with a hard deadline.

    Request material (including credentials) is sent only through stdin.  The
    command and child environment therefore remain safe to inspect in process
    lists and diagnostics.
    """
    clock = clock or time.monotonic
    remaining = deadline - clock()
    if remaining <= 0:
        raise HTTPProcessError("deadline_exceeded")
    factory = process_factory or subprocess.Popen
    child_env = minimal_subprocess_env()
    try:
        process = factory(
            [sys.executable, str(_WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=child_env,
            close_fds=True,
        )
    except OSError as exc:
        raise HTTPProcessError("network_error") from exc
    payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        stdout, _ = process.communicate(input=payload, timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise HTTPProcessError("deadline_exceeded") from exc
    except OSError as exc:
        if process.poll() is None:
            process.kill()
            process.communicate()
        raise HTTPProcessError("network_error") from exc
    if clock() > deadline:
        raise HTTPProcessError("deadline_exceeded")
    if process.returncode != 0:
        raise HTTPProcessError("network_error")
    try:
        result = json.loads(stdout.decode("utf-8") if isinstance(stdout, bytes) else stdout)
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPProcessError("invalid_response") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        code = result.get("code") if isinstance(result, dict) else None
        raise HTTPProcessError(code if code in {"network_error", "too_large", "invalid_response"} else "network_error")
    result["body"] = _validate_success_envelope(result)
    return result
