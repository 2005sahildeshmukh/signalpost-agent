# Signalpost evidence agent

An evidence-first research agent for Norwegian companies, built for the Builderr
[Signalpost challenge](https://builderr.ai/challenges/signalpost). Give it organisation numbers. For each one it returns
**exactly one terminal envelope**: the legal identity, the latest filed accounts and their history, leadership and
registered workplaces, a verified official website and company-owned profiles, hiring and dated public activity, a
cited summary, explicit availability states, and changes since the previous run. Every published fact links to a
preserved source snapshot with its retrieval time, content hash, locator and the quoted span.

## One command

```bash
uv sync --frozen && uv run signalpost run --input <batch.jsonl> --output-dir out
```

- `--input` accepts JSONL (`{"organisation_number": "..."}` per line), JSON arrays, CSV or plain text (one number per line).
- Output: `out/envelopes.jsonl` (one envelope per input, input order), `out/run-report.json`, `out/site/index.html`,
  `out/snapshots/` (raw evidence, gzip, content-addressed) and `out/state/` (refresh state and history).
- Re-run with the same `--output-dir` (or `--state-dir`) to refresh: unchanged facts keep their ids and first-seen
  time, changes are emitted as typed diffs, and the previous envelope is archived under `state/history/<org>/<run_id>.json`.
- Starter-kit compatible aliases: `--organisations`, `--output`, `--report`, `--bulk`, `--expected-count`, `--run-id`.

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/) (or `pip install .` into a fresh 3.12 venv, then
`signalpost run ...`). No API key, no account and no company download is needed.

**Time budget.** `--time-budget` (default 1500 s, or `$SIGNALPOST_TIME_BUDGET`) bounds the whole batch. Measured on a
laptop: 100 companies ≈ 4 min; 1,000 companies ≈ 10–12 min of research plus the polite ~30/min lane for the Brreg
filing-copy year list (36 min in total when that lane is allowed to finish, run `scale-1000`). When the budget is
reached, unfinished low-priority work (the filing-year lane first) is stopped and every company still gets its envelope,
with the affected module marked `failed` and a note. Set the budget to the evaluator's limit minus a safety margin.

### Smoke test

```bash
uv run python scripts/select_batch.py --universe signalpost-universe.jsonl.gz --count 100 --output smoke-100.jsonl
uv run signalpost run --input smoke-100.jsonl --output-dir smoke --run-id smoke-100 --expected-count 100
uv run pytest -q
```

The committed result of the 100-company smoke test is in [`smoke/`](smoke/) (`run-report.json`, `envelopes.jsonl.gz`,
`SMOKE_REPORT.md` and the browsable `site/`).

## What it collects

| Envelope section | Sources (in order of trust) | Notes |
|---|---|---|
| Legal identity and public brand | Enhetsregisteret API (NLOD 2.0); brand only from the verified site | name, form, status, founding/registration dates, industry codes, addresses, phone, capital, purpose, former names, VAT/register flags |
| Annual accounts and history | Regnskapsregisteret API; filing-copy year list | up to three normalized years per account type, 16 line items each, audit flags, filing years and PDF copy links; missing lines are omitted, never zero |
| Leadership and workplaces | Roles API, subunits API, group-structure API; verified site | CEO, chair, board, auditor, accountant; registered subunits with addresses and employees; parent/subsidiaries kept as labelled relations; registry role holders named on the company site |
| Website and company-owned profiles | registry website, Wikidata P2333, registry e-mail domain, subunit websites, name-derived `.no/.com` domains | published only after the identity gate (below); social links from the verified site or the Wikidata item; contact e-mail/phone; JSON-LD organisation data |
| Hiring and dated activity | NAV arbeidsplassen (robots allow), verified site, Brreg update stream | job ads verified by the **employer organisation number on the ad** (entity or its subunits); careers pages and ATS links; dated news from listings, RSS/Atom and sitemaps; filings and dated registry updates |
| Evidence and availability | — | per-claim evidence ids; per-section, per-module and per-field-family states: `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed` |
| Refresh | local state | stable claim keys, first/last observed, typed changes (`new_role`, `closed_job`, `new_filing`, `new_location`…), materiality, both-side evidence, carry-forward on failed modules |

## Identity gate (precision first)

A website candidate is published as the official website only when its own fetched pages tie it to the exact entity:

1. the organisation number appears on the site (mod-11 validated), **or**
2. it is declared by an official source (Enhetsregisteret or the Wikidata item matched by organisation number) **and**
   the site restates the legal name, phone, e-mail domain, address or a registered role holder, **or**
3. a derived candidate shows the legal name **and** an independent corroboration (registry phone, registry e-mail
   domain, street + postal code, registered person).

A site showing a different organisation number is a `conflict` and never exact for a derived candidate. Parked,
placeholder and directory hosts are rejected. A registry-declared site that cannot be corroborated is published as the
declared URL only (`declared_only`), and nothing is extracted from it. Social profiles must be linked from the verified
site and their handle must match the legal name, brand or domain (or come from the Wikidata item). Across the batch, a
profile or site claimed by more than one entity is demoted to `ambiguous`. Every candidate checked and its verdict are
listed in `identity_candidates`.

See [IDENTITY_RESOLUTION.md](IDENTITY_RESOLUTION.md), [DATA_SCHEMA.md](DATA_SCHEMA.md), [REFRESH.md](REFRESH.md),
[SOURCES.md](SOURCES.md) and [LIMITATIONS.md](LIMITATIONS.md).

## Models, APIs, licences and cost

- **Models:** none. Synthesis is deterministic and every sentence cites claim ids (`llm_calls: 0`).
- **APIs (all keyless, public):** Brønnøysundregistrene Enhetsregisteret and Regnskapsregisteret (NLOD 2.0), Wikidata
  SPARQL (CC0), NAV arbeidsplassen public search/ad pages (robots.txt permits), company websites (robots.txt respected).
- **Optional:** `BRAVE_SEARCH_API_KEY` enables search-API website candidates (results are transient candidates only;
  ~USD 0.005/query). Disabled by default; only use an evaluator-supplied key.
- **Third-party cost per official run:** USD 0 by default.
- **Code licence:** MIT. Python dependencies: httpx (BSD), beautifulsoup4 (MIT), lxml (BSD), extruct (BSD), tldextract (BSD), trafilatura (Apache-2.0).

## Safety

Only public `http(s)` URLs on ports 80/443 are fetched; every hop of every redirect is re-resolved and private,
loopback, link-local and reserved addresses are refused (SSRF guard). Response size is capped. robots.txt is honoured
for every non-API host with our identifying user agent. Per-host concurrency and spacing, adaptive back-off on 429, and
a dedicated ~30/minute lane for the Brreg filing-copy endpoint keep the load polite. No credentials are used or stored.
Birth dates from the roles API are discarded.
