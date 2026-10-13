"""Optional, declared search-API candidate discovery (disabled unless a key is supplied by the evaluator).

Set BRAVE_SEARCH_API_KEY to enable. Search results are candidates only: raw titles, snippets and ranks are
kept in memory and discarded; only candidate URLs that later pass the website identity gate (fetched
independently, organisation number / phone / e-mail / address proof) are published. Directories and
social networks are never accepted as the official website.
"""
from __future__ import annotations

import os
import urllib.parse

from .fetch import Fetcher

BRAVE = "https://api.search.brave.com/res/v1/web/search?q={q}&country=no&count=10&safesearch=strict"


class SearchCandidates:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        self.key = os.environ.get("BRAVE_SEARCH_API_KEY") or None
        self.calls = 0
        self.max_calls = int(os.environ.get("SIGNALPOST_SEARCH_MAX_CALLS", "1500"))

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    async def website_candidates(self, legal_name: str, municipality: str | None) -> list[str]:
        if not self.key or self.calls >= self.max_calls:
            return []
        self.calls += 1
        query = f'"{legal_name}"' + (f" {municipality.title()}" if municipality else "")
        response = await self.fetcher.get(BRAVE.format(q=urllib.parse.quote(query)), accept="application/json", respect_robots=False,
                                          headers={"X-Subscription-Token": self.key}, store=False)
        if response.status != 200:
            return []
        try:
            body = response.json()
        except ValueError:
            return []
        urls = []
        for result in (body.get("web") or {}).get("results") or []:
            url = result.get("url")
            if isinstance(url, str) and url.startswith("http"):
                urls.append(url)
        return urls[:6]
