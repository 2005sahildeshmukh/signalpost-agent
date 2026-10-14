"""Static, dependency-free review site: find, compare and verify every claim (desktop and mobile).

index.html     searchable / filterable directory with side-by-side comparison of up to four companies
c/<org>.html   one page per company: cited summary, sections, every claim with its source, evidence
               (retrieval time, content hash, snapshot, locator, quoted span), unknowns and changes
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .synthesis import money

SECTION_TITLES = {
    "legal_identity": "Identity",
    "financials": "Financials",
    "leadership_workplaces": "Leadership & workplaces",
    "web_presence": "Website & profiles",
    "hiring_activity": "Hiring & activity",
}
STATE_LABEL = {"available": "Found", "not_available": "Not found", "blocked": "Blocked", "not_applicable": "Not applicable",
               "ambiguous": "Ambiguous", "failed": "Failed"}
FAMILY_LABEL = {
    "legal_identity": "Identity", "public_brand": "Brand", "annual_accounts": "Accounts", "account_history": "Filing history",
    "leadership": "Leadership", "registered_workplaces": "Workplaces", "official_website": "Website", "social_profiles": "Social",
    "contact_details": "Contact", "job_postings": "Jobs", "careers_channel": "Careers", "company_news": "News", "registry_activity": "Registry events",
}

CSS = r"""
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1c1d1f;--muted:#5d6168;--line:#e3e3df;--accent:#0b6e4f;--accent-ink:#fff;--warn:#a15c00;--bad:#b42318;--chip:#eef2ef;--link:#0b5cad}
@media (prefers-color-scheme:dark){:root{--bg:#121315;--panel:#1b1c1f;--ink:#ececec;--muted:#a3a7ad;--line:#2c2e33;--accent:#3fbf8f;--accent-ink:#0b1a14;--warn:#f0b35a;--bad:#ff8a80;--chip:#23262a;--link:#7fb6ff}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--link)}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wrap{max-width:1180px;margin:0 auto;padding:16px}
header.top{background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
header.top .wrap{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding-top:10px;padding-bottom:10px}
.brand{font-weight:700;letter-spacing:.2px;text-decoration:none;color:var(--ink)}.brand span{color:var(--accent)}
.muted{color:var(--muted)}.small{font-size:13px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;word-break:break-all}
.grid{display:grid;gap:12px}.stats{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.stat b{font-size:22px;display:block}
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0}
.controls input[type=search]{flex:1 1 260px;min-width:0;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);font-size:15px}
select,button{padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);font-size:14px;cursor:pointer}
button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.chip{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:99px;background:var(--chip);font-size:12.5px;margin:2px 4px 2px 0;white-space:nowrap}
.chip.ok{background:color-mix(in srgb,var(--accent) 16%,transparent)}.chip.warn{background:color-mix(in srgb,var(--warn) 18%,transparent)}.chip.bad{background:color-mix(in srgb,var(--bad) 18%,transparent)}
label.filter{display:inline-flex;gap:6px;align-items:center;font-size:14px;padding:6px 10px;border:1px solid var(--line);border-radius:99px;background:var(--panel);cursor:pointer}
.list{display:grid;gap:10px}
.co{display:grid;grid-template-columns:1fr auto;gap:6px 14px;align-items:start}
.co h3{margin:0;font-size:16px}.co h3 a{text-decoration:none;color:var(--ink)}.co h3 a:hover{text-decoration:underline}
.dots{display:flex;gap:3px;flex-wrap:wrap;margin-top:6px}.dot{width:10px;height:10px;border-radius:3px;background:var(--line)}.dot.on{background:var(--accent)}
.kv{display:grid;grid-template-columns:minmax(120px,190px) 1fr;gap:6px 14px}.kv dt{color:var(--muted)}.kv dd{margin:0;min-width:0;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600;font-size:13px}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
section.block{margin:18px 0}section.block>h2{font-size:18px;margin:0 0 8px}
.cite{font-size:11px;vertical-align:super;text-decoration:none;padding:0 2px}
.summary p{margin:.35em 0}
.ev{font-size:13px;border-left:3px solid var(--line);padding:6px 10px;margin:6px 0;background:var(--bg);border-radius:0 6px 6px 0}
details>summary{cursor:pointer;color:var(--link);font-size:13px}
.claimrow:target{background:color-mix(in srgb,var(--accent) 14%,transparent)}
#compare{position:fixed;left:0;right:0;bottom:0;background:var(--panel);border-top:1px solid var(--line);padding:10px 16px;display:none;z-index:6;max-height:70vh;overflow:auto}
#compare.show{display:block}
.cmp th{position:sticky;left:0;background:var(--panel)}
.badge{font-size:12px;padding:2px 8px;border-radius:6px;border:1px solid var(--line)}
nav.toc{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}nav.toc a{font-size:13px;text-decoration:none;padding:4px 10px;border:1px solid var(--line);border-radius:99px;background:var(--panel);color:var(--ink)}
@media (max-width:640px){.wrap{padding:12px}.kv{grid-template-columns:1fr}.kv dt{font-weight:600;margin-top:6px}.co{grid-template-columns:1fr}.hide-sm{display:none}}
"""

APP_JS = r"""
(function(){
const data = window.SP_DATA || [];
const $ = s => document.querySelector(s);
const list = $('#list'), q = $('#q'), sort = $('#sort'), count = $('#count');
const filters = [...document.querySelectorAll('input[data-f]')];
const picked = new Set();
const fmt = n => n==null ? '—' : new Intl.NumberFormat('nb-NO',{maximumFractionDigits:0}).format(n);
const esc = s => String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function matches(c, term){ if(!term) return true; const t = term.toLowerCase(); return c.search.includes(t); }
function render(){
  const term = q.value.trim(); const active = filters.filter(f=>f.checked).map(f=>f.dataset.f);
  let rows = data.filter(c => matches(c, term) && active.every(f => c.flags[f]));
  const key = sort.value;
  rows.sort((a,b)=> key==='name' ? a.name.localeCompare(b.name,'nb') : key==='revenue' ? (b.revenue??-1e18)-(a.revenue??-1e18) : key==='jobs' ? b.jobs-a.jobs : b.score-a.score || a.name.localeCompare(b.name,'nb'));
  count.textContent = rows.length + ' of ' + data.length + ' companies';
  list.innerHTML = rows.slice(0,400).map(c => `
   <article class="card co">
     <div><h3><a href="c/${c.org}.html">${esc(c.name)}</a></h3>
       <div class="small muted">${c.org} · ${esc(c.form||'')} · ${esc(c.municipality||'')}${c.brand?` · brand “${esc(c.brand)}”`:''}</div>
       <div class="small">${esc(c.industry||'')}</div>
       <div>${c.website?`<span class="chip ok">🌐 ${esc(c.website.replace(/^https?:\/\//,''))}</span>`:''}${c.jobs?`<span class="chip ok">${c.jobs} job${c.jobs>1?'s':''}</span>`:''}${c.news?`<span class="chip">${c.news} news</span>`:''}${c.socials?`<span class="chip">${c.socials} profiles</span>`:''}${c.risk.map(r=>`<span class="chip bad">${esc(r)}</span>`).join('')}</div>
       <div class="dots" title="information areas found">${c.families.map(f=>`<span class="dot ${f[1]?'on':''}" title="${esc(f[0])}: ${f[1]?'found':'not found'}"></span>`).join('')}</div>
     </div>
     <div class="small" style="text-align:right">
       ${c.revenue!=null?`<div><span class="muted">Revenue ${esc(c.period||'')}</span><br><b>${fmt(c.revenue)}</b> ${esc(c.currency||'')}</div>`:'<div class="muted">No filed revenue</div>'}
       <label class="filter" style="margin-top:6px"><input type="checkbox" data-pick="${c.org}" ${picked.has(c.org)?'checked':''}> Compare</label>
     </div>
   </article>`).join('') + (rows.length>400?`<p class="muted">Showing the first 400 — refine the search.</p>`:'');
}
function renderCompare(){
  const box = $('#compare'); const rows = data.filter(c=>picked.has(c.org));
  if(!rows.length){ box.classList.remove('show'); return; }
  box.classList.add('show');
  const fields = [['Organisation no.',c=>c.org],['Legal form',c=>c.form],['Municipality',c=>c.municipality],['Industry',c=>c.industry],['Founded',c=>c.founded],['Employees (register)',c=>c.employees==null?'not registered':fmt(c.employees)],['Revenue',c=>c.revenue==null?'—':fmt(c.revenue)+' '+(c.currency||'')+' ('+(c.period||'')+')'],['Annual result',c=>c.result==null?'—':fmt(c.result)],['Equity',c=>c.equity==null?'—':fmt(c.equity)],['CEO',c=>c.ceo],['Chair',c=>c.chair],['Website',c=>c.website],['Social profiles',c=>c.socials],['Active jobs',c=>c.jobs],['News items',c=>c.news],['Workplaces',c=>c.workplaces]];
  box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><b>Compare (${rows.length}/4)</b><button id="clr">Clear</button></div>
   <div class="tablewrap"><table class="cmp small"><tr><th></th>${rows.map(c=>`<th><a href="c/${c.org}.html">${esc(c.name)}</a></th>`).join('')}</tr>
   ${fields.map(([l,f])=>`<tr><th>${l}</th>${rows.map(c=>`<td>${esc(f(c)??'—')}</td>`).join('')}</tr>`).join('')}</table></div>`;
  $('#clr').onclick = ()=>{picked.clear(); render(); renderCompare();};
}
list.addEventListener('change', e => { const o = e.target.dataset.pick; if(!o) return;
  if(e.target.checked){ if(picked.size>=4){ e.target.checked=false; return;} picked.add(o);} else picked.delete(o); renderCompare(); });
[q, sort, ...filters].forEach(el => el.addEventListener('input', render));
const params = new URLSearchParams(location.search); if(params.get('q')) q.value = params.get('q');
render();
document.addEventListener('keydown', e => { if(e.key==='/' && document.activeElement!==q){ e.preventDefault(); q.focus(); }});
})();
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _page(title: str, body: str, *, root: str = "") -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><link rel="stylesheet" href="{root}assets/style.css"></head><body>
<header class="top"><div class="wrap"><a class="brand" href="{root}index.html">Signal<span>post</span></a><span class="muted small">Norwegian company research · every fact linked to its source</span></div></header>
<main class="wrap">{body}</main></body></html>"""


def _value_text(claim: dict[str, Any]) -> str:
    value = claim.get("value")
    if isinstance(value, dict):
        if "amount" in value:
            return f"{money(value['amount'], value.get('currency'))} ({value.get('amount'):,.0f})".replace(",", " ")
        if "url" in value and "platform" in value:
            return f"{value['platform']}: {value['url']}"
        if "title" in value:
            parts = [value.get("title"), value.get("date") or value.get("published"), value.get("location")]
            return " · ".join(str(p) for p in parts if p)
        if "role" in value and "name" in value:
            return f"{value['name']} — {value['role']}"
        if "name" in value:
            return " · ".join(str(value[k]) for k in ("name", "address", "organisation_number", "relation") if value.get(k))
        if "description" in value and "code" in value:
            return f"{value['description']} ({value['code']})"
        return ", ".join(f"{k}: {v}" for k, v in value.items() if v not in (None, "", [], {}))
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return "" if value is None else str(value)


def _link_value(text: str) -> str:
    if text.startswith(("http://", "https://")):
        return f'<a href="{esc(text)}" rel="noopener nofollow" target="_blank">{esc(text)}</a>'
    return esc(text)


def _evidence_html(ev: dict[str, Any]) -> str:
    url = ev.get("final_url") or ev.get("source_url") or ""
    link = f'<a href="{esc(url)}" rel="noopener nofollow" target="_blank">{esc(url[:110])}</a>' if url.startswith("http") else esc(url)
    span = f'<div>“{esc(ev.get("claim_span"))}”</div>' if ev.get("claim_span") else ""
    return (f'<div class="ev">{span}<div>{link}</div><div class="muted">{esc(ev.get("source_class"))} · retrieved {esc(ev.get("retrieved_at"))}'
            f'{" · period " + esc(ev.get("reporting_period")) if ev.get("reporting_period") else ""} · {esc(ev.get("extraction_method"))}'
            f'{" · " + esc(ev.get("locator")) if ev.get("locator") else ""}</div>'
            f'<div class="mono muted">sha256 {esc((ev.get("content_sha256") or "n/a"))}{" · " + esc(ev.get("snapshot_path")) if ev.get("snapshot_path") else ""}</div></div>')


def company_page(env: dict[str, Any]) -> str:
    claims = env["claims"]
    by_id = {c["id"]: c for c in claims}
    evidence = {e["id"]: e for e in env["evidence"]}
    cite_index: dict[str, int] = {}
    order = [c["id"] for c in claims]
    for i, cid in enumerate(order, 1):
        cite_index[cid] = i
    name = env.get("legal_name") or env["organisation_number"]
    profile = env.get("profile") or {}
    brand = (profile.get("public_brand") or {}).get("value")
    status = (profile.get("status") or {}).get("value") or {}
    badges = [f'<span class="chip">Org. no. {esc(env["organisation_number"])}</span>']
    if profile.get("legal_form"):
        badges.append(f'<span class="chip">{esc((profile["legal_form"]["value"] or {}).get("description"))}</span>')
    if status.get("bankrupt"):
        badges.append('<span class="chip bad">Bankrupt</span>')
    if status.get("under_liquidation"):
        badges.append('<span class="chip bad">Under liquidation</span>')
    site = (profile.get("official_website") or {}).get("value")
    if site:
        badges.append(f'<span class="chip ok">🌐 <a href="{esc(site)}" rel="noopener nofollow" target="_blank">{esc(site.replace("https://", "").rstrip("/"))}</a></span>')
    summary = env.get("summary") or {}
    sentences = "".join(
        f'<p>{esc(s["text"])} ' + "".join(f'<a class="cite" href="#{esc(cid)}">[{cite_index[cid]}]</a>' for cid in sorted((c for c in s.get("claim_ids", []) if c in by_id), key=lambda c: cite_index[c])[:8]) + "</p>"
        for s in summary.get("sentences", []) if s.get("kind") not in ("risk",))
    risks = "".join(f'<span class="chip bad">{esc(r["text"])}</span>' for r in summary.get("risk_flags", []))
    unknowns = "".join(f"<li>{esc(u)}</li>" for u in summary.get("unknowns", []))
    families = "".join(f'<span class="chip {"ok" if f["state"] == "available" else "warn" if f["state"] in ("ambiguous", "blocked", "failed") else ""}" title="{esc(f.get("reason") or "")}">{esc(FAMILY_LABEL.get(k, k))}: {esc(STATE_LABEL.get(f["state"], f["state"]))}</span>'
                       for k, f in env.get("field_families", {}).items())

    fin_rows = ""
    fin = profile.get("financials") or []
    if fin:
        metrics = [("revenue", "Revenue"), ("operating_result", "Operating result"), ("annual_result", "Annual result"), ("payroll_expenses", "Payroll"),
                   ("total_assets", "Total assets"), ("equity", "Equity"), ("total_debt", "Debt"), ("cash_and_bank", "Cash")]
        head = "".join(f'<th class="num">{esc(r["period_end"][:4])}</th>' for r in fin)
        body = ""
        for key, label in metrics:
            cells = ""
            for r in fin:
                if r.get(key) is None:
                    cells += '<td class="num muted">—</td>'
                else:
                    cid = r["claim_ids"].get(key)
                    cells += f'<td class="num"><a href="#{esc(cid)}" title="source">{r[key]:,.0f}</a></td>'.replace(",", " ")
            body += f"<tr><th>{label}</th>{cells}</tr>"
        currency = fin[0].get("currency") or "NOK"
        fin_rows = f'<div class="tablewrap"><table><tr><th>{esc(currency)}, company accounts</th>{head}</tr>{body}</table></div><p class="small muted">Figures as filed in Regnskapsregisteret; “—” means the line item was not reported (never shown as zero).</p>'

    sections_html = ""
    for section, title in SECTION_TITLES.items():
        rows = []
        for claim in claims:
            if claim["section"] != section:
                continue
            state = claim["availability"]
            evs = "".join(_evidence_html(evidence[e]) for e in claim.get("evidence_ids", []) if e in evidence)
            value = _value_text(claim) if state == "available" else STATE_LABEL.get(state, state)
            meta = []
            if claim.get("reporting_period"):
                meta.append(f"period {claim['reporting_period']}")
            if claim.get("effective_date"):
                meta.append(f"effective {claim['effective_date']}")
            if claim.get("first_observed_at"):
                meta.append(f"first seen {claim['first_observed_at'][:10]}")
            note = f'<div class="small muted">{esc(claim.get("note"))}</div>' if claim.get("note") else ""
            ident = f'<div class="small muted">identity: {esc(claim.get("identity_basis"))}</div>' if claim.get("identity_basis") and section == "web_presence" else ""
            rows.append(f'<tr class="claimrow" id="{esc(claim["id"])}"><td class="small muted">[{cite_index[claim["id"]]}]</td><td>{esc(claim["field"].replace("_", " "))}</td>'
                        f'<td>{"<span class=\"chip warn\">" + esc(value) + "</span>" if state != "available" else _link_value(value)}{note}{ident}'
                        f'<div class="small muted">{esc(" · ".join(meta))}</div>'
                        f'{"<details><summary>source & evidence (" + str(len(claim.get("evidence_ids", []))) + ")</summary>" + evs + "</details>" if evs else ""}</td></tr>')
        sec = env["sections"].get(section, {})
        extra = fin_rows if section == "financials" else ""
        sections_html += (f'<section class="block card" id="s-{section}"><h2>{esc(title)} <span class="badge">{esc(STATE_LABEL.get(sec.get("availability"), sec.get("availability")))}</span></h2>{extra}'
                          f'<div class="tablewrap"><table><tr><th class="hide-sm">#</th><th>Field</th><th>Value and source</th></tr>{"".join(rows) or "<tr><td colspan=3 class=muted>No claims.</td></tr>"}</table></div></section>')
    changes = env.get("changes") or []
    refresh = env.get("refresh") or {}
    change_html = "".join(f'<li><b>{esc(c["change_type"])}</b>{" (material)" if c.get("material") else ""}: {esc(c["description"])}' +
                          (f' <a class="cite" href="#{esc(c["claim_id"])}">[{cite_index.get(c["claim_id"], "?")}]</a>' if c.get("claim_id") in cite_index else "") + "</li>" for c in changes[:60])
    if refresh.get("first_run", True):
        change_html = "<li>First observation of this company by this agent; future runs will list changes against this snapshot.</li>"
    elif not changes:
        change_html = f"<li>No changes since run {esc(refresh.get('previous_run_id'))}.</li>"
    rejected = env.get("identity_candidates") or []
    rejected_html = "".join(f"<li>{esc(r.get('type'))} {esc(r.get('url'))} — {esc(r.get('verdict'))}{(': ' + esc('; '.join(r.get('reasons') or []))) if r.get('reasons') else ''}</li>" for r in rejected[:25])
    run = env.get("run") or {}
    body = f"""
<p class="small"><a href="../index.html">← All companies</a></p>
<h1 style="margin:.2em 0">{esc(name)}</h1>
{f'<div class="muted">Public brand: {esc(brand)}</div>' if brand else ''}
<div style="margin:8px 0">{''.join(badges)}</div>
<nav class="toc">{''.join(f'<a href="#s-{k}">{esc(v)}</a>' for k, v in SECTION_TITLES.items())}<a href="#s-changes">Changes</a><a href="#s-unknowns">Unknowns</a></nav>
<section class="block card summary"><h2>Summary</h2>{sentences or '<p class="muted">No supported summary.</p>'}{('<div>' + risks + '</div>') if risks else ''}
<p class="small muted">Generated only from the cited claims below ({esc(summary.get('method'))}). Bracketed numbers jump to the claim and its evidence.</p></section>
<section class="block card"><h2>Coverage</h2><div>{families}</div></section>
{sections_html}
<section class="block card" id="s-changes"><h2>What changed</h2><ul>{change_html}</ul></section>
<section class="block card" id="s-unknowns"><h2>What remains unknown</h2><ul>{unknowns or '<li>Nothing flagged.</li>'}</ul>
{f'<details><summary>Candidates checked during identity resolution ({len(rejected)})</summary><ul class="small">{rejected_html}</ul></details>' if rejected else ''}</section>
<p class="small muted">Run {esc(run.get('run_id'))} · completed {esc(run.get('completed_at'))} · {esc(env.get('operations', {}).get('requests'))} requests · agent {esc(run.get('agent'))} {esc(run.get('agent_version'))}</p>
"""
    return _page(f"{name} · Signalpost", body, root="../")


def index_record(env: dict[str, Any]) -> dict[str, Any]:
    p = env.get("profile") or {}
    fin = (p.get("financials") or [{}])[0] if p.get("financials") else {}
    leaders = p.get("leadership") or []
    ceo = next((l["value"]["name"] for l in leaders if l["value"].get("role_code") == "DAGL"), None)
    chair = next((l["value"]["name"] for l in leaders if l["value"].get("role_code") == "LEDE"), None)
    families = env.get("field_families") or {}
    fam = [[FAMILY_LABEL.get(k, k), v["state"] == "available"] for k, v in families.items()]
    v = lambda key: (p.get(key) or {}).get("value") if isinstance(p.get(key), dict) else None
    industry = v("industry")
    form = v("legal_form")
    status = v("status") or {}
    risk = [label for flag, label in (("bankrupt", "bankrupt"), ("under_liquidation", "liquidation"), ("under_forced_dissolution", "forced dissolution")) if status.get(flag)]
    name = env.get("legal_name") or env["organisation_number"]
    record = {
        "org": env["organisation_number"], "name": name, "brand": v("public_brand"),
        "form": (form or {}).get("code") if isinstance(form, dict) else form, "municipality": (v("municipality") or "").title(),
        "industry": (industry or {}).get("description") if isinstance(industry, dict) else None, "founded": v("founded_date"),
        "employees": v("registered_employees"), "website": v("official_website"),
        "revenue": fin.get("revenue"), "result": fin.get("annual_result"), "equity": fin.get("equity"),
        "currency": fin.get("currency"), "period": (fin.get("period_end") or "")[:4] or None,
        "ceo": ceo, "chair": chair, "jobs": len(p.get("job_postings") or []), "news": len(p.get("company_news") or []),
        "socials": len(p.get("social_profiles") or []), "workplaces": len(p.get("workplaces") or []),
        "families": fam, "score": sum(1 for _, on in fam if on), "risk": risk,
    }
    record["flags"] = {"website": bool(record["website"]), "jobs": record["jobs"] > 0, "accounts": record["revenue"] is not None,
                       "news": record["news"] > 0, "socials": record["socials"] > 0}
    record["search"] = " ".join(str(x) for x in (name, record["org"], record["brand"], record["municipality"], record["industry"], record["website"], ceo, chair) if x).lower()
    return record


def build_site(envelopes: list[dict[str, Any]], out: Path, report: dict[str, Any]) -> None:
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "c").mkdir(parents=True, exist_ok=True)
    (out / "assets" / "style.css").write_text(CSS, encoding="utf-8")
    (out / "assets" / "app.js").write_text(APP_JS, encoding="utf-8")
    records = [index_record(env) for env in envelopes]
    (out / "assets" / "data.js").write_text("window.SP_DATA=" + json.dumps(records, ensure_ascii=False, separators=(",", ":")) + ";", encoding="utf-8")
    for env in envelopes:
        (out / "c" / f"{env['organisation_number']}.html").write_text(company_page(env), encoding="utf-8")
    coverage = report.get("coverage") or {}
    total = len(envelopes) or 1
    stats = "".join(f'<div class="card stat"><b>{v["companies_available"]}</b><span class="small muted">{esc(FAMILY_LABEL.get(k, k))} · {v["companies_available"] * 100 // total}%</span></div>'
                    for k, v in coverage.items())
    body = f"""
<h1 style="margin:.3em 0">Company directory</h1>
<p class="muted">{len(envelopes)} companies · run {esc(report.get('run_id'))} · {esc(report.get('completed_at'))} · {esc(report.get('claims_published'))} sourced claims. Each profile cites the official register, the verified company website or the job board for every fact; missing data stays marked as missing.</p>
<div class="grid stats">{stats}</div>
<div class="controls"><input id="q" type="search" placeholder="Search name, org. number, municipality, industry, person… (press /)" aria-label="Search companies">
<select id="sort" aria-label="Sort"><option value="score">Most information</option><option value="name">Name A–Z</option><option value="revenue">Revenue</option><option value="jobs">Active jobs</option></select></div>
<div class="controls"><label class="filter"><input type="checkbox" data-f="website"> Website</label><label class="filter"><input type="checkbox" data-f="accounts"> Accounts</label><label class="filter"><input type="checkbox" data-f="jobs"> Hiring</label><label class="filter"><input type="checkbox" data-f="news"> News</label><label class="filter"><input type="checkbox" data-f="socials"> Social profiles</label><span id="count" class="small muted"></span></div>
<div id="list" class="list"></div>
<div id="compare" aria-live="polite"></div>
<script src="assets/data.js"></script><script src="assets/app.js"></script>
"""
    (out / "index.html").write_text(_page("Signalpost · company directory", body), encoding="utf-8")
