# Envelope schema (`signalpost.envelope.v1`)

One JSON object per input line in `envelopes.jsonl`, in input order.

```jsonc
{
  "schema_version": "signalpost.envelope.v1",
  "organisation_number": "810034882",
  "legal_name": "SANDNES ELEKTRISKE AS",
  "status": "completed",                       // completed | failed (terminal)
  "run": {"run_id", "agent", "agent_version", "batch_started_at", "started_at", "completed_at", "terminal_status"},
  "summary": {
    "text": "...",                              // concatenated sentences
    "sentences": [{"kind": "financials", "text": "...", "claim_ids": ["c-..."]}],
    "risk_flags": [{"flag": "negative_equity", "text": "...", "claim_ids": [...]}],
    "unknowns": ["No verified active job postings.", ...],
    "what_changed": [...], "method": "deterministic_template_v1 ..."
  },
  "profile": { ... compact view; every value carries its claim_id ... },
  "sections": {                                 // the seven required sections map onto these five + claims/evidence + refresh
    "legal_identity": {"title", "availability", "claims", "unavailable_fields"},
    "financials": {...}, "leadership_workplaces": {...}, "web_presence": {...}, "hiring_activity": {...}
  },
  "field_families": {"official_website": {"state": "available", "facts": 1}, "job_postings": {"state": "not_available", "facts": 0, "reason": "..."}, ...},
  "module_states": {"registry_entity": {"state": "available", "checked_at": "..."}, "nav_jobs": {...}, ...},
  "claims": [{
    "id": "c-<16 hex>",                         // hash(org, claim key, value): stable across identical refreshes
    "key": "financials.revenue.SELSKAP.2025-12-31",
    "section": "financials", "field": "revenue",
    "value": {"amount": 10012000.0, "currency": "NOK", "account_type": "SELSKAP"},
    "availability": "available",                // available | not_available | blocked | not_applicable | ambiguous | failed
    "source_class": "official_annual_accounts",
    "evidence_ids": ["ev-..."], "confidence": 1.0,
    "reporting_period": "2025-01-01/2025-12-31", "effective_date": null,
    "identity_basis": "organisation_number", "note": null,
    "first_observed_at": "...", "last_observed_at": "..."
  }],
  "evidence": [{
    "id": "ev-<16 hex>", "source_url": "...", "final_url": "...", "http_status": 200,
    "source_class": "official_annual_accounts", "retrieved_at": "2026-10-14T12:21:42Z",
    "content_sha256": "...", "snapshot_path": "snapshots/ab/<sha256>.gz",
    "extraction_method": "brreg_accounts_api_v1", "locator": "json:/0/resultatregnskapResultat/...",
    "claim_span": "revenue=10012000.0 NOK", "reporting_period": "...", "access_basis": "..."
  }],
  "changes": [{"claim_key", "change", "change_type", "material", "description", "claim_id", "previous_claim_id",
               "old_value", "new_value", "old_evidence", "new_evidence_ids", "first_observed_at", "last_observed_at_previous"}],
  "refresh": {"previous_run_id", "previous_completed_at", "first_run", "carried_forward_claims", "change_count", "material_change_count"},
  "identity_candidates": [{"type": "website", "url", "origin", "final_url", "verdict", "score", "reasons"}],
  "errors": [{"module", "message", "url", "at"}],
  "operations": {"requests", "bytes", "runtime_ms", "third_party_cost_usd", "llm_calls"}
}
```

Non-available states are recorded as claims with `value: null` and a `note`, so “checked and found nothing” is distinct
from “not checked”. Missing financial line items are omitted, never zero. Locators: `json:<pointer>` for API
responses, `html:<selector>`, `jsonld:<property>`, `opengraph:<property>`, `text:<rule>`, `xml:<path>`, `url:<rule>`.
Snapshots are the exact bytes received, gzip-compressed, named by SHA-256.
