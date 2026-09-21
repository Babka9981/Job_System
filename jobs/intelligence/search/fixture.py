from collections import deque

from .base import SearchHit


class FixtureSearchProvider:
    """Explicit test transport; it is never selected as the production default."""

    is_mock = True
    provider_name = "fixture"

    def __init__(self, responses):
        self._responses = deque(responses)
        self.queries = []

    def search(self, query: str, *, max_results: int = 5, deadline=None, clock=None) -> list[SearchHit]:
        if deadline is not None and clock is not None and clock() >= deadline:
            return []
        self.queries.append(query)
        response = self._responses.popleft() if self._responses else []
        if isinstance(response, Exception):
            raise response
        return list(response)[: min(max(1, int(max_results)), 5)]
