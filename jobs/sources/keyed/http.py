import json
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from jobs.sources.core.contracts import SourceCollectionError


class KeyedJsonHttpClient:
    """Bounded JSON transport that never includes URLs, bodies, or credentials in errors."""

    def __init__(self, *, opener=urlopen, sleeper=time.sleep, max_attempts=3):
        self.opener = opener
        self.sleeper = sleeper
        self.max_attempts = max(1, min(int(max_attempts), 3))

    def get_json(self, url, *, params=None, headers=None, timeout=15, before_attempt=None):
        target = f"{url}?{urlencode(params)}" if params else url
        return self._request(Request(target, headers=self._headers(headers)), timeout, before_attempt)

    def post_json(self, url, *, json_body, headers=None, timeout=15, before_attempt=None):
        request = Request(
            url,
            data=json.dumps(json_body, separators=(",", ":")).encode("utf-8"),
            headers=self._headers({"Content-Type": "application/json", **(headers or {})}),
            method="POST",
        )
        return self._request(request, timeout, before_attempt)

    @staticmethod
    def _headers(headers):
        return {"Accept": "application/json", "User-Agent": "JobSearch/1.0", **(headers or {})}

    def _request(self, request, timeout, before_attempt):
        for attempt in range(1, self.max_attempts + 1):
            delay = min(2 ** (attempt - 1), 4)
            error = None
            if before_attempt is not None:
                before_attempt()
            try:
                with self.opener(request, timeout=min(max(float(timeout), 1), 30)) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                delay = 10 if exc.code == 429 else delay
                code = "credentials_rejected" if exc.code in {401, 403} else f"upstream_http_{exc.code}"
                message = "Источник отклонил данные доступа." if exc.code in {401, 403} else "Источник временно недоступен." if retryable else "Источник отклонил запрос."
                error = SourceCollectionError(code, message, retryable=retryable)
            except (UnicodeDecodeError, json.JSONDecodeError):
                error = SourceCollectionError("invalid_json", "Источник вернул некорректные данные.")
            except (HTTPException, URLError, OSError, TimeoutError, ValueError):
                error = SourceCollectionError("upstream_network", "Не удалось связаться с источником.", retryable=True)
            if not error.retryable or attempt == self.max_attempts:
                raise error from None
            self.sleeper(delay)
        raise SourceCollectionError("upstream_network", "Не удалось связаться с источником.")
