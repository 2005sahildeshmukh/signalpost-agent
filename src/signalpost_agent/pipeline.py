"""Batch orchestration: one terminal envelope per input, always."""
from __future__ import annotations

import asyncio
import gzip
import csv
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import AGENT_NAME, AGENT_VERSION
from .claims import AVAILABILITY, SECTIONS, Collector
from .fetch import COUNTERS, Fetcher, SnapshotStore, utc_now
from .jobs import NavFeed
from .refresh import RefreshStore
from .registry import Registry
from .search import SearchCandidates
from .synthesis import build_summary, financial_series
from .textutil import fold, norwegian_phone, org_checksum_ok
from .website import IdentityInfo, Website, registered_domain
from .wikidata import Wikidata

SCHEMA_VERSION = "signalpost.envelope.v1"
SECTION_MODULES = {
    "legal_identity": ("registry_entity",),
    "financials": ("annual_accounts", "annual_account_copies"),
    "leadership_workplaces": ("registry_roles", "registry_workplaces", "registry_group"),
    "web_presence": ("website", "website_identity", "wikidata"),
    "hiring_activity": ("nav_jobs", "company_site_activity", "registry_updates"),
}
FIELD_FAMILIES = {
    "legal_identity": "legal_identity.legal_name",
    "public_brand": "legal_identity.public_brand",
    "annual_accounts": "financials.accounts_basis",
    "account_history": "financials.filed_account_years",
    "leadership": "leadership_workplaces.role",
    "registered_workplaces": "leadership_workplaces.registered_workplace",
    "official_website": "web_presence.official_website",
    "social_profiles": "web_presence.social_profile",
    "contact_details": "web_presence.contact_",
    "job_postings": "hiring_activity.job_posting",
    "careers_channel": "hiring_activity.careers_page",
    "company_news": "hiring_activity.company_news",
    "registry_activity": "hiring_activity.registry_update",
}
FAMILY_MODULE = {
    "legal_identity": "registry_entity", "public_brand": "website_identity", "annual_accounts": "annual_accounts",
    "account_history": "annual_account_copies", "leadership": "registry_roles", "registered_workplaces": "registry_workplaces",
    "official_website": "website", "social_profiles": "website_identity", "contact_details": "website_identity",
    "job_postings": "nav_jobs", "careers_channel": "website_identity", "company_news": "company_site_activity",
    "registry_activity": "registry_updates",
}


@dataclass
class RunConfig:
    run_id: str
    output_dir: Path
    state_dir: Path
    concurrency: int = 24
    per_company_timeout: float = 150.0
    time_budget: float = 3300.0
    guess_domains: bool = True
    filing_years: bool = True
    jobs: bool = True
    website: bool = True
    wikidata: bool = True
    registry_snapshot: Path | None = None
    expected_count: int | None = None


@dataclass
class InputItem:
    raw: str
    organisation_number: str | None
    error: str | None = None


def read_inputs(path: Path) -> list[InputItem]:
    text = path.read_bytes()
    if path.suffix == ".gz":
        text = gzip.decompress(text)
    content = text.decode("utf-8-sig")
    values: list[Any] = []
    stripped = content.strip()
    if path.suffix in (".json",) or stripped.startswith("["):
        body = json.loads(stripped or "[]")
        values = body if isinstance(body, list) else body.get("organisation_numbers") or body.get("companies") or []
    elif path.suffix == ".csv":
        rows = list(csv.DictReader(content.splitlines()))
        for row in rows:
            values.append(next((row[k] for k in row if k and fold(k).replace("_", "") in ("organisationnumber", "orgnr", "organisasjonsnummer", "org")), None) or next(iter(row.values()), ""))
    else:
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                try:
                    values.append(json.loads(line))
                    continue
                except ValueError:
                    pass
            values.append(line)
    items: list[InputItem] = []
    seen: set[str] = set()
    for value in values:
        raw = value
        if isinstance(value, dict):
            raw = next((value[k] for k in ("organisation_number", "organisationNumber", "orgnr", "org_nr", "organisasjonsnummer", "org") if k in value), "")
        raw_text = str(raw or "").strip()
        digits = re.sub(r"\D", "", raw_text)
        if len(digits) != 9:
            items.append(InputItem(raw_text, None, "not a nine-digit Norwegian organisation number"))
            continue
        if digits in seen:
            continue  # exactly one terminal envelope per organisation number
        seen.add(digits)
        items.append(InputItem(raw_text, digits, None if org_checksum_ok(digits) else "organisation number fails the mod-11 checksum"))
    return items


def load_registry_snapshot(path: Path | None, wanted: set[str]) -> dict[str, dict[str, Any]]:
    """Optional frozen registry snapshot (Builderr universe JSONL(.gz) or Brreg bulk CSV) used as identity fallback."""
    if not path or not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        if ".csv" in path.name:
            for row in csv.DictReader(handle):
                org = row.get("organisasjonsnummer") or row.get("Organisasjonsnummer")
                if org in wanted:
                    rows[org] = {"name": row.get("navn"), "legal_form": row.get("organisasjonsform.kode"),
                                 "municipality": row.get("forretningsadresse.kommune"), "website": row.get("hjemmeside"),
                                 "industry_code": row.get("naeringskode1.kode"), "industry_label": row.get("naeringskode1.beskrivelse")}
        else:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("organisation_number") in wanted:
                    rows[row["organisation_number"]] = row
    return rows


class Agent:
    def __init__(self, config: RunConfig):
        self.config = config
        self.snapshots = SnapshotStore(config.output_dir / "snapshots")
        self.fetcher = Fetcher(self.snapshots)
        self.registry = Registry(self.fetcher)
        self.search = SearchCandidates(self.fetcher)
        self.website = Website(self.fetcher, guess_domains=config.guess_domains, search=self.search)
        self.jobs = NavFeed(self.fetcher)
        self.wikidata = Wikidata(self.fetcher)
        self.refresh = RefreshStore(config.state_dir)
        self.started = time.monotonic()
        self.deadline = self.started + config.time_budget
        self.metrics: dict[str, dict[str, Any]] = {}

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    # ------------------------------------------------------------------ one company
    async def research(self, col: Collector, bulk_row: dict[str, Any] | None) -> None:
        org = col.organisation_number
        registry = await self.registry.run_all(col, bulk_row)
        entity = registry["entity"]
        col.context["registry"] = registry
        if not entity:
            for module in ("website", "nav_jobs"):
                col.module(module, "not_applicable", note="entity could not be resolved in the official register")
            return
        legal_name = entity.get("navn") or ""
        wiki = self.wikidata.apply(col) if self.config.wikidata else {}
        if not self.config.website:
            col.module("website", "not_applicable", note="website module disabled for this run")
        else:
            addresses = [entity.get("forretningsadresse") or {}, entity.get("postadresse") or {}]
            street_tokens = set()
            for address in addresses:
                for line in address.get("adresse") or []:
                    street_tokens.update(t for t in re.findall(r"[a-z]+", fold(line)) if len(t) >= 5 and t not in ("postboks", "gate", "veien", "vegen", "gata"))
            phones = {p for p in (norwegian_phone(entity.get("telefon")), norwegian_phone(entity.get("mobil"))) if p}
            email = entity.get("epostadresse")
            info = IdentityInfo(
                org=org, legal_name=legal_name,
                former_names=[n.get("navn") for n in entity.get("historiskeNavn") or [] if n.get("navn")],
                phones=phones,
                email_domains={registered_domain(email.split("@")[-1])} if email and "@" in email else set(),
                postal_codes={a.get("postnummer") for a in addresses if a.get("postnummer")},
                street_tokens=street_tokens,
                municipality=(entity.get("forretningsadresse") or {}).get("kommune"),
                person_names=[r["name"] for r in registry["roles"] if r.get("kind") == "person" and len(str(r.get("name", "")).split()) >= 2],
            )
            employees = entity.get("antallAnsatte") if entity.get("harRegistrertAntallAnsatte") else None
            crawl, verdict = await self.website.discover(
                col, info, registry_site=entity.get("hjemmeside"), wikidata_sites=self.wikidata.websites(org) if self.config.wikidata else [],
                registry_email=email, subunit_sites=[u.get("website") for u in registry["subunits"] if u.get("website")], employees=employees)
            if crawl and verdict:
                summary = self.website.extract(col, crawl, verdict, info)
                col.context["website"] = summary
                col.module("website", "available", url=summary["url"], identity=verdict["status"], origin=crawl.origin)
                if verdict["publishable"]:
                    await self.website.activity(col, crawl)
                else:
                    col.module("company_site_activity", "ambiguous", note="site identity not strong enough to attribute site content")
            else:
                verdicts = {c.get("verdict") for c in col.candidates if c.get("type") == "website"}
                if verdicts & {"name_only", "conflict"}:
                    state, note = "ambiguous", "candidate sites found but none could be tied to this exact entity"
                elif verdicts & {"blocked"}:
                    state, note = "blocked", "candidate site disallows automated access (robots.txt)"
                else:
                    state, note = "not_available", "no verifiable official website found (registry, Wikidata, e-mail domain, subunits, name-derived domains checked)"
                col.absent("web_presence", "official_website", state, source_class="company_owned", note=note)
                col.module("website", state, note=note, candidates_checked=len(verdicts))
                col.module("company_site_activity", "not_applicable" if state == "not_available" else state, note="requires a verified website")
                if not wiki:
                    col.absent("web_presence", "social_profile", "not_available" if state == "not_available" else state,
                               source_class="company_owned", note="no verified website or Wikidata item to anchor company-owned profiles")
        col.context["jobs_input"] = {
            "legal_name": legal_name,
            "brand": (col.context.get("website") or {}).get("brand"),
            "unit_orgs": {u["organisation_number"] for u in registry["subunits"] if u.get("organisation_number")},
        }

    async def match_jobs(self, collectors: list[Collector]) -> None:
        semaphore = asyncio.Semaphore(8)

        async def one(col: Collector) -> None:
            data = col.context.get("jobs_input")
            if not data:
                col.module_states.setdefault("nav_jobs", {"state": "not_applicable", "checked_at": utc_now(), "note": "entity not resolved"})
                return
            async with semaphore:
                counters: dict[str, int] = {}
                token = COUNTERS.set(counters)
                try:
                    await asyncio.wait_for(self.jobs.run(col, data["legal_name"], data["brand"], data["unit_orgs"]), timeout=60)
                except Exception as exc:
                    col.module("nav_jobs", "failed", note=f"{type(exc).__name__}")
                finally:
                    COUNTERS.reset(token)
                metric = self.metrics.setdefault(col.organisation_number, {"requests": 0, "bytes": 0, "runtime_ms": 0})
                metric["requests"] += counters.get("requests", 0)
                metric["bytes"] += counters.get("bytes", 0)

        await asyncio.gather(*(one(col) for col in collectors))

    async def research_guarded(self, item: InputItem, bulk: dict[str, dict[str, Any]], semaphore: asyncio.Semaphore) -> Collector:
        org = item.organisation_number or item.raw
        col = Collector(org)
        async with semaphore:
            counters: dict[str, int] = {}
            token = COUNTERS.set(counters)
            started = time.monotonic()
            col.context["started_at"] = utc_now()
            try:
                if item.organisation_number is None:
                    col.module("input", "failed", note=item.error)
                    col.error("input", item.error or "invalid input")
                elif self.remaining() < 5:
                    col.module("registry_entity", "failed", note="run time budget exhausted before this company started")
                    col.error("pipeline", "time budget exhausted before start")
                else:
                    timeout = max(5.0, min(self.config.per_company_timeout, self.remaining() - 20))
                    if item.error:
                        col.error("input", item.error)
                    await asyncio.wait_for(self.research(col, bulk.get(item.organisation_number)), timeout=timeout)
            except asyncio.TimeoutError:
                col.error("pipeline", f"company research hit its time limit; partial results kept")
                for module in ("registry_entity", "website", "company_site_activity"):
                    col.module_states.setdefault(module, {"state": "failed", "checked_at": utc_now(), "note": "time limit reached before this module finished"})
            except Exception as exc:  # never drop a company
                col.error("pipeline", f"{type(exc).__name__}: {exc}")
                for module in ("registry_entity", "website"):
                    col.module_states.setdefault(module, {"state": "failed", "checked_at": utc_now(), "note": f"{type(exc).__name__}"})
            finally:
                COUNTERS.reset(token)
            col.context["completed_at"] = utc_now()
            self.metrics[org] = {"requests": counters.get("requests", 0), "bytes": counters.get("bytes", 0),
                                 "runtime_ms": int((time.monotonic() - started) * 1000)}
        return col

    async def filing_lane(self, collectors: list[Collector], done: asyncio.Event) -> None:
        """Background lane for the ~30 requests/minute filing-copy endpoint."""
        for col in collectors:
            if col.module_states.get("registry_entity", {}).get("state") != "available":
                continue
            if self.remaining() < 40:
                break
            counters: dict[str, int] = {}
            token = COUNTERS.set(counters)
            try:
                await asyncio.wait_for(self.registry.filing_years(col), timeout=30)
            except Exception as exc:
                col.module("annual_account_copies", "failed", note=f"{type(exc).__name__}")
            finally:
                COUNTERS.reset(token)
            metric = self.metrics.setdefault(col.organisation_number, {"requests": 0, "bytes": 0, "runtime_ms": 0})
            metric["requests"] += counters.get("requests", 0)
            metric["bytes"] += counters.get("bytes", 0)

    # ------------------------------------------------------------------ batch
    async def run(self, items: list[InputItem]) -> dict[str, Any]:
        started_at = utc_now()
        valid = [i.organisation_number for i in items if i.organisation_number]
        bulk = load_registry_snapshot(self.config.registry_snapshot, set(valid))
        if self.config.wikidata and valid:
            try:
                await asyncio.wait_for(self.wikidata.prefetch(valid), timeout=min(120, max(10, self.remaining() / 10)))
            except asyncio.TimeoutError:
                self.wikidata.state = "failed"
        feed_task = asyncio.create_task(self.jobs.prefetch(self.deadline - 120)) if self.config.jobs else None
        semaphore = asyncio.Semaphore(self.config.concurrency)
        collectors_by_index: dict[int, Collector] = {}
        lane_queue: list[Collector] = []
        lane_done = asyncio.Event()

        async def worker(index: int, item: InputItem) -> None:
            col = await self.research_guarded(item, bulk, semaphore)
            collectors_by_index[index] = col
            lane_queue.append(col)

        tasks = [asyncio.create_task(worker(i, item)) for i, item in enumerate(items)]
        lane_task = None
        if self.config.filing_years:
            async def lane() -> None:
                processed = 0
                while not lane_done.is_set() or processed < len(lane_queue):
                    if processed < len(lane_queue):
                        await self.filing_lane([lane_queue[processed]], lane_done)
                        processed += 1
                    else:
                        await asyncio.sleep(0.2)
                    if self.remaining() < 40:
                        break
            lane_task = asyncio.create_task(lane())
        await asyncio.gather(*tasks)
        lane_done.set()
        collectors = [collectors_by_index[i] for i in range(len(items))]
        if feed_task:
            try:
                await asyncio.wait_for(asyncio.shield(feed_task), timeout=max(1.0, self.remaining() - 90))
            except asyncio.TimeoutError:
                pass
            await self.match_jobs(collectors)
        else:
            for col in collectors:
                col.module("nav_jobs", "not_applicable", note="jobs module disabled for this run")
        if lane_task:
            try:
                await asyncio.wait_for(lane_task, timeout=max(1.0, self.remaining() - 30))
            except asyncio.TimeoutError:
                pass
        for col in collectors:
            if self.config.filing_years and "annual_account_copies" not in col.module_states and col.module_states.get("registry_entity", {}).get("state") == "available":
                col.module("annual_account_copies", "failed", note="rate-limited filing-copy endpoint not reached within the run time budget")
        self.batch_guards(collectors)
        completed_at = utc_now()
        envelopes = [self.envelope(col, started_at, completed_at) for col in collectors]
        await self.fetcher.close()
        return {"envelopes": envelopes, "started_at": started_at, "completed_at": completed_at, "items": items}

    def batch_guards(self, collectors: list[Collector]) -> None:
        """Cross-company precision guard: a site or social profile claimed by several entities is not exact."""
        social_owners: dict[str, set[str]] = {}
        domain_owners: dict[str, set[str]] = {}
        for col in collectors:
            for claim in col.values("web_presence.social_profile"):
                social_owners.setdefault(claim.value["url"].lower(), set()).add(col.organisation_number)
            for claim in col.values("web_presence.official_website"):
                domain_owners.setdefault(registered_domain(str(claim.value)), set()).add(col.organisation_number)
        for col in collectors:
            for claim in col.values("web_presence.social_profile"):
                if len(social_owners[claim.value["url"].lower()]) > 1:
                    claim.availability = "ambiguous"
                    claim.note = "the same profile is linked from sites of several entities in this batch (agency, parent or shared brand); not attributed"
            website = col.values("web_presence.official_website")
            if website and len(domain_owners[registered_domain(str(website[0].value))]) > 1:
                signals = col.module_states.get("website_identity", {})
                if signals.get("score", 0) < 1.0:
                    for claim in col.values("web_presence.") + col.values("hiring_activity.company_news") + col.values("hiring_activity.careers_page"):
                        if claim.key.startswith(("web_presence.wikidata", "web_presence.knowledge_base")):
                            continue
                        claim.availability = "ambiguous"
                        claim.note = "website is shared with another entity in this batch and the site does not show this organisation number"

    # ------------------------------------------------------------------ envelope
    def envelope(self, col: Collector, started_at: str, completed_at: str) -> dict[str, Any]:
        now = col.context.get("completed_at") or completed_at
        changes, refresh_meta = self.refresh.apply(col, self.config.run_id, now) if re.fullmatch(r"\d{9}", col.organisation_number) else ([], {})
        summary = build_summary(col, changes if not refresh_meta.get("first_run", True) else None)
        sections = {}
        for section, title in SECTIONS.items():
            claims = [c for c in col.claims.values() if c.section == section]
            available = [c for c in claims if c.availability == "available"]
            states = [col.module_states.get(m, {}).get("state") for m in SECTION_MODULES[section]]
            if available:
                state = "available"
            elif "failed" in states:
                state = "failed"
            elif "blocked" in states:
                state = "blocked"
            elif "ambiguous" in states:
                state = "ambiguous"
            elif states and all(s in (None, "not_applicable") for s in states):
                state = "not_applicable"
            else:
                state = "not_available"
            sections[section] = {"title": title, "availability": state, "claims": len(available),
                                 "unavailable_fields": sorted({c.field for c in claims if c.availability != "available"})}
        families = {}
        for family, prefix in FIELD_FAMILIES.items():
            hits = [c for c in col.claims.values() if c.key.startswith(prefix) and c.availability == "available"]
            if hits:
                families[family] = {"state": "available", "facts": len(hits)}
            else:
                explicit = next((c for c in col.claims.values() if c.key.startswith(prefix) and c.availability != "available"), None)
                module_state = col.module_states.get(FAMILY_MODULE[family], {}).get("state")
                state = explicit.availability if explicit else (module_state if module_state and module_state != "available" else "not_available")
                families[family] = {"state": state, "facts": 0, "reason": (explicit.note if explicit else col.module_states.get(FAMILY_MODULE[family], {}).get("note"))}
        claims = sorted(col.claims.values(), key=lambda c: (list(SECTIONS).index(c.section) if c.section in SECTIONS else 9, c.key))
        used_evidence = {eid for c in claims for eid in c.evidence_ids}
        evidence = [e.to_dict() for eid, e in sorted(col.evidence.items()) if eid in used_evidence]
        name = next((c.value for c in claims if c.key == "legal_identity.legal_name" and c.availability == "available"), None)
        terminal = "completed" if col.module_states.get("registry_entity", {}).get("state") == "available" else (
            "completed" if col.module_states.get("registry_entity", {}).get("state") == "not_available" else "failed")
        metric = self.metrics.get(col.organisation_number, {})
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "organisation_number": col.organisation_number,
            "legal_name": name,
            "status": terminal,
            "run": {"run_id": self.config.run_id, "agent": AGENT_NAME, "agent_version": AGENT_VERSION,
                    "batch_started_at": started_at, "started_at": col.context.get("started_at"), "completed_at": now,
                    "terminal_status": terminal},
            "summary": summary,
            "profile": self.profile_view(col),
            "sections": sections,
            "field_families": families,
            "module_states": col.module_states,
            "claims": [c.to_dict() for c in claims],
            "evidence": evidence,
            "changes": changes,
            "refresh": {**refresh_meta, "change_count": len(changes), "material_change_count": sum(1 for c in changes if c["material"])},
            "identity_candidates": col.candidates[:40],
            "errors": col.errors,
            "operations": {"requests": metric.get("requests", 0), "bytes": metric.get("bytes", 0), "runtime_ms": metric.get("runtime_ms", 0),
                           "third_party_cost_usd": 0.0, "llm_calls": 0},
        }
        if re.fullmatch(r"\d{9}", col.organisation_number):
            self.refresh.save(envelope, self.config.run_id)
        return envelope

    def profile_view(self, col: Collector) -> dict[str, Any]:
        def one(prefix: str) -> Any:
            claims = col.values(prefix)
            return {"value": claims[0].value, "claim_id": claims[0].id} if claims else None

        def many(prefix: str, limit: int = 50) -> list[dict[str, Any]]:
            return [{"value": c.value, "claim_id": c.id, "effective_date": c.effective_date} for c in col.values(prefix)[:limit]]

        series = financial_series(col)
        financials = []
        for end, metrics in list(series.items())[:5]:
            row: dict[str, Any] = {"period_end": end, "claim_ids": {}}
            for metric, claim in metrics.items():
                row[metric] = claim.value["amount"]
                row["currency"] = claim.value.get("currency")
                row["period"] = claim.reporting_period
                row["claim_ids"][metric] = claim.id
            financials.append(row)
        return {
            "legal_name": one("legal_identity.legal_name"), "public_brand": one("legal_identity.public_brand"),
            "legal_form": one("legal_identity.legal_form"), "status": one("legal_identity.status"),
            "founded_date": one("legal_identity.founded_date"), "registered_date": one("legal_identity.registered_date"),
            "municipality": one("legal_identity.municipality"), "business_address": one("legal_identity.business_address"),
            "industry": one("legal_identity.industry.1"), "registered_employees": one("legal_identity.registered_employees"),
            "phone": one("legal_identity.phone"), "share_capital": one("legal_identity.share_capital"),
            "official_website": one("web_presence.official_website"), "site_description": one("web_presence.site_description"),
            "social_profiles": many("web_presence.social_profile"), "contact_emails": many("web_presence.contact_email"),
            "contact_phones": many("web_presence.contact_phone"), "wikidata": one("web_presence.wikidata_item"),
            "financials": financials, "filed_account_years": one("financials.filed_account_years"),
            "leadership": many("leadership_workplaces.role"), "workplaces": many("leadership_workplaces.registered_workplace"),
            "group_parent": one("leadership_workplaces.group_parent"), "subsidiaries": many("leadership_workplaces.group_subsidiary", 20),
            "job_postings": many("hiring_activity.job_posting"), "company_news": many("hiring_activity.company_news"),
            "careers_page": one("hiring_activity.careers_page"), "registry_updates": many("hiring_activity.registry_update", 10),
            "filings": many("hiring_activity.annual_accounts_filed", 5),
        }


def run_report(result: dict[str, Any], config: RunConfig, agent: Agent, wall_seconds: float) -> dict[str, Any]:
    envelopes = result["envelopes"]
    items: list[InputItem] = result["items"]
    orgs = [e["organisation_number"] for e in envelopes]
    states_ok = all(e["status"] in ("completed", "failed") for e in envelopes)
    module_ok = all(s.get("state") in AVAILABILITY for e in envelopes for s in e["module_states"].values())
    section_ok = all(s["availability"] in AVAILABILITY for e in envelopes for s in e["sections"].values())
    validation = {
        "one_envelope_per_input": len(envelopes) == len(items),
        "expected_count_matches": config.expected_count is None or len(envelopes) == config.expected_count,
        "unique_organisation_numbers": len(orgs) == len(set(orgs)),
        "terminal_status_valid": states_ok,
        "module_states_valid": module_ok,
        "section_states_valid": section_ok,
        "every_available_claim_has_evidence": all(c["evidence_ids"] for e in envelopes for c in e["claims"] if c["availability"] == "available"),
        "every_evidence_has_retrieval_time": all(ev.get("retrieved_at") for e in envelopes for ev in e["evidence"]),
    }
    coverage = {}
    for family in FIELD_FAMILIES:
        counter = Counter(e["field_families"][family]["state"] for e in envelopes)
        coverage[family] = {"companies_available": counter.get("available", 0), "facts": sum(e["field_families"][family]["facts"] for e in envelopes),
                            "states": dict(counter)}
    runtimes = sorted(e["operations"]["runtime_ms"] for e in envelopes)
    pct = lambda q: runtimes[min(len(runtimes) - 1, int(len(runtimes) * q))] if runtimes else None
    identity = Counter(e["module_states"].get("website_identity", {}).get("status") for e in envelopes if e["module_states"].get("website_identity"))
    origins = Counter(e["module_states"].get("website", {}).get("origin") for e in envelopes if e["module_states"].get("website", {}).get("state") == "available")
    return {
        "run_id": config.run_id,
        "agent": AGENT_NAME, "agent_version": AGENT_VERSION, "schema_version": SCHEMA_VERSION,
        "started_at": result["started_at"], "completed_at": result["completed_at"], "wall_clock_seconds": round(wall_seconds, 1),
        "inputs": len(items), "invalid_inputs": [i.raw for i in items if i.organisation_number is None],
        "emitted_envelopes": len(envelopes),
        "terminal_status_counts": dict(Counter(e["status"] for e in envelopes)),
        "validation": {"passed": all(validation.values()), "checks": validation},
        "coverage": coverage,
        "website_identity_status": dict(identity), "website_discovery_origin": dict(origins),
        "claims_published": sum(1 for e in envelopes for c in e["claims"] if c["availability"] == "available"),
        "changes_detected": sum(len(e["changes"]) for e in envelopes),
        "material_changes": sum(e["refresh"].get("material_change_count", 0) for e in envelopes),
        "operations": {"requests": agent.fetcher.requests, "bytes": agent.fetcher.bytes, "p50_company_ms": pct(0.5), "p95_company_ms": pct(0.95),
                       "requests_by_host_top": dict(Counter(agent.fetcher.by_host).most_common(12)),
                       "third_party_cost_usd": round(agent.search.calls * 0.005, 3), "search_api_calls": agent.search.calls, "llm_calls": 0},
        "config": {"search_api_enabled": agent.search.enabled, "concurrency": config.concurrency, "per_company_timeout": config.per_company_timeout, "time_budget": config.time_budget,
                   "guess_domains": config.guess_domains, "filing_years": config.filing_years, "jobs": config.jobs, "website": config.website,
                   "wikidata": config.wikidata, "registry_snapshot": str(config.registry_snapshot) if config.registry_snapshot else None},
    }
