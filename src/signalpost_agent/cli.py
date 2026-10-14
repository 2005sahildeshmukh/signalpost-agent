"""Command line entry point.

    signalpost run --input batch.jsonl --output-dir out

Writes out/envelopes.jsonl (one terminal envelope per input, input order), out/run-report.json,
out/site/index.html (browsable, verifiable profiles), out/snapshots/ (raw evidence) and refresh state.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import Agent, RunConfig, read_inputs, run_report
from .site import build_site


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def cmd_run(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
    config = RunConfig(
        run_id=run_id, output_dir=output_dir, state_dir=Path(args.state_dir) if args.state_dir else output_dir / "state",
        concurrency=args.concurrency, per_company_timeout=args.per_company_timeout, time_budget=args.time_budget,
        guess_domains=not args.no_domain_guess, filing_years=not args.no_filing_years, jobs=not args.no_jobs,
        website=not args.no_website, wikidata=not args.no_wikidata,
        registry_snapshot=Path(args.registry_snapshot) if args.registry_snapshot else None, expected_count=args.expected_count,
    )
    items = read_inputs(input_path)
    print(f"[signalpost] run {run_id}: {len(items)} organisations from {input_path}", file=sys.stderr)
    started = time.monotonic()
    agent = Agent(config)
    result = asyncio.run(agent.run(items))
    envelopes = result["envelopes"]
    envelopes_path = Path(args.output) if args.output else output_dir / "envelopes.jsonl"
    _write_jsonl(envelopes_path, envelopes)
    report = run_report(result, config, agent, time.monotonic() - started)
    report["outputs"] = {"envelopes": str(envelopes_path), "site": str(output_dir / "site" / "index.html"),
                         "snapshots": str(output_dir / "snapshots"), "state": str(config.state_dir)}
    report_path = Path(args.report) if args.report else output_dir / "run-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.no_site:
        build_site(envelopes, output_dir / "site", report)
    print(json.dumps({k: report[k] for k in ("run_id", "inputs", "emitted_envelopes", "validation", "wall_clock_seconds")}, indent=2), file=sys.stderr)
    return 0 if report["validation"]["passed"] else 1


def cmd_site(args: argparse.Namespace) -> int:
    path = Path(args.envelopes)
    envelopes = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = json.loads(Path(args.report).read_text(encoding="utf-8")) if args.report else {}
    build_site(envelopes, Path(args.output_dir), report)
    print(f"site written to {args.output_dir}/index.html", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signalpost", description="Evidence-first Norwegian company research agent")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="research a batch of organisation numbers")
    run.add_argument("--input", "--organisations", dest="input", required=True, help="JSONL/JSON/CSV/TXT of organisation numbers")
    run.add_argument("--output-dir", default="out", help="directory for envelopes, report, site, snapshots and state")
    run.add_argument("--output", help="optional explicit path for the envelope JSONL")
    run.add_argument("--report", help="optional explicit path for the run report JSON")
    run.add_argument("--run-id")
    run.add_argument("--state-dir", help="refresh state (previous snapshots); default <output-dir>/state")
    run.add_argument("--registry-snapshot", "--bulk", dest="registry_snapshot", help="optional frozen registry snapshot used as identity fallback")
    run.add_argument("--expected-count", type=int)
    run.add_argument("--concurrency", type=int, default=24)
    run.add_argument("--per-company-timeout", type=float, default=150.0)
    run.add_argument("--time-budget", type=float, default=float(os.environ.get("SIGNALPOST_TIME_BUDGET", "1500")),
                     help="seconds for the whole batch (default 1500, or $SIGNALPOST_TIME_BUDGET); every company still gets an envelope")
    run.add_argument("--no-domain-guess", action="store_true")
    run.add_argument("--no-filing-years", action="store_true")
    run.add_argument("--no-jobs", action="store_true")
    run.add_argument("--no-website", action="store_true")
    run.add_argument("--no-wikidata", action="store_true")
    run.add_argument("--no-site", action="store_true")
    run.set_defaults(func=cmd_run)
    site = sub.add_parser("site", help="rebuild the HTML site from an envelope file")
    site.add_argument("--envelopes", required=True)
    site.add_argument("--report")
    site.add_argument("--output-dir", default="out/site")
    site.set_defaults(func=cmd_site)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
