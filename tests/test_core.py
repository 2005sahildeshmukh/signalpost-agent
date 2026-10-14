"""Offline unit tests: identity rules, normalisation, claim model, refresh diffs and envelope contract."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from signalpost_agent.claims import Collector
from signalpost_agent.fetch import Response
from signalpost_agent.pipeline import read_inputs
from signalpost_agent.refresh import RefreshStore
from signalpost_agent.synthesis import build_summary, money
from signalpost_agent.textutil import (domain_label_variants, norwegian_phone, org_checksum_ok, org_numbers_in, phones_in)
from signalpost_agent.website import (IdentityInfo, Page, SiteCrawl, Website, find_text_date, normalize_homepage,
                                      normalize_social_url, url_date)


def response(url: str, body: str = "<html></html>") -> Response:
    return Response(url, url, 200, body.encode(), "text/html", "2026-10-14T00:00:00Z", 1, sha256="abc", snapshot_path="snapshots/ab/abc.gz")


def page(url: str, html: str, kind: str = "home") -> Page:
    soup = BeautifulSoup(html, "lxml")
    return Page(kind, url, response(url, html), soup, soup.get_text(" "), soup.title.get_text() if soup.title else "", [], {})


def info(**overrides) -> IdentityInfo:
    base = dict(org="810034882", legal_name="SANDNES ELEKTRISKE AS", former_names=[], phones={"51685700"}, email_domains=set(),
                postal_codes={"4307"}, street_tokens={"strandgata"}, municipality="SANDNES", person_names=["Morten Aarrestad"])
    base.update(overrides)
    return IdentityInfo(**base)


def test_org_checksum():
    assert org_checksum_ok("923609016")
    assert org_checksum_ok("810034882")
    assert not org_checksum_ok("923609017")
    assert not org_checksum_ok("12345678")


def test_org_numbers_in_text_with_context():
    found, contextual = org_numbers_in("Sandnes Elektriske AS · Org.nr: 810 034 882 MVA · tlf 51 68 57 00")
    assert "810034882" in found and "810034882" in contextual


def test_phone_normalisation():
    assert norwegian_phone("+47 51 68 57 00") == "51685700"
    assert norwegian_phone("0047 412 60 584") == "41260584"
    assert "51685700" in phones_in("Ring oss på 51 68 57 00 i dag")


def test_domain_label_variants():
    labels = domain_label_variants("SANDNES ELEKTRISKE AS")
    assert "sandneselektriske" in labels and "sandnes-elektriske" in labels
    assert "bjornstadbygg" in domain_label_variants("BJØRNSTAD BYGG AS")
    assert "bjoernstadbygg" in domain_label_variants("BJØRNSTAD BYGG AS")


def test_social_normalisation():
    assert normalize_social_url("https://www.facebook.com/sharer/sharer.php?u=x") is None
    assert normalize_social_url("https://no.linkedin.com/company/equinor/posts")["url"] == "https://linkedin.com/company/equinor"
    assert normalize_social_url("https://www.instagram.com/p/abc") is None
    assert normalize_social_url("https://twitter.com/equinor")["url"] == "https://x.com/equinor"
    assert normalize_social_url("https://www.youtube.com/@equinor")["platform"] == "youtube"


def test_homepage_normalisation():
    assert normalize_homepage("www.equinor.com") == "https://www.equinor.com/"
    assert normalize_homepage("post@firma.no") is None
    assert normalize_homepage("") is None


def test_text_dates():
    assert find_text_date("14 October 2026 | 08:15")[0].date().isoformat() == "2026-10-14"
    assert find_text_date("Publisert 3. mars 2025")[0].date().isoformat() == "2025-03-03"
    assert find_text_date("03.02.2026")[0].date().isoformat() == "2026-02-03"
    assert url_date("https://x.no/news/20261014-oil").date().isoformat() == "2026-10-14"


def test_identity_gate_org_number_is_exact():
    site = Website(fetcher=None)  # type: ignore[arg-type]
    crawl = SiteCrawl("https://sandneselektriske.no/", "name_derived_domain", final_domain="sandneselektriske.no")
    crawl.pages.append(page("https://sandneselektriske.no/", "<title>Sandnes Elektriske</title><body>Alt elektrisk på ett sted. Org.nr 810 034 882</body>"))
    verdict = site.assess(crawl, info())
    assert verdict["status"] == "exact" and verdict["publishable"]


def test_identity_gate_rejects_name_only_guess():
    site = Website(fetcher=None)  # type: ignore[arg-type]
    crawl = SiteCrawl("https://sandneselektriske.com/", "name_derived_domain", final_domain="sandneselektriske.com")
    crawl.pages.append(page("https://sandneselektriske.com/", "<title>Sandnes Elektriske</title><body>Welcome to Sandnes Elektriske, electrical goods since 1977 and more text here.</body>"))
    verdict = site.assess(crawl, info(phones=set(), postal_codes=set(), street_tokens=set(), person_names=[]))
    assert verdict["status"] == "name_only" and not verdict["publishable"]


def test_identity_gate_rejects_other_org_number():
    site = Website(fetcher=None)  # type: ignore[arg-type]
    crawl = SiteCrawl("https://example.no/", "name_derived_domain", final_domain="example.no")
    crawl.pages.append(page("https://example.no/", "<title>Sandnes Elektriske</title><body>Sandnes Elektriske AS org.nr. 923 609 016 lots of company text here for the check</body>"))
    verdict = site.assess(crawl, info(phones=set()))
    assert verdict["status"] == "conflict" and not verdict["publishable"]


def test_identity_gate_rejects_parked():
    site = Website(fetcher=None)  # type: ignore[arg-type]
    crawl = SiteCrawl("https://x.no/", "registry_declared", final_domain="x.no")
    crawl.pages.append(page("https://x.no/", "<title>x.no</title><body>This domain is parked free, courtesy of the registrar. Buy this domain today now.</body>"))
    assert site.assess(crawl, info())["status"] == "parked_or_placeholder"


def test_claims_never_store_missing_as_zero_and_ids_are_stable():
    first = Collector("810034882")
    assert first.add("financials", "revenue", None, source_class="official_annual_accounts", evidence_ids=["ev-1"]) is None
    claim = first.add("financials", "revenue", {"amount": 0.0, "currency": "NOK"}, source_class="official_annual_accounts", evidence_ids=["ev-1"], key_suffix="SELSKAP.2025-12-31")
    second = Collector("810034882")
    again = second.add("financials", "revenue", {"amount": 0.0, "currency": "NOK"}, source_class="official_annual_accounts", evidence_ids=["ev-1"], key_suffix="SELSKAP.2025-12-31")
    assert claim is not None and claim.id == again.id
    first.absent("hiring_activity", "job_posting", "not_available", source_class="public_job_board", note="none")
    absent = [c for c in first.claims.values() if c.field == "job_posting"][0]
    assert absent.value is None and absent.availability == "not_available"


def test_same_fact_from_two_sources_merges_evidence():
    col = Collector("923609016")
    col.add("web_presence", "social_profile", {"platform": "x", "url": "https://x.com/equinor", "handle": "equinor"}, source_class="company_owned", evidence_ids=["ev-a"], key_suffix="x.https://x.com/equinor")
    col.add("web_presence", "social_profile", {"platform": "x", "url": "https://x.com/Equinor", "handle": "Equinor"}, source_class="open_knowledge_base", evidence_ids=["ev-b"], key_suffix="x.https://x.com/equinor")
    profiles = col.values("web_presence.social_profile")
    assert len(profiles) == 1 and profiles[0].evidence_ids == ["ev-a", "ev-b"]


def _collector_with(name: str, revenue: float, ceo: str) -> Collector:
    col = Collector("810034882")
    ev = col.add_evidence(response("https://data.brreg.no/x"), source_class="official_registry", method="t", locator="json:/navn", span=name)
    col.add("legal_identity", "legal_name", name, source_class="official_registry", evidence_ids=[ev])
    col.add("financials", "revenue", {"amount": revenue, "currency": "NOK", "account_type": "SELSKAP"}, source_class="official_annual_accounts",
            evidence_ids=[ev], key_suffix="SELSKAP.2025-12-31", period="2025-01-01/2025-12-31")
    col.add("leadership_workplaces", "role", {"name": ceo, "role_code": "DAGL", "role": "Daglig leder"}, source_class="official_registry",
            evidence_ids=[ev], key_suffix=f"DAGL.{ceo}")
    for module in ("registry_entity", "annual_accounts", "registry_roles"):
        col.module(module, "available")
    return col


def test_refresh_detects_changes_and_is_idempotent(tmp_path: Path):
    store = RefreshStore(tmp_path)
    col = _collector_with("SANDNES ELEKTRISKE AS", 100.0, "Morten Aarrestad")
    changes, meta = store.apply(col, "r1", "2026-10-01T00:00:00Z")
    assert changes == [] and meta["first_run"]
    store.save({"organisation_number": "810034882", "run": {"run_id": "r1", "completed_at": "2026-10-01T00:00:00Z"},
                "claims": [c.to_dict() for c in col.claims.values()], "evidence": [e.to_dict() for e in col.evidence.values()]}, "r1")
    same = _collector_with("SANDNES ELEKTRISKE AS", 100.0, "Morten Aarrestad")
    changes, _ = store.apply(same, "r2", "2026-10-02T00:00:00Z")
    assert changes == []
    assert all(c.first_observed_at == "2026-10-01T00:00:00Z" for c in same.claims.values())
    changed = _collector_with("SANDNES ELEKTRISKE AS", 150.0, "Kari Nordmann")
    changes, _ = store.apply(changed, "r3", "2026-10-03T00:00:00Z")
    kinds = {c["change_type"] for c in changes}
    assert "new_role" in kinds and "removed_role" in kinds and "new_filing" in {c["change_type"] for c in changes if c["claim_key"].startswith("financials")} or "changed_value" in kinds
    assert all(c["old_evidence"] or c["change"] == "added" for c in changes)


def test_refresh_failed_module_keeps_last_value(tmp_path: Path):
    store = RefreshStore(tmp_path)
    col = _collector_with("SANDNES ELEKTRISKE AS", 100.0, "Morten Aarrestad")
    store.apply(col, "r1", "2026-10-01T00:00:00Z")
    store.save({"organisation_number": "810034882", "run": {"run_id": "r1", "completed_at": "2026-10-01T00:00:00Z"},
                "claims": [c.to_dict() for c in col.claims.values()], "evidence": [e.to_dict() for e in col.evidence.values()]}, "r1")
    failed = Collector("810034882")
    ev = failed.add_evidence(response("https://data.brreg.no/x"), source_class="official_registry", method="t")
    failed.add("legal_identity", "legal_name", "SANDNES ELEKTRISKE AS", source_class="official_registry", evidence_ids=[ev])
    failed.module("registry_entity", "available")
    failed.module("annual_accounts", "failed", note="HTTP 500")
    failed.module("registry_roles", "available")
    changes, meta = store.apply(failed, "r2", "2026-10-02T00:00:00Z")
    assert meta["carried_forward_claims"] >= 1
    assert failed.values("financials.revenue")
    assert not any(c["claim_key"].startswith("financials") for c in changes)


def test_summary_cites_claims_and_lists_unknowns():
    col = _collector_with("SANDNES ELEKTRISKE AS", 10_000_000.0, "Morten Aarrestad")
    summary = build_summary(col)
    assert summary["sentences"] and all(s["claim_ids"] for s in summary["sentences"])
    assert any("website" in u for u in summary["unknowns"])
    assert money(10_000_000, "NOK") == "NOK 10.0 m"


def test_read_inputs_handles_formats_and_bad_rows(tmp_path: Path):
    path = tmp_path / "batch.jsonl"
    path.write_text('{"organisation_number": "923609016"}\n{"organisation_number": "923 609 016"}\n{"orgnr": "810034882"}\n{"organisation_number": "12"}\n', encoding="utf-8")
    items = read_inputs(path)
    assert [i.organisation_number for i in items] == ["923609016", "810034882", None]
    text = tmp_path / "batch.txt"
    text.write_text("923609016\n810034882\n", encoding="utf-8")
    assert len(read_inputs(text)) == 2
