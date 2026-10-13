"""Active job postings from NAV's official public Job Vacancy Feed (pam-stilling-feed.nav.no).

The feed is free for anyone (terms: https://navikt.github.io/pam-stilling-feed/); a rotating public token is
read at run time from NAV's own publicToken endpoint, so no personal credential is involved. Once per run the
agent walks the feed pages changed in the last N days, keeps the latest state of every ad and the ACTIVE ones,
pre-matches ads to batch companies by employer name, and then fetches each candidate's feed entry. An ad is
published only when the entry's employer organisation number equals the entity or one of its registered
subunits. FINN-only ads are not part of the feed.
"""
from __future__ import annotations

import asyncio
import re
import time
from email.utils import formatdate
from typing import Any

from .claims import Collector
from .fetch import Fetcher, Response
from .textutil import distinctive_tokens, fold, name_tokens

BASE = "https://pam-stilling-feed.nav.no"
TOKEN_URL = BASE + "/api/publicToken"
SOURCE = "public_job_board"
ACT = "hiring_activity"
TERMS = "NAV Job Vacancy Feed, free public API (https://navikt.github.io/pam-stilling-feed/)"


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", fold(text)))


def _name_matches(candidate: str, tokens: list[str]) -> bool:
    if not tokens:
        return False
    cand = _tokens(candidate)
    compact = re.sub(r"[^a-z0-9]", "", fold(candidate))
    return all(t in cand or (len(t) >= 5 and t in compact) for t in tokens)


class NavFeed:
    def __init__(self, fetcher: Fetcher, *, days: int = 60):
        self.fetcher = fetcher
        self.days = days
        self.token: str | None = None
        self.active: dict[str, dict[str, Any]] = {}
        self.by_token: dict[str, set[str]] = {}
        self.state = "not_applicable"
        self.note: str | None = None
        self.pages = 0
        self.window_start: str | None = None
        self.walk_response: Response | None = None

    async def prefetch(self, deadline: float) -> None:
        token_response = await self.fetcher.get(TOKEN_URL, accept="text/plain", store=False)
        match = re.search(r"eyJ[A-Za-z0-9._-]+", token_response.text()) if token_response.status == 200 else None
        if not match:
            self.state, self.note = "failed", f"public token unavailable ({token_response.error or token_response.status})"
            return
        self.token = match.group(0)
        latest: dict[str, dict[str, Any]] = {}
        since = time.time() - self.days * 86400
        self.window_start = formatdate(since, usegmt=True)
        path = "/api/v1/feed"
        headers = {"Authorization": f"Bearer {self.token}", "If-Modified-Since": self.window_start}
        complete = False
        while path and time.monotonic() < deadline:
            response = await self.fetcher.get(BASE + path, accept="application/json", store=False, retries=2, headers=headers)
            if response.status != 200:
                self.note = f"feed page failed: {response.error or response.status}"
                break
            try:
                body = response.json()
            except ValueError:
                self.note = "feed page was not JSON"
                break
            self.pages += 1
            self.walk_response = response
            items = body.get("items") or []
            for item in items:
                entry = item.get("_feed_entry") or {}
                if entry.get("uuid"):
                    latest[entry["uuid"]] = {**entry, "url": item.get("url"), "date_modified": item.get("date_modified")}
            headers = {"Authorization": f"Bearer {self.token}"}
            path = body.get("next_url")
            if not path or not items:
                complete = True
                break
        self.active = {uuid: e for uuid, e in latest.items() if e.get("status") == "ACTIVE" and e.get("businessName")}
        for uuid, entry in self.active.items():
            for token in _tokens(entry["businessName"]):
                if len(token) >= 3:
                    self.by_token.setdefault(token, set()).add(uuid)
        if self.pages == 0:
            self.state = "failed"
        else:
            self.state = "available"
            if not complete:
                self.note = (self.note or "feed walk stopped by the run time budget") + f"; {self.pages} pages read"

    def _candidates(self, names: list[str]) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name in names:
            tokens = distinctive_tokens(name)
            if not tokens or len("".join(name_tokens(name))) < 4:
                continue
            rare = min(tokens, key=lambda t: len(self.by_token.get(t, ())))
            for uuid in self.by_token.get(rare, ()):
                entry = self.active[uuid]
                if _name_matches(entry["businessName"], tokens):
                    out[uuid] = entry
        return sorted(out.values(), key=lambda e: str(e.get("date_modified") or ""), reverse=True)

    async def run(self, col: Collector, legal_name: str, brand: str | None, unit_orgs: set[str], *, max_verify: int = 15) -> list[dict[str, Any]]:
        if self.state != "available":
            col.module("nav_jobs", "failed" if self.state == "failed" else "not_applicable", note=self.note)
            return []
        names = [legal_name] + ([brand] if brand and fold(brand) != fold(legal_name) and len(fold(brand)) >= 4 else [])
        candidates = self._candidates(names)
        allowed = {col.organisation_number} | unit_orgs
        accepted: list[dict[str, Any]] = []
        unverifiable = 0
        results = await asyncio.gather(*(self._entry(c) for c in candidates[:max_verify]))
        for entry, (response, body) in zip(candidates, results):
            ad = (body or {}).get("ad_content") or {}
            employer = ad.get("employer") or {}
            orgnr = re.sub(r"\D", "", str(employer.get("orgnr") or ""))
            if (body or {}).get("status") not in (None, "ACTIVE") or not ad:
                continue
            if not orgnr:
                unverifiable += 1
                col.candidates.append({"type": "job", "url": ad.get("link"), "verdict": "employer_orgnr_missing_not_published"})
                continue
            if orgnr not in allowed:
                col.candidates.append({"type": "job", "url": ad.get("link"), "verdict": "employer_orgnr_mismatch", "employer_orgnr": orgnr})
                continue
            location = (ad.get("workLocations") or [{}])[0] or {}
            posting = {
                "title": ad.get("title"),
                "published": str(ad.get("published") or "")[:10] or None,
                "expires": str(ad.get("expires") or "")[:10] or None,
                "employer_name": employer.get("name"),
                "employer_unit_orgnr": orgnr,
                "location": ", ".join(x for x in [location.get("city"), location.get("municipal")] if x) or None,
                "occupation": ((ad.get("occupationCategories") or [{}])[0] or {}).get("level2"),
                "engagement": ad.get("engagementtype"),
                "positions": ad.get("positioncount"),
                "url": ad.get("link") or f"https://arbeidsplassen.nav.no/stillinger/stilling/{entry['uuid']}",
            }
            ev = col.add_evidence(response, source_class=SOURCE, method="nav_feed_entry_employer_orgnr_v1",
                                  locator="json:/ad_content/employer/orgnr", span=f"employer {employer.get('name')} orgnr {orgnr}: {posting['title']}",
                                  access_basis=TERMS)
            col.add(ACT, "job_posting", {k: v for k, v in posting.items() if v}, source_class=SOURCE, evidence_ids=[ev],
                    key_suffix=f"nav.{entry['uuid']}", effective=posting["published"],
                    identity="employer organisation number on the NAV ad equals the entity" + (" (registered subunit)" if orgnr != col.organisation_number else ""))
            accepted.append(posting)
        if accepted:
            col.add(ACT, "active_job_postings_count", len(accepted), source_class=SOURCE,
                    evidence_ids=sorted({eid for c in col.values("hiring_activity.job_posting.nav") for eid in c.evidence_ids}),
                    note="Active ads in NAV's public job feed verified by employer organisation number at retrieval time")
            col.module("nav_jobs", "available", active_postings=len(accepted), feed_pages=self.pages)
        else:
            ev = col.add_evidence(self.walk_response, source_class=SOURCE, method="nav_feed_walk_v1", url=BASE + "/api/v1/feed",
                                  span=f"{len(self.active)} active ads in feed pages modified since {self.window_start}; none matched this employer",
                                  access_basis=TERMS)
            col.absent(ACT, "job_posting", "not_available", source_class=SOURCE, evidence_ids=[ev],
                       note=f"No active ads with this employer organisation number in NAV's public job feed (ads changed in the last {self.days} days)"
                       + (f"; {unverifiable} name-matched ad(s) lacked an employer number and were not attributed" if unverifiable else ""))
            col.module("nav_jobs", "not_available", feed_pages=self.pages, name_matches=len(candidates))
        return accepted

    async def _entry(self, entry: dict[str, Any]) -> tuple[Response, Any]:
        path = entry.get("url") or f"/api/v1/feedentry/{entry['uuid']}"
        return await self.fetcher.get_json(BASE + path, headers={"Authorization": f"Bearer {self.token}"})
