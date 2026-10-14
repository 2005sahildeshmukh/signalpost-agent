#!/usr/bin/env python3
"""Write a human-readable Markdown report for a run directory (used for the committed smoke test)."""
from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--command", default="uv run signalpost run --input smoke-100.jsonl --output-dir smoke --run-id smoke-100 --expected-count 100")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    report = json.loads((run_dir / "run-report.json").read_text(encoding="utf-8"))
    path = run_dir / "envelopes.jsonl"
    opener = open if path.exists() else gzip.open
    path = path if path.exists() else run_dir / "envelopes.jsonl.gz"
    with opener(path, "rt", encoding="utf-8") as handle:
        envelopes = [json.loads(line) for line in handle if line.strip()]
    n = len(envelopes)
    lines = [
        f"# Smoke test — {report['run_id']}",
        "",
        f"- Command: `{args.command}`",
        f"- Started {report['started_at']}, completed {report['completed_at']} ({report['wall_clock_seconds']} s wall clock)",
        f"- Inputs: {report['inputs']} · envelopes emitted: {report['emitted_envelopes']} · terminal states: {report['terminal_status_counts']}",
        f"- Contract validation: **{'passed' if report['validation']['passed'] else 'FAILED'}** — " + ", ".join(f"{k}={v}" for k, v in report["validation"]["checks"].items()),
        f"- Published claims: {report['claims_published']} · requests: {report['operations']['requests']} · p50/p95 per company: {report['operations']['p50_company_ms']} / {report['operations']['p95_company_ms']} ms",
        f"- Third-party cost: USD {report['operations']['third_party_cost_usd']} · language-model calls: {report['operations']['llm_calls']}",
        "",
        "## Coverage by field family",
        "",
        "| Family | Companies with data | % | Facts | States |",
        "|---|---:|---:|---:|---|",
    ]
    for family, row in report["coverage"].items():
        lines.append(f"| {family} | {row['companies_available']} | {row['companies_available'] * 100 // max(n, 1)}% | {row['facts']} | {', '.join(f'{k}: {v}' for k, v in sorted(row['states'].items()))} |")
    lines += ["", "## Website identity", "",
              f"- Verdicts on published sites: {report.get('website_identity_status')}",
              f"- How the published sites were found: {report.get('website_discovery_origin')}", ""]
    rows = []
    for env in envelopes:
        site = next((c for c in env["claims"] if c["key"] == "web_presence.official_website" and c["availability"] == "available"), None)
        if site:
            rows.append(f"| {env['organisation_number']} | {env['legal_name']} | {site['value']} | {(site.get('identity_basis') or '')[:110]} |")
    if rows:
        lines += ["| Org. no. | Legal name | Website | Identity proof |", "|---|---|---|---|", *rows, ""]
    states = Counter((m, s.get("state")) for env in envelopes for m, s in env["module_states"].items())
    lines += ["## Module states", "", "| Module | State | Companies |", "|---|---|---:|"]
    lines += [f"| {m} | {s} | {c} |" for (m, s), c in sorted(states.items())]
    lines += ["", "## Example summaries", ""]
    for env in sorted(envelopes, key=lambda e: -sum(1 for f in e["field_families"].values() if f["state"] == "available"))[:5]:
        lines += [f"**{env['legal_name']} ({env['organisation_number']})** — {env['summary']['text']}", ""]
    (run_dir / "SMOKE_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {run_dir / 'SMOKE_REPORT.md'}")


if __name__ == "__main__":
    main()
