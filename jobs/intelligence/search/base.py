from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    published_at: str | None = None


class SearchProviderError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class SearchProviderUnavailable(SearchProviderError):
    pass


class SearchProvider(Protocol):
    def search(self, query: str, *, max_results: int = 5, deadline=None, clock=None) -> list[SearchHit]: ...
