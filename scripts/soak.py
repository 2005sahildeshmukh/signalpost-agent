#!/usr/bin/env python3
"""Unattended soak test: several fresh batches plus refresh re-runs, summarised in one Markdown file.

    uv run python scripts/soak.py --universe data/signalpost-universe.jsonl.gz --out work/soak

Each step is a separate `signalpost run` subprocess (same command an evaluator would use). A failing step is
recorded and the soak continues.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sh(cmd: list[str], log: Path, timeout: float) -> tuple[int, float]:
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as handle:
        try:
            code = subprocess.run(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = -9
    return code, time.monotonic() - started


def analyse(run_dir: Path) -> dict:
    out: dict = {"run_dir": str(run_dir)}
    report_path = run_dir / "run-report.json"
    env_path = run_dir / "envelopes.jsonl"
    if not report_path.exists() or not env_path.exists():
        out["missing_outputs"] = True
        return out
    report = json.loads(report_path.read_text(encoding="utf-8"))
    envelopes = [json.loads(line) for line in env_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    errors = Counter((e["module"], e["message"][:60]) for env in envelopes for e in env["errors"])
    module_failed = Counter(m for env in envelopes for m, s in env["module_states"].items() if s.get("state") == "failed")
    shared_sites = Counter()
    for env in envelopes:
        for c in env["claims"]:
            if c["key"] == "web_presence.official_website" and c["availability"] == "available":
                shared_sites[c["value"]] += 1
    weak_sites = [(env["organisation_number"], env["legal_name"], c["value"], env["module_states"].get("website_identity", {}).get("status"))
                  for env in envelopes for c in env["claims"]
                  if c["key"] == "web_presence.official_website" and c["availability"] == "available"
                  and (c.get("confidence") or 0) < 0.95]
    out.update({
        "validation_passed": report["validation"]["passed"],
        "inputs": report["inputs"], "emitted": report["emitted_envelopes"],
        "wall_clock_seconds": report["wall_clock_seconds"],
        "coverage": {k: v["companies_available"] for k, v in report["coverage"].items()},
        "requests": report["operations"]["requests"],
        "p95_company_ms": report["operations"]["p95_company_ms"],
        "changes": report.get("changes_detected"), "material_changes": report.get("material_changes"),
        "top_errors": errors.most_common(6),
        "module_failed": dict(module_failed.most_common(8)),
        "duplicate_websites": [(site, n) for site, n in shared_sites.items() if n > 1],
        "low_confidence_websites": weak_sites[:25],
        "website_origins": report.get("website_discovery_origin"),
    })
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="data/signalpost-universe.jsonl.gz")
    parser.add_argument("--out", default="work/soak")
    parser.add_argument("--seeds", default="101,202,303")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--resume", action="store_true", help="keep finished steps from soak-results.json and run only the rest")
    args = parser.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    py = [sys.executable]
    steps: list[dict] = []
    if args.resume and (out / "soak-results.json").exists():
        steps = json.loads((out / "soak-results.json").read_text(encoding="utf-8"))
    done = {s["step"] for s in steps}
    plan: list[tuple[str, str, list[str], float]] = []
    seeds = [s.strip() for s in args.seeds.split(",") if s.strip()]
    for index, seed in enumerate(seeds):
        batch = out / f"batch-{seed}.jsonl"
        subprocess.run(py + ["scripts/select_batch.py", "--universe", args.universe, "--output", str(batch), "--count", str(args.count), "--seed", seed], cwd=ROOT, check=True)
        budget = "1500" if index else "2700"  # first batch: let the slow filing lane finish; others: default budget
        run_dir = out / f"run-{seed}"
        plan.append((f"fresh-{seed}", str(run_dir), ["--input", str(batch), "--output-dir", str(run_dir), "--run-id", f"soak-{seed}-a",
                                                      "--expected-count", str(args.count), "--time-budget", budget], float(budget) + 900))
        if index == 0:
            plan.append((f"refresh-{seed}", str(run_dir), ["--input", str(batch), "--output-dir", str(run_dir), "--run-id", f"soak-{seed}-b",
                                                           "--expected-count", str(args.count), "--time-budget", "1500"], 2400))
    plan.append(("smoke-refresh", "work/soak/smoke", ["--input", "smoke-100.jsonl", "--output-dir", str(out / "smoke"), "--run-id", "soak-smoke-a", "--expected-count", "100"], 1500))
    plan.append(("smoke-refresh-2", "work/soak/smoke", ["--input", "smoke-100.jsonl", "--output-dir", str(out / "smoke"), "--run-id", "soak-smoke-b", "--expected-count", "100"], 1500))
    for name, run_dir, run_args, timeout in plan:
        if name in done:
            continue
        log = out / f"{name}.log"
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        code, seconds = sh(["uv", "run", "signalpost", "run", *run_args], log, timeout)
        result = {"step": name, "started": started, "exit_code": code, "seconds": round(seconds, 1), **analyse(ROOT / run_dir if not Path(run_dir).is_absolute() else Path(run_dir))}
        steps.append(result)
        (out / "soak-results.json").write_text(json.dumps(steps, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: result.get(k) for k in ("step", "exit_code", "seconds", "validation_passed", "emitted")}), flush=True)
    lines = ["# Overnight soak summary", "", f"Finished {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "",
             "| Step | Exit | Seconds | Valid | Emitted/Inputs | Websites | Socials | Jobs | News | Changes (material) | Requests |",
             "|---|---|---:|---|---|---:|---:|---:|---:|---|---:|"]
    for s in steps:
        cov = s.get("coverage") or {}
        lines.append(f"| {s['step']} | {s['exit_code']} | {s['seconds']} | {s.get('validation_passed')} | {s.get('emitted')}/{s.get('inputs')} | "
                     f"{cov.get('official_website')} | {cov.get('social_profiles')} | {cov.get('job_postings')} | {cov.get('company_news')} | "
                     f"{s.get('changes')} ({s.get('material_changes')}) | {s.get('requests')} |")
    for s in steps:
        lines += ["", f"## {s['step']}", "", f"- module failures: {s.get('module_failed')}", f"- top errors: {s.get('top_errors')}",
                  f"- website origins: {s.get('website_origins')}", f"- websites shared by several companies: {s.get('duplicate_websites')}",
                  f"- low-confidence websites to audit: {s.get('low_confidence_websites')}"]
    (out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"summary: {out / 'SUMMARY.md'}", flush=True)


if __name__ == "__main__":
    main()
