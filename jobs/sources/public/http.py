import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from jobs.sources.core.contracts import SourceCollectionError


class JsonHttpClient:
    def __init__(self, *, opener=urlopen, sleeper=time.sleep, max_attempts=3):
        self.opener = opener
        self.sleeper = sleeper
        self.max_attempts = max(1, min(int(max_attempts), 3))

    def get_json(self, url, *, params=None, timeout=15):
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "JobSearch/1.0"})
        for attempt in range(1, self.max_attempts + 1):
            retry_delay = min(2 ** (attempt - 1), 4)
            try:
                with self.opener(request, timeout=timeout) as response:
                    if getattr(response, "status", 200) >= 400:
                        raise SourceCollectionError("upstream_http", "Источник вернул ошибку HTTP.", retryable=True)
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if exc.code == 429:
                    retry_delay = 60
                error = SourceCollectionError(f"upstream_http_{exc.code}", "Источник временно недоступен." if retryable else "Источник отклонил запрос.", retryable=retryable)
            except (URLError, OSError, TimeoutError):
                error = SourceCollectionError("upstream_network", "Не удалось связаться с источником.", retryable=True)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise SourceCollectionError("invalid_json", "Источник вернул некорректные данные.")
            except SourceCollectionError as exc:
                error = exc
            if not error.retryable or attempt == self.max_attempts:
                raise error
            self.sleeper(retry_delay)
        raise SourceCollectionError("upstream_network", "Не удалось связаться с источником.")
