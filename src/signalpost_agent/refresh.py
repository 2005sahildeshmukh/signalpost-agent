"""Refresh as a diff over stable claim keys.

State layout (all JSON, human-inspectable):
  <state>/profiles/<org>.json            latest published claims + evidence for the entity
  <state>/history/<org>/<run_id>.json    immutable copy of every prior envelope (never overwritten)

Re-running the same run id over unchanged sources yields identical claim ids and zero changes.
When a module fails, the last supported claims of that module are carried forward, marked stale,
so a failed refresh never erases evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .claims import Claim, Collector, Evidence

# Which claim-key prefixes a module is responsible for (used to carry values forward on failure).
MODULE_PREFIXES = {
    "registry_entity": ("legal_identity.",),
    "registry_roles": ("leadership_workplaces.role",),
    "registry_workplaces": ("leadership_workplaces.registered_workplace",),
    "registry_group": ("leadership_workplaces.group_",),
    "annual_accounts": ("financials.revenue", "financials.sales_revenue", "financials.operating", "financials.payroll",
                        "financials.net_financial", "financials.profit", "financials.annual_result", "financials.total",
                        "financials.fixed", "financials.current", "financials.cash", "financials.equity",
                        "financials.short", "financials.long", "financials.accounts_basis"),
    "annual_account_copies": ("financials.filed_account_years", "financials.annual_account_copy"),
    "nav_jobs": ("hiring_activity.job_posting.nav", "hiring_activity.active_job_postings_count"),
    "website": ("web_presence.", "hiring_activity.company_news", "hiring_activity.careers_page", "hiring_activity.recruitment_channel"),
}
MATERIAL_PREFIXES = (
    "legal_identity.legal_name", "legal_identity.status", "legal_identity.business_address", "legal_identity.legal_form",
    "legal_identity.registered_employees", "legal_identity.share_capital", "leadership_workplaces.role",
    "leadership_workplaces.registered_workplace", "leadership_workplaces.group_", "financials.revenue",
    "financials.annual_result", "financials.equity", "web_presence.official_website", "hiring_activity.job_posting",
    "legal_identity.latest_submitted_accounts_year", "legal_identity.industry",
)
CHANGE_TYPES = {
    "leadership_workplaces.role": ("new_role", "removed_role"),
    "leadership_workplaces.registered_workplace": ("new_location", "closed_location"),
    "hiring_activity.job_posting": ("new_job", "closed_job"),
    "financials.": ("new_filing", "removed_financial_value"),
    "hiring_activity.company_news": ("new_activity", "activity_no_longer_listed"),
    "web_presence.social_profile": ("new_profile", "removed_profile"),
}


def _change_type(key: str, kind: str) -> str:
    for prefix, (added, removed) in CHANGE_TYPES.items():
        if key.startswith(prefix):
            return added if kind == "added" else removed if kind == "removed" else "changed_value"
    return {"added": "new_fact", "removed": "removed_fact"}.get(kind, "changed_value")


def _short(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "title", "url", "amount", "address", "date"):
            if value.get(key) is not None:
                return str(value[key])[:80]
        return json.dumps(value, ensure_ascii=False)[:80]
    return str(value)[:80]


class RefreshStore:
    def __init__(self, root: Path):
        self.root = root
        (root / "profiles").mkdir(parents=True, exist_ok=True)
        (root / "history").mkdir(parents=True, exist_ok=True)

    def previous(self, org: str) -> dict[str, Any] | None:
        path = self.root / "profiles" / f"{org}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def apply(self, col: Collector, run_id: str, now: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        previous = self.previous(col.organisation_number)
        if not previous:
            for claim in col.claims.values():
                claim.first_observed_at = claim.first_observed_at or now
                claim.last_observed_at = now
            return [], {"previous_run_id": None, "first_run": True}
        prev_claims: dict[str, dict[str, Any]] = {c["id"]: c for c in previous.get("claims", [])}
        prev_by_key: dict[str, list[dict[str, Any]]] = {}
        for claim in previous.get("claims", []):
            prev_by_key.setdefault(claim["key"], []).append(claim)
        prev_evidence = {e["id"]: e for e in previous.get("evidence", [])}

        # Carry forward last supported values for modules that failed in this run.
        carried = 0
        for module, state in col.module_states.items():
            if state.get("state") not in ("failed", "blocked"):
                continue
            for prefix in MODULE_PREFIXES.get(module, ()):
                for old in previous.get("claims", []):
                    if not old["key"].startswith(prefix) or old.get("availability") != "available" or old["id"] in col.claims:
                        continue
                    restored = Claim(**{k: old.get(k) for k in Claim.__dataclass_fields__ if k in old})
                    restored.note = ((old.get("note") or "") + " [retained from previous run; refresh of this source failed]").strip()
                    restored.last_observed_at = old.get("last_observed_at")
                    col.claims[restored.id] = restored
                    for eid in restored.evidence_ids:
                        if eid in prev_evidence and eid not in col.evidence:
                            col.evidence[eid] = Evidence(**{k: prev_evidence[eid].get(k) for k in Evidence.__dataclass_fields__ if k in prev_evidence[eid]})
                    carried += 1

        changes: list[dict[str, Any]] = []
        current_keys: dict[str, list[Claim]] = {}
        for claim in col.claims.values():
            current_keys.setdefault(claim.key, []).append(claim)
            if claim.id in prev_claims:
                claim.first_observed_at = prev_claims[claim.id].get("first_observed_at") or previous.get("run", {}).get("completed_at")
                if not (claim.note and "retained from previous run" in claim.note):
                    claim.last_observed_at = now
            else:
                claim.first_observed_at = now
                claim.last_observed_at = now
        failed_modules = {m for m, s in col.module_states.items() if s.get("state") in ("failed", "blocked")}
        failed_prefixes = tuple(p for m in failed_modules for p in MODULE_PREFIXES.get(m, ()))
        for key, claims in current_keys.items():
            available_now = [c for c in claims if c.availability == "available"]
            old = [c for c in prev_by_key.get(key, []) if c.get("availability") == "available"]
            if not available_now:
                continue
            new_ids = {c.id for c in available_now}
            old_ids = {c["id"] for c in old}
            if new_ids == old_ids:
                continue
            claim = available_now[0]
            if not old:
                kind = "added"
                description = f"new {key.split('.')[1].replace('_', ' ')}: {_short(claim.value)}"
            else:
                kind = "changed"
                description = f"{key.split('.')[1].replace('_', ' ')} changed from {_short(old[0].get('value'))} to {_short(claim.value)}"
            changes.append(self._change(key, kind, old[0] if old else None, claim, description, prev_evidence))
        for key, olds in prev_by_key.items():
            if key in current_keys or failed_prefixes and key.startswith(failed_prefixes):
                continue
            old = next((c for c in olds if c.get("availability") == "available"), None)
            if old is None:
                continue
            description = f"{key.split('.')[1].replace('_', ' ')} no longer observed: {_short(old.get('value'))}"
            changes.append(self._change(key, "removed", old, None, description, prev_evidence))
        meta = {
            "previous_run_id": previous.get("run", {}).get("run_id"),
            "previous_completed_at": previous.get("run", {}).get("completed_at"),
            "first_run": False,
            "carried_forward_claims": carried,
        }
        return changes, meta

    def _change(self, key: str, kind: str, old: dict[str, Any] | None, new: Claim | None, description: str,
                prev_evidence: dict[str, Any]) -> dict[str, Any]:
        material = key.startswith(MATERIAL_PREFIXES)
        return {
            "claim_key": key,
            "change": kind,
            "change_type": _change_type(key, kind),
            "material": material,
            "description": description,
            "claim_id": new.id if new else None,
            "previous_claim_id": old.get("id") if old else None,
            "old_value": old.get("value") if old else None,
            "new_value": new.value if new else None,
            "old_evidence": [prev_evidence[e] for e in (old or {}).get("evidence_ids", []) if e in prev_evidence][:2],
            "new_evidence_ids": new.evidence_ids if new else [],
            "first_observed_at": new.first_observed_at if new else None,
            "last_observed_at_previous": old.get("last_observed_at") if old else None,
        }

    def save(self, envelope: dict[str, Any], run_id: str) -> None:
        org = envelope["organisation_number"]
        history_dir = self.root / "history" / org
        history_dir.mkdir(parents=True, exist_ok=True)
        previous = self.root / "profiles" / f"{org}.json"
        if previous.exists():
            try:
                old = json.loads(previous.read_text(encoding="utf-8"))
                old_run = old.get("run", {}).get("run_id") or "unknown"
                archived = history_dir / f"{old_run}.json"
                if not archived.exists():
                    archived.write_text(previous.read_text(encoding="utf-8"), encoding="utf-8")
            except (OSError, ValueError):
                pass
        state = {key: envelope[key] for key in ("organisation_number", "run", "claims", "evidence") if key in envelope}
        temporary = previous.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(previous)
