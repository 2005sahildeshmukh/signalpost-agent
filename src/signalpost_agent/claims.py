"""Claim and evidence model.

A claim is one atomic, attributable fact about the exact legal entity. Every claim points at one or
more evidence records; every evidence record points at a preserved raw snapshot (content hash) plus a
locator (JSON pointer, CSS selector or text span) inside it. Claim ids are derived from a stable claim
key and value, so re-running the same snapshot yields identical ids (idempotent refresh).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .fetch import Response, utc_now

AVAILABILITY = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")

SECTIONS = {
    "legal_identity": "Legal identity and public brand",
    "financials": "Latest annual accounts and available history",
    "leadership_workplaces": "Leadership and registered workplaces",
    "web_presence": "Verified official website and company-owned profiles",
    "hiring_activity": "Hiring and dated public activity",
}

SOURCE_CLASSES = {
    "official_registry",        # Brønnøysund Enhetsregisteret API
    "official_annual_accounts", # Regnskapsregisteret API
    "official_registry_updates",
    "open_knowledge_base",      # Wikidata (CC0), matched by organisation-number property P2333
    "company_owned",            # verified official website
    "public_job_board",         # NAV arbeidsplassen (Norwegian Labour and Welfare Administration)
}


def _digest(*parts: Any, length: int = 16) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


@dataclass
class Evidence:
    id: str
    source_url: str
    source_class: str
    retrieved_at: str
    content_sha256: str | None
    snapshot_path: str | None
    extraction_method: str
    final_url: str | None = None
    http_status: int | None = None
    locator: str | None = None
    claim_span: str | None = None
    reporting_period: str | None = None
    access_basis: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


@dataclass
class Claim:
    id: str
    key: str
    section: str
    field: str
    value: Any
    availability: str
    source_class: str
    evidence_ids: list[str]
    confidence: float = 1.0
    reporting_period: str | None = None
    effective_date: str | None = None
    identity_basis: str | None = None
    note: str | None = None
    first_observed_at: str | None = None
    last_observed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {key: value for key, value in self.__dict__.items() if value is not None}
        data["evidence_ids"] = self.evidence_ids
        return data


@dataclass
class Collector:
    organisation_number: str
    claims: dict[str, Claim] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    module_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    _by_key: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------ evidence
    def add_evidence(self, response: Response | None, *, source_class: str, method: str, locator: str | None = None,
                     span: str | None = None, period: str | None = None, url: str | None = None,
                     access_basis: str | None = None) -> str:
        meta = response.source_meta() if response else {}
        source_url = url or meta.get("final_url") or meta.get("source_url") or ""
        digest = meta.get("content_sha256")
        span = (span or "").strip()[:400] or None
        evidence_id = "ev-" + _digest(source_url, digest, locator, span, period)
        if evidence_id not in self.evidence:
            self.evidence[evidence_id] = Evidence(
                id=evidence_id,
                source_url=meta.get("source_url") or source_url,
                final_url=meta.get("final_url") if meta.get("final_url") != meta.get("source_url") else None,
                http_status=meta.get("http_status"),
                source_class=source_class,
                retrieved_at=meta.get("retrieved_at") or utc_now(),
                content_sha256=digest,
                snapshot_path=meta.get("snapshot_path"),
                extraction_method=method,
                locator=locator,
                claim_span=span,
                reporting_period=period,
                access_basis=access_basis,
            )
        return evidence_id

    # ------------------------------------------------------------ claims
    def add(self, section: str, field_name: str, value: Any, *, source_class: str, evidence_ids: list[str],
            key_suffix: str | None = None, availability: str = "available", confidence: float = 1.0,
            period: str | None = None, effective: str | None = None, identity: str | None = None,
            note: str | None = None) -> Claim | None:
        if availability == "available" and (value is None or value == "" or value == []):
            return None
        key = f"{section}.{field_name}" + (f".{key_suffix}" if key_suffix else "")
        if key_suffix and availability == "available" and key in self._by_key:
            # Same atomic fact from a second source (e.g. a social profile on both the site and Wikidata).
            existing = self.claims[self._by_key[key]]
            existing.evidence_ids = sorted(set(existing.evidence_ids) | set(evidence_ids))
            existing.confidence = max(existing.confidence, round(confidence, 3))
            return existing
        claim_id = "c-" + _digest(self.organisation_number, key, value if availability == "available" else availability)
        if claim_id in self.claims:
            existing = self.claims[claim_id]
            existing.evidence_ids = sorted(set(existing.evidence_ids) | set(evidence_ids))
            return existing
        claim = Claim(
            id=claim_id, key=key, section=section, field=field_name, value=value if availability == "available" else None,
            availability=availability, source_class=source_class, evidence_ids=sorted(set(evidence_ids)),
            confidence=round(confidence, 3), reporting_period=period, effective_date=effective,
            identity_basis=identity, note=note,
        )
        self.claims[claim_id] = claim
        if key_suffix and availability == "available":
            self._by_key[key] = claim_id
        return claim

    def absent(self, section: str, field_name: str, availability: str, *, source_class: str,
               evidence_ids: list[str] | None = None, note: str | None = None) -> None:
        """Record an explicit non-available state. Never converts absence into zero."""
        if availability == "available":
            raise ValueError("absent() requires a non-available state")
        self.add(section, field_name, None, source_class=source_class, evidence_ids=evidence_ids or [],
                 availability=availability, note=note, confidence=1.0)

    def has(self, field_prefix: str) -> bool:
        return any(c.key.startswith(field_prefix) and c.availability == "available" for c in self.claims.values())

    def values(self, key_prefix: str) -> list[Claim]:
        return [c for c in self.claims.values() if c.key.startswith(key_prefix) and c.availability == "available"]

    def module(self, name: str, state: str, **detail: Any) -> None:
        if state not in AVAILABILITY:
            raise ValueError(f"invalid availability state {state}")
        self.module_states[name] = {"state": state, "checked_at": utc_now(), **{k: v for k, v in detail.items() if v is not None}}

    def error(self, module: str, message: str, url: str | None = None) -> None:
        self.errors.append({"module": module, "message": message[:300], "url": url, "at": utc_now()})
