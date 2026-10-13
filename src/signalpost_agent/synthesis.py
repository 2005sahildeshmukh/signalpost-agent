"""Deterministic, citation-bound synthesis.

Every sentence is assembled only from published claims and carries the claim ids it relies on. Nothing
is inferred beyond simple arithmetic on filed numbers (year-over-year change), and that arithmetic is
labelled. Unknowns are listed explicitly instead of being smoothed over.
"""
from __future__ import annotations

from typing import Any

from .claims import Claim, Collector

LEGAL_FORM_EN = {
    "AS": "private limited company (AS)", "ASA": "public limited company (ASA)", "ENK": "sole proprietorship (ENK)",
    "ANS": "general partnership (ANS)", "DA": "partnership with shared liability (DA)", "NUF": "Norwegian-registered foreign company (NUF)",
    "SA": "cooperative (SA)", "STI": "foundation (STI)", "BRL": "housing cooperative (BRL)", "FLI": "association (FLI)",
    "ESEK": "jointly owned property (ESEK)", "KS": "limited partnership (KS)", "SAM": "co-ownership (SAM)", "VPFO": "securities fund (VPFO)",
    "BBL": "housing building cooperative (BBL)", "IKS": "inter-municipal company (IKS)", "SF": "state enterprise (SF)",
    "KF": "municipal enterprise (KF)", "PK": "pension fund (PK)", "SPA": "savings bank (SPA)", "GFS": "mutual insurance company (GFS)",
}

ROLE_ORDER = {"DAGL": 0, "LEDE": 1, "NEST": 2, "MEDL": 3, "VARA": 4, "REVI": 5, "REGN": 6, "DTPR": 3, "DTSO": 3, "KOMP": 1, "INNH": 0, "FFØR": 2}
ROLE_LABEL_EN = {"DAGL": "CEO (daglig leder)", "LEDE": "chair of the board", "NEST": "deputy chair", "MEDL": "board member",
                 "VARA": "deputy board member", "REVI": "auditor", "REGN": "accountant", "INNH": "owner", "KOMP": "general partner",
                 "DTPR": "partner", "DTSO": "partner", "FFØR": "manager (NUF)", "KONT": "contact person", "SAM": "joint owner"}


def money(amount: Any, currency: str | None = "NOK") -> str:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    sign = "-" if value < 0 else ""
    value = abs(value)
    cur = currency or "NOK"
    if value >= 1e9:
        return f"{cur} {sign}{value / 1e9:.2f} bn"
    if value >= 1e6:
        return f"{cur} {sign}{value / 1e6:.1f} m"
    if value >= 1e3:
        return f"{cur} {sign}{value / 1e3:.0f} k"
    return f"{cur} {sign}{value:.0f}"


def _first(col: Collector, prefix: str) -> Claim | None:
    values = col.values(prefix)
    return values[0] if values else None


def financial_series(col: Collector) -> dict[str, dict[str, Claim]]:
    """period_end -> metric -> claim, preferring company (SELSKAP) accounts over group (KONSERN)."""
    series: dict[str, dict[str, Claim]] = {}
    for claim in col.values("financials."):
        if not isinstance(claim.value, dict) or "amount" not in claim.value:
            continue
        if claim.value.get("account_type") not in (None, "SELSKAP"):
            continue
        end = (claim.reporting_period or "").split("/")[-1]
        series.setdefault(end, {})[claim.field] = claim
    return dict(sorted(series.items(), reverse=True))


def build_summary(col: Collector, changes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    sentences: list[dict[str, Any]] = []

    def say(text: str, claims: list[Claim | None], kind: str) -> None:
        ids = [c.id for c in claims if c is not None]
        if ids:
            sentences.append({"kind": kind, "text": text, "claim_ids": ids})

    name = _first(col, "legal_identity.legal_name")
    if not name:
        return {"text": "The organisation number could not be resolved in the official register, so no profile was published.",
                "sentences": [], "unknowns": ["legal identity"], "risk_flags": [], "what_changed": [], "method": "deterministic_template_v1"}
    form = _first(col, "legal_identity.legal_form")
    municipality = _first(col, "legal_identity.municipality")
    founded = _first(col, "legal_identity.founded_date") or _first(col, "legal_identity.registered_date")
    industry = _first(col, "legal_identity.industry.1")
    brand = _first(col, "legal_identity.public_brand")
    parts = [f"{name.value}"]
    if brand and str(brand.value).casefold() not in str(name.value).casefold():
        parts.append(f"(public brand “{brand.value}”)")
    text = " ".join(parts)
    form_label = "registered entity"
    if form and isinstance(form.value, dict):
        form_label = LEGAL_FORM_EN.get(form.value.get("code"), f"{str(form.value.get('description') or form.value.get('code')).lower()} ({form.value.get('code')})")
    text += f" is {'an' if form_label[0] in 'aeiou' else 'a'} {form_label}"
    if municipality:
        text += f" based in {str(municipality.value).title()}"
    if founded:
        text += f", founded {founded.value}" if founded.field == "founded_date" else f", registered {founded.value}"
    if industry and isinstance(industry.value, dict):
        text += f", registered in the industry “{industry.value.get('description')}” ({industry.value.get('code')})"
    say(text + ".", [name, form, municipality, founded, industry, brand], "identity")

    website = _first(col, "web_presence.official_website")
    description = _first(col, "web_presence.site_description")
    about = _first(col, "web_presence.about_text")
    purpose = _first(col, "legal_identity.statutory_purpose") or _first(col, "legal_identity.registered_activity")
    kb = _first(col, "web_presence.knowledge_base_description")
    if website and (description or about):
        snippet = str((description or about).value)
        snippet = snippet if len(snippet) <= 260 else snippet[:257].rsplit(" ", 1)[0] + "…"
        say(f"Its verified website ({website.value}) describes the business as: “{snippet}” (self-reported).", [website, description or about], "business")
    elif website:
        say(f"Its official website is {website.value}.", [website], "business")
    if purpose:
        snippet = str(purpose.value)
        snippet = snippet if len(snippet) <= 220 else snippet[:217].rsplit(" ", 1)[0] + "…"
        say(f"The register records its purpose/activity as: “{snippet}”.", [purpose], "business")
    if kb:
        say(f"Wikidata describes it as “{kb.value}”.", [kb], "business")

    series = financial_series(col)
    periods = list(series)
    if periods:
        latest = series[periods[0]]
        revenue = latest.get("revenue")
        result = latest.get("annual_result")
        operating = latest.get("operating_result")
        equity = latest.get("equity")
        currency = next((c.value.get("currency") for c in latest.values() if isinstance(c.value, dict)), "NOK")
        bits = []
        used: list[Claim | None] = []
        if revenue:
            bits.append(f"revenue of {money(revenue.value['amount'], currency)}")
            used.append(revenue)
            if len(periods) > 1 and series[periods[1]].get("revenue"):
                previous = series[periods[1]]["revenue"]
                try:
                    old = float(previous.value["amount"])
                    new = float(revenue.value["amount"])
                    if old:
                        change = (new - old) / abs(old) * 100
                        bits[-1] += f" ({change:+.0f}% vs {periods[1][:4]}, computed from filed figures)"
                        used.append(previous)
                except (TypeError, ValueError):
                    pass
        if operating:
            bits.append(f"an operating result of {money(operating.value['amount'], currency)}")
            used.append(operating)
        if result:
            bits.append(f"an annual result of {money(result.value['amount'], currency)}")
            used.append(result)
        if equity:
            bits.append(f"equity of {money(equity.value['amount'], currency)}")
            used.append(equity)
        if bits:
            say(f"Its latest filed company accounts (period ending {periods[0]}) show " + ", ".join(bits) + ".", used, "financials")
        if len(periods) > 1:
            history = [p[:4] for p in periods]
            say(f"Normalized accounts are available for {', '.join(sorted(history))}.", [c for p in periods for c in list(series[p].values())[:1]], "financials")
    filed = _first(col, "financials.filed_account_years")
    if filed and isinstance(filed.value, list) and filed.value:
        say(f"Annual-account copies are on file for {len(filed.value)} years ({filed.value[0]}–{filed.value[-1]}).", [filed], "financials")

    roles = sorted(col.values("leadership_workplaces.role."), key=lambda c: ROLE_ORDER.get(c.value.get("role_code"), 9))
    if roles:
        named = []
        used_roles = []
        counts: dict[str, int] = {}
        for claim in roles:
            code = claim.value.get("role_code")
            counts[code] = counts.get(code, 0) + 1
            if code in ("DAGL", "LEDE", "INNH", "KOMP", "REVI", "FFØR") and len(named) < 4:
                named.append(f"{claim.value.get('name')} ({ROLE_LABEL_EN.get(code, claim.value.get('role', '').lower())})")
                used_roles.append(claim)
        board = counts.get("MEDL", 0) + counts.get("LEDE", 0) + counts.get("NEST", 0)
        text = "Registered leadership: " + "; ".join(named) if named else "The register lists role holders"
        if board:
            text += f"; the board has {board} member{'s' if board != 1 else ''}"
        say(text + ".", used_roles or roles[:3], "leadership")
    confirmed = col.values("leadership_workplaces.person_on_company_site")
    if confirmed:
        say(f"{len(confirmed)} registered role holder(s) are also named on the company's own website.", confirmed, "leadership")
    employees = _first(col, "legal_identity.registered_employees")
    if employees:
        say(f"The register reports {employees.value} employees.", [employees], "workforce")
    units = col.values("leadership_workplaces.registered_workplace")
    if units:
        places = sorted({str(u.value.get("municipality") or "").title() for u in units if u.value.get("municipality")})
        say(f"It has {len(units)} registered workplace{'s' if len(units) != 1 else ''}" + (f" in {', '.join(places[:5])}" + (" and more" if len(places) > 5 else "") if places else "") + ".", units[:6], "workplaces")
    parent = _first(col, "leadership_workplaces.group_parent")
    if parent:
        say(f"Official group structure lists {parent.value.get('name')} ({parent.value.get('organisation_number')}) as its parent; group facts are kept separate.", [parent], "group")
    subs = col.values("leadership_workplaces.group_subsidiary")
    if subs:
        say(f"It is registered as parent of {len(subs)} subsidiar{'ies' if len(subs) != 1 else 'y'}.", subs[:5], "group")

    jobs = [c for c in col.values("hiring_activity.job_posting")]
    if jobs:
        latest_job = max(jobs, key=lambda c: str(c.value.get("published") or c.value.get("date_posted") or ""))
        say(f"It appears to be hiring: {len(jobs)} active job posting{'s' if len(jobs) != 1 else ''} verified by employer organisation number, most recently “{latest_job.value.get('title')}”"
            + (f" ({latest_job.value.get('published')})" if latest_job.value.get("published") else "") + ".", jobs, "hiring")
    else:
        no_jobs = next((c for c in col.claims.values() if c.key == "hiring_activity.job_posting" and c.availability == "not_available"), None)
        if no_jobs:
            say("No active job ads with its organisation number were found in NAV's public job feed at retrieval time.", [no_jobs], "hiring")
    careers = col.values("hiring_activity.careers_page") + col.values("hiring_activity.recruitment_channel")
    if careers:
        say("Its website links to a careers page or recruitment system.", careers[:3], "hiring")
    news = sorted(col.values("hiring_activity.company_news"), key=lambda c: c.value.get("date", ""), reverse=True)
    if news:
        say(f"Latest dated activity on its website: “{news[0].value.get('title')}” ({news[0].value.get('date')})" + (f", and {len(news) - 1} more item(s)" if len(news) > 1 else "") + ".", news[:5], "activity")
    socials = col.values("web_presence.social_profile")
    if socials:
        platforms = sorted({c.value.get("platform") for c in socials})
        say(f"Company-owned profiles verified on: {', '.join(platforms)}.", socials, "web")

    risk_flags = []
    status = _first(col, "legal_identity.status")
    if status and isinstance(status.value, dict):
        for flag, label in (("bankrupt", "registered as bankrupt"), ("under_liquidation", "under liquidation"), ("under_forced_dissolution", "under forced dissolution")):
            if status.value.get(flag):
                risk_flags.append({"flag": flag, "text": f"The register marks the entity as {label}.", "claim_ids": [status.id]})
    if periods:
        latest = series[periods[0]]
        if latest.get("equity") and _num(latest["equity"].value.get("amount")) is not None and _num(latest["equity"].value["amount"]) < 0:
            risk_flags.append({"flag": "negative_equity", "text": "Equity was negative in the latest filed accounts.", "claim_ids": [latest["equity"].id]})
        if latest.get("annual_result") and _num(latest["annual_result"].value.get("amount")) is not None and _num(latest["annual_result"].value["amount"]) < 0:
            risk_flags.append({"flag": "loss", "text": "The latest filed annual result was a loss.", "claim_ids": [latest["annual_result"].id]})
    for flag in risk_flags:
        sentences.append({"kind": "risk", "text": flag["text"], "claim_ids": flag["claim_ids"]})

    unknowns = []
    if not website:
        unknowns.append("No official website could be verified for this exact entity.")
    if not periods:
        unknowns.append("No normalized annual accounts were returned by Regnskapsregisteret.")
    if not employees:
        unknowns.append("The register has no employee count; this is not treated as zero.")
    if not jobs:
        unknowns.append("No verified active job postings.")
    if not news:
        unknowns.append("No dated company news found on a verified company site.")
    if not socials:
        unknowns.append("No company-owned social profiles verified.")
    what_changed = []
    for change in (changes or [])[:12]:
        if change.get("material"):
            what_changed.append(change.get("description"))
    if what_changed:
        sentences.append({"kind": "changes", "text": "Since the previous run: " + "; ".join(what_changed[:6]) + ".",
                          "claim_ids": [c.get("claim_id") for c in (changes or []) if c.get("claim_id")][:12]})
    elif changes is not None:
        sentences.append({"kind": "changes", "text": "No material changes since the previous run.", "claim_ids": []})
    return {
        "text": " ".join(s["text"] for s in sentences),
        "sentences": sentences,
        "risk_flags": risk_flags,
        "unknowns": unknowns,
        "what_changed": what_changed,
        "method": "deterministic_template_v1 (no language model; every sentence cites claim ids)",
    }


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
