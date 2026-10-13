"""Official Norwegian registers (Brønnøysundregistrene, NLOD 2.0): identity anchor and official facts."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from .claims import Collector
from .fetch import Fetcher, Response

ENTITY = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
ROLES = ENTITY + "/roller"
SUBUNITS = "https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=200"
GROUP = "https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{org}"
UPDATES = "https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter?organisasjonsnummer={org}&dato={since}&size=100"
ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap/{org}"
ACCOUNT_YEARS = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/aar"
ACCOUNT_COPY = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/{year}"
REGISTRY_PAGE = "https://virksomhet.brreg.no/nb/oppslag/enheter/{org}"

OFFICIAL = "official_registry"
ACCOUNTS_CLASS = "official_annual_accounts"
LEGAL = "legal_identity"
FIN = "financials"
LEAD = "leadership_workplaces"
ACT = "hiring_activity"


def _get(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def format_address(address: dict[str, Any] | None) -> str | None:
    if not isinstance(address, dict):
        return None
    street = ", ".join(part for part in address.get("adresse") or [] if part)
    town = " ".join(part for part in [address.get("postnummer"), address.get("poststed")] if part)
    country = address.get("land") if address.get("landkode") not in (None, "NO") else None
    text = ", ".join(part for part in [street, town, country] if part)
    return text or None


def _mod_state(response: Response) -> str:
    if response.blocked_reason:
        return "blocked"
    if response.status in (404, 410):
        return "not_available"
    if response.status == 200:
        return "available"
    return "failed"


class Registry:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher

    # ---------------------------------------------------------------- entity
    async def entity(self, col: Collector, bulk_row: dict[str, Any] | None = None) -> dict[str, Any] | None:
        org = col.organisation_number
        response, body = await self.fetcher.get_json(ENTITY.format(org=org))
        if response.status == 200 and isinstance(body, dict):
            self._entity_claims(col, response, body)
            col.module("registry_entity", "available")
            return body
        if response.status in (404, 410):
            ev = col.add_evidence(response, source_class=OFFICIAL, method="brreg_entity_api_v1", span=f"HTTP {response.status}")
            col.module("registry_entity", "not_available", note=f"Entity API returned HTTP {response.status}; entity may be deleted or not registered")
            col.absent(LEGAL, "legal_name", "not_available", source_class=OFFICIAL, evidence_ids=[ev],
                       note=f"Brreg entity API returned HTTP {response.status}")
        else:
            col.module("registry_entity", "failed", note=response.error or f"HTTP {response.status}")
            col.error("registry_entity", response.error or f"HTTP {response.status}", response.url)
        if bulk_row:
            self._bulk_fallback(col, bulk_row)
            return {"navn": bulk_row.get("name"), "organisasjonsnummer": org, "_from_bulk": True,
                    "hjemmeside": bulk_row.get("website") or None,
                    "organisasjonsform": {"kode": bulk_row.get("legal_form")},
                    "forretningsadresse": {"kommune": bulk_row.get("municipality")}}
        return None

    def _bulk_fallback(self, col: Collector, row: dict[str, Any]) -> None:
        ev = col.add_evidence(None, source_class=OFFICIAL, method="frozen_registry_snapshot_row",
                              url="https://builderr.ai/signalpost-company-universe-2025.jsonl.gz",
                              locator=f"organisation_number={col.organisation_number}", span=row.get("name"))
        col.add(LEGAL, "legal_name", row.get("name"), source_class=OFFICIAL, evidence_ids=[ev], identity="organisation_number",
                note="From the frozen registry snapshot because the live entity API was unavailable")
        col.add(LEGAL, "legal_form", row.get("legal_form"), source_class=OFFICIAL, evidence_ids=[ev])
        col.add(LEGAL, "municipality", row.get("municipality"), source_class=OFFICIAL, evidence_ids=[ev])
        if row.get("industry_code"):
            col.add(LEGAL, "industry", {"code": row.get("industry_code"), "description": row.get("industry_label")},
                    source_class=OFFICIAL, evidence_ids=[ev], key_suffix="primary")

    def _entity_claims(self, col: Collector, response: Response, body: dict[str, Any]) -> None:
        method = "brreg_entity_api_v1"

        def ev(pointer: str, span: Any) -> str:
            return col.add_evidence(response, source_class=OFFICIAL, method=method, locator=f"json:{pointer}",
                                    span=str(span) if span is not None else None)

        def add(field: str, value: Any, pointer: str, **kwargs: Any) -> None:
            if value is None or value == "" or value == []:
                return
            col.add(LEGAL, field, value, source_class=OFFICIAL, evidence_ids=[ev(pointer, value)], identity="organisation_number", **kwargs)

        add("organisation_number", body.get("organisasjonsnummer"), "/organisasjonsnummer")
        add("legal_name", body.get("navn"), "/navn")
        form = body.get("organisasjonsform") or {}
        add("legal_form", {"code": form.get("kode"), "description": form.get("beskrivelse")} if form.get("kode") else None, "/organisasjonsform")
        add("registered_date", body.get("registreringsdatoEnhetsregisteret"), "/registreringsdatoEnhetsregisteret")
        add("founded_date", body.get("stiftelsesdato"), "/stiftelsesdato")
        add("business_registry_date", body.get("registreringsdatoForetaksregisteret"), "/registreringsdatoForetaksregisteret")
        for index, key in enumerate(("naeringskode1", "naeringskode2", "naeringskode3"), 1):
            code = body.get(key) or {}
            if code.get("kode"):
                add("industry", {"code": code.get("kode"), "description": code.get("beskrivelse"), "rank": index}, f"/{key}", key_suffix=str(index))
        sector = body.get("institusjonellSektorkode") or {}
        add("institutional_sector", {"code": sector.get("kode"), "description": sector.get("beskrivelse")} if sector.get("kode") else None, "/institusjonellSektorkode")
        business = format_address(body.get("forretningsadresse"))
        add("business_address", business, "/forretningsadresse")
        add("postal_address", format_address(body.get("postadresse")), "/postadresse")
        municipality = _get(body, "forretningsadresse", "kommune") or _get(body, "postadresse", "kommune")
        add("municipality", municipality, "/forretningsadresse/kommune")
        add("phone", body.get("telefon"), "/telefon")
        add("mobile", body.get("mobil"), "/mobil")
        add("email", body.get("epostadresse"), "/epostadresse")
        add("registry_website", body.get("hjemmeside"), "/hjemmeside")
        if body.get("harRegistrertAntallAnsatte") and body.get("antallAnsatte") is not None:
            add("registered_employees", body.get("antallAnsatte"), "/antallAnsatte",
                effective=body.get("registreringsdatoAntallAnsatteNAVAaregisteret") or body.get("registreringsdatoAntallAnsatteEnhetsregisteret"))
        else:
            col.absent(LEGAL, "registered_employees", "not_available", source_class=OFFICIAL,
                       evidence_ids=[ev("/harRegistrertAntallAnsatte", body.get("harRegistrertAntallAnsatte"))],
                       note="The registry has no registered employee count for this entity; not treated as zero")
        capital = body.get("kapital") or {}
        if capital.get("belop") is not None:
            add("share_capital", {"amount": capital.get("belop"), "currency": capital.get("valuta"), "type": capital.get("type"),
                                  "shares": capital.get("antallAksjer")}, "/kapital", effective=capital.get("innfortDato"))
        purpose = " ".join(body.get("vedtektsfestetFormaal") or []).strip()
        add("statutory_purpose", purpose or None, "/vedtektsfestetFormaal")
        activity = " ".join(body.get("aktivitet") or []).strip()
        add("registered_activity", activity or None, "/aktivitet")
        add("articles_date", body.get("vedtektsdato"), "/vedtektsdato")
        add("language_form", body.get("maalform"), "/maalform")
        status = {
            "bankrupt": bool(body.get("konkurs")),
            "under_liquidation": bool(body.get("underAvvikling")),
            "under_forced_dissolution": bool(body.get("underTvangsavviklingEllerTvangsopplosning")),
            "deleted_date": body.get("slettedato"),
        }
        add("status", status, "/konkurs")
        registers = {
            "business_register": body.get("registrertIForetaksregisteret"),
            "vat_register": body.get("registrertIMvaregisteret"),
            "voluntary_register": body.get("registrertIFrivillighetsregisteret"),
            "foundation_register": body.get("registrertIStiftelsesregisteret"),
        }
        add("registers", {k: v for k, v in registers.items() if v is not None}, "/registrertIMvaregisteret")
        if body.get("registreringsdatoMerverdiavgiftsregisteret"):
            add("vat_registered_date", body.get("registreringsdatoMerverdiavgiftsregisteret"), "/registreringsdatoMerverdiavgiftsregisteret")
        add("latest_submitted_accounts_year", body.get("sisteInnsendteAarsregnskap"), "/sisteInnsendteAarsregnskap")
        add("in_group", body.get("erIKonsern"), "/erIKonsern")
        if body.get("overordnetEnhet"):
            add("parent_unit", body.get("overordnetEnhet"), "/overordnetEnhet")
        for index, item in enumerate(body.get("historiskeNavn") or []):
            add("former_name", {"name": item.get("navn"), "from": (item.get("fraDato") or "")[:10] or None,
                                "to": (item.get("tilDato") or "")[:10] or None}, f"/historiskeNavn/{index}",
                key_suffix=str(index), effective=(item.get("tilDato") or "")[:10] or None)
        col.add(LEGAL, "registry_profile_url", REGISTRY_PAGE.format(org=col.organisation_number), source_class=OFFICIAL,
                evidence_ids=[ev("/organisasjonsnummer", body.get("organisasjonsnummer"))])

    # ---------------------------------------------------------------- roles
    async def roles(self, col: Collector) -> list[dict[str, Any]]:
        response, body = await self.fetcher.get_json(ROLES.format(org=col.organisation_number))
        state = _mod_state(response)
        roles: list[dict[str, Any]] = []
        if state != "available" or not isinstance(body, dict):
            col.module("registry_roles", state, note=response.error or f"HTTP {response.status}")
            if state == "not_available":
                ev = col.add_evidence(response, source_class=OFFICIAL, method="brreg_roles_api_v1", span=f"HTTP {response.status}")
                col.absent(LEAD, "role", "not_available", source_class=OFFICIAL, evidence_ids=[ev], note="No public roles returned")
            return roles
        for g_index, group in enumerate(body.get("rollegrupper") or []):
            changed = group.get("sistEndret")
            for r_index, item in enumerate(group.get("roller") or []):
                if item.get("avregistrert") or item.get("fratraadt"):
                    continue
                person = item.get("person") or {}
                if person.get("erDoed"):
                    continue
                name_parts = person.get("navn") or {}
                entity = item.get("enhet") or {}
                name = " ".join(filter(None, [name_parts.get("fornavn"), name_parts.get("mellomnavn"), name_parts.get("etternavn")]))
                entity_name = " ".join(entity.get("navn") or []) if isinstance(entity.get("navn"), list) else entity.get("navn")
                record = {
                    "name": name or entity_name,
                    "kind": "person" if name else "organisation",
                    "organisation_number": entity.get("organisasjonsnummer"),
                    "role_code": _get(item, "type", "kode"),
                    "role": _get(item, "type", "beskrivelse"),
                    "group_code": _get(group, "type", "kode"),
                    "group": _get(group, "type", "beskrivelse"),
                    "order": item.get("rekkefolge"),
                }
                if not record["name"]:
                    continue
                roles.append({**record, "last_changed": changed})
                ev = col.add_evidence(response, source_class=OFFICIAL, method="brreg_roles_api_v1",
                                      locator=f"json:/rollegrupper/{g_index}/roller/{r_index}",
                                      span=f"{record['role']}: {record['name']}")
                col.add(LEAD, "role", record, source_class=OFFICIAL, evidence_ids=[ev], effective=changed,
                        key_suffix=f"{record['role_code']}.{record['name']}", identity="organisation_number")
        if roles:
            col.module("registry_roles", "available", count=len(roles))
        else:
            ev = col.add_evidence(response, source_class=OFFICIAL, method="brreg_roles_api_v1", span="rollegrupper empty")
            col.absent(LEAD, "role", "not_available", source_class=OFFICIAL, evidence_ids=[ev], note="Registry lists no active roles")
            col.module("registry_roles", "not_available")
        return roles

    # ---------------------------------------------------------------- subunits
    async def subunits(self, col: Collector) -> list[dict[str, Any]]:
        response, body = await self.fetcher.get_json(SUBUNITS.format(org=col.organisation_number))
        state = _mod_state(response)
        units: list[dict[str, Any]] = []
        if state != "available" or not isinstance(body, dict):
            col.module("registry_workplaces", state, note=response.error or f"HTTP {response.status}")
            return units
        for index, item in enumerate(_get(body, "_embedded", "underenheter") or []):
            if item.get("nedleggelsesdato"):
                continue
            unit = {
                "organisation_number": item.get("organisasjonsnummer"),
                "name": item.get("navn"),
                "address": format_address(item.get("beliggenhetsadresse")) or format_address(item.get("postadresse")),
                "municipality": _get(item, "beliggenhetsadresse", "kommune"),
                "industry": _get(item, "naeringskode1", "beskrivelse"),
                "employees": item.get("antallAnsatte") if item.get("harRegistrertAntallAnsatte") else None,
                "started": item.get("oppstartsdato"),
                "website": item.get("hjemmeside"),
            }
            units.append(unit)
            ev = col.add_evidence(response, source_class=OFFICIAL, method="brreg_subunits_api_v1",
                                  locator=f"json:/_embedded/underenheter/{index}", span=f"{unit['name']} — {unit['address']}")
            col.add(LEAD, "registered_workplace", {k: v for k, v in unit.items() if v is not None}, source_class=OFFICIAL,
                    evidence_ids=[ev], key_suffix=str(unit["organisation_number"]), effective=item.get("registreringsdatoIEnhetsregisteret"),
                    identity="organisation_number (subunit of parent)")
        total = _get(body, "page", "totalElements")
        if units:
            col.module("registry_workplaces", "available", count=len(units), total_reported=total)
        else:
            ev = col.add_evidence(response, source_class=OFFICIAL, method="brreg_subunits_api_v1", span="no active subunits")
            col.absent(LEAD, "registered_workplace", "not_available", source_class=OFFICIAL, evidence_ids=[ev],
                       note="No active registered subunits (workplaces) in Enhetsregisteret")
            col.module("registry_workplaces", "not_available")
        return units

    # ---------------------------------------------------------------- group
    async def group(self, col: Collector) -> None:
        response, body = await self.fetcher.get_json(GROUP.format(org=col.organisation_number))
        state = _mod_state(response)
        if state != "available" or not isinstance(body, dict):
            col.module("registry_group", "not_available" if state == "not_available" else state, note=response.error)
            return
        org = col.organisation_number
        nodes: list[dict[str, Any]] = []

        def walk(node: Any, path: str) -> None:
            if isinstance(node, dict):
                if node.get("organisasjonsnummer"):
                    nodes.append({**{k: v for k, v in node.items() if k != "children"}, "_path": path})
                for i, child in enumerate(node.get("children") or []):
                    walk(child, f"{path}/children/{i}")
            elif isinstance(node, list):
                for i, child in enumerate(node):
                    walk(child, f"{path}/{i}")

        walk(body, "")
        added = 0
        for node in nodes:
            relation = None
            if node.get("organisasjonsnummer") == org and node.get("parentOrganisasjonsnummer"):
                relation = ("parent", node.get("parentOrganisasjonsnummer"), node.get("parentNavn"))
            elif node.get("parentOrganisasjonsnummer") == org:
                relation = ("subsidiary", node.get("organisasjonsnummer"), node.get("navn"))
            if not relation or added >= 60:
                continue
            kind, other_org, other_name = relation
            ev = col.add_evidence(response, source_class=OFFICIAL, method="brreg_group_structure_api_v1",
                                  locator=f"json:{node['_path']}", span=f"{other_name} ({other_org})")
            col.add(LEAD, f"group_{kind}", {"organisation_number": other_org, "name": other_name,
                                           "relation": _get(node, "knytningsform", "beskrivelse"), "share": node.get("grunnlag")},
                    source_class=OFFICIAL, evidence_ids=[ev], key_suffix=str(other_org), effective=node.get("dato"),
                    note="Official group relationship; group facts are not merged into this entity")
            added += 1
        col.module("registry_group", "available" if added else "not_available", relations=added)

    # ---------------------------------------------------------------- updates (dated official activity)
    async def updates(self, col: Collector, years: int = 3) -> None:
        since = (datetime.now(timezone.utc) - timedelta(days=365 * years)).strftime("%Y-%m-%dT00:00:00.000Z")
        response, body = await self.fetcher.get_json(UPDATES.format(org=col.organisation_number, since=since))
        state = _mod_state(response)
        if state != "available" or not isinstance(body, dict):
            col.module("registry_updates", state, note=response.error)
            return
        events = _get(body, "_embedded", "oppdaterteEnheter") or []
        # Several technical updates can share one day; keep one dated event per day and type.
        seen: set[tuple[str, str]] = set()
        kept = 0
        for index in range(len(events) - 1, -1, -1):
            item = events[index]
            day = str(item.get("dato") or "")[:10]
            kind = item.get("endringstype") or "Ukjent"
            if not day or (day, kind) in seen:
                continue
            seen.add((day, kind))
            ev = col.add_evidence(response, source_class="official_registry_updates", method="brreg_update_stream_v1",
                                  locator=f"json:/_embedded/oppdaterteEnheter/{index}", span=f"{day} {kind}")
            col.add(ACT, "registry_update", {"date": day, "change_type": kind, "update_id": item.get("oppdateringsid")},
                    source_class="official_registry_updates", evidence_ids=[ev], effective=day, key_suffix=f"{day}.{kind}",
                    note="Dated change event in Enhetsregisteret; the open stream does not state which field changed")
            kept += 1
            if kept >= 15:
                break
        col.module("registry_updates", "available" if kept else "not_available", events=kept)

    # ---------------------------------------------------------------- accounts
    async def accounts(self, col: Collector) -> list[dict[str, Any]]:
        response, body = await self.fetcher.get_json(ACCOUNTS.format(org=col.organisation_number))
        state = _mod_state(response)
        records: list[dict[str, Any]] = []
        if state != "available" or not isinstance(body, list) or not body:
            note = "Regnskapsregisteret returned no normalized accounts" if state in ("available", "not_available") else (response.error or f"HTTP {response.status}")
            final_state = "not_available" if state in ("available", "not_available") else state
            ev = col.add_evidence(response, source_class=ACCOUNTS_CLASS, method="brreg_accounts_api_v1", span=note)
            col.absent(FIN, "annual_accounts", final_state, source_class=ACCOUNTS_CLASS, evidence_ids=[ev], note=note)
            col.module("annual_accounts", final_state, note=note)
            return records
        ordered = sorted(enumerate(body), key=lambda pair: str(_get(pair[1], "regnskapsperiode", "tilDato") or ""), reverse=True)
        for index, item in ordered:
            start = _get(item, "regnskapsperiode", "fraDato")
            end = _get(item, "regnskapsperiode", "tilDato")
            period = f"{start}/{end}" if start and end else (end or start)
            account_type = (item.get("regnskapstype") or "SELSKAP").upper()
            metrics = {
                "revenue": _get(item, "resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
                "sales_revenue": _get(item, "resultatregnskapResultat", "driftsresultat", "driftsinntekter", "salgsinntekter"),
                "operating_expenses": _get(item, "resultatregnskapResultat", "driftsresultat", "driftskostnad", "sumDriftskostnad"),
                "payroll_expenses": _get(item, "resultatregnskapResultat", "driftsresultat", "driftskostnad", "loennskostnad"),
                "operating_result": _get(item, "resultatregnskapResultat", "driftsresultat", "driftsresultat"),
                "net_financial_items": _get(item, "resultatregnskapResultat", "finansresultat", "nettoFinans"),
                "profit_before_tax": _get(item, "resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad"),
                "annual_result": _get(item, "resultatregnskapResultat", "aarsresultat"),
                "total_assets": _get(item, "eiendeler", "sumEiendeler"),
                "fixed_assets": _get(item, "eiendeler", "anleggsmidler", "sumAnleggsmidler"),
                "current_assets": _get(item, "eiendeler", "omloepsmidler", "sumOmloepsmidler"),
                "cash_and_bank": _get(item, "eiendeler", "sumBankinnskuddOgKontanter"),
                "equity": _get(item, "egenkapitalGjeld", "egenkapital", "sumEgenkapital"),
                "total_debt": _get(item, "egenkapitalGjeld", "gjeldOversikt", "sumGjeld"),
                "short_term_debt": _get(item, "egenkapitalGjeld", "gjeldOversikt", "kortsiktigGjeld", "sumKortsiktigGjeld"),
                "long_term_debt": _get(item, "egenkapitalGjeld", "gjeldOversikt", "langsiktigGjeld", "sumLangsiktigGjeld"),
            }
            pointers = {
                "revenue": "resultatregnskapResultat/driftsresultat/driftsinntekter/sumDriftsinntekter",
                "sales_revenue": "resultatregnskapResultat/driftsresultat/driftsinntekter/salgsinntekter",
                "operating_expenses": "resultatregnskapResultat/driftsresultat/driftskostnad/sumDriftskostnad",
                "payroll_expenses": "resultatregnskapResultat/driftsresultat/driftskostnad/loennskostnad",
                "operating_result": "resultatregnskapResultat/driftsresultat/driftsresultat",
                "net_financial_items": "resultatregnskapResultat/finansresultat/nettoFinans",
                "profit_before_tax": "resultatregnskapResultat/ordinaertResultatFoerSkattekostnad",
                "annual_result": "resultatregnskapResultat/aarsresultat",
                "total_assets": "eiendeler/sumEiendeler",
                "fixed_assets": "eiendeler/anleggsmidler/sumAnleggsmidler",
                "current_assets": "eiendeler/omloepsmidler/sumOmloepsmidler",
                "cash_and_bank": "eiendeler/sumBankinnskuddOgKontanter",
                "equity": "egenkapitalGjeld/egenkapital/sumEgenkapital",
                "total_debt": "egenkapitalGjeld/gjeldOversikt/sumGjeld",
                "short_term_debt": "egenkapitalGjeld/gjeldOversikt/kortsiktigGjeld/sumKortsiktigGjeld",
                "long_term_debt": "egenkapitalGjeld/gjeldOversikt/langsiktigGjeld/sumLangsiktigGjeld",
            }
            record = {"account_type": account_type, "period": period, "period_end": end, "currency": item.get("valuta"),
                      "audited": None if _get(item, "revisjon", "ikkeRevidertAarsregnskap") is None else not _get(item, "revisjon", "ikkeRevidertAarsregnskap"),
                      "audit_opted_out": _get(item, "revisjon", "fravalgRevisjon"),
                      "small_company_rules": _get(item, "regnkapsprinsipper", "smaaForetak"),
                      "accounting_rules": _get(item, "regnkapsprinsipper", "regnskapsregler"),
                      "liquidation_accounts": item.get("avviklingsregnskap"),
                      "metrics": {k: v for k, v in metrics.items() if v is not None}}
            records.append(record)
            suffix = f"{account_type}.{end}"
            for metric, value in metrics.items():
                if value is None:
                    continue  # never convert a missing line item into zero
                ev = col.add_evidence(response, source_class=ACCOUNTS_CLASS, method="brreg_accounts_api_v1",
                                      locator=f"json:/{index}/{pointers[metric]}", span=f"{metric}={value} {item.get('valuta') or ''}",
                                      period=period)
                col.add(FIN, metric, {"amount": value, "currency": item.get("valuta"), "account_type": account_type},
                        source_class=ACCOUNTS_CLASS, evidence_ids=[ev], period=period, key_suffix=suffix, identity="organisation_number")
            ev = col.add_evidence(response, source_class=ACCOUNTS_CLASS, method="brreg_accounts_api_v1",
                                  locator=f"json:/{index}/revisjon", span=f"audit flags {item.get('revisjon')}", period=period)
            col.add(FIN, "accounts_basis", {k: record[k] for k in ("account_type", "currency", "audited", "audit_opted_out", "small_company_rules", "accounting_rules", "liquidation_accounts")},
                    source_class=ACCOUNTS_CLASS, evidence_ids=[ev], period=period, key_suffix=suffix)
            if account_type == "SELSKAP" or not any(r.get("account_type") == "SELSKAP" and r.get("period_end") == end for r in records[:-1]):
                col.add(ACT, "annual_accounts_filed", {"period": period, "journal_number": item.get("journalnr")},
                        source_class=ACCOUNTS_CLASS, evidence_ids=[ev], effective=end, key_suffix=str(end))
        col.module("annual_accounts", "available", periods=len(records))
        return records

    async def filing_years(self, col: Collector) -> None:
        org = col.organisation_number
        response, body = await self.fetcher.get_json(ACCOUNT_YEARS.format(org=org), retries=1)
        state = _mod_state(response)
        if state != "available" or not isinstance(body, list):
            col.module("annual_account_copies", state if state != "available" else "not_available", note=response.error)
            return
        years = sorted({str(year) for year in body if str(year).isdigit()})
        if not years:
            col.module("annual_account_copies", "not_available")
            return
        ev = col.add_evidence(response, source_class=ACCOUNTS_CLASS, method="brreg_account_copy_years_v1", locator="json:/",
                              span=", ".join(years))
        col.add(FIN, "filed_account_years", years, source_class=ACCOUNTS_CLASS, evidence_ids=[ev], identity="organisation_number")
        for year in years[-5:]:
            col.add(FIN, "annual_account_copy", {"year": year, "url": ACCOUNT_COPY.format(org=org, year=year)},
                    source_class=ACCOUNTS_CLASS, evidence_ids=[ev], key_suffix=year, period=year)
        col.module("annual_account_copies", "available", years=len(years))

    async def run_all(self, col: Collector, bulk_row: dict[str, Any] | None, *, filing_years: bool = True) -> dict[str, Any]:
        entity = await self.entity(col, bulk_row)
        tasks = [self.roles(col), self.subunits(col), self.accounts(col), self.group(col), self.updates(col)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for name, result in zip(("roles", "subunits", "accounts", "group", "updates"), results):
            if isinstance(result, BaseException):
                col.error(f"registry_{name}", f"{type(result).__name__}: {result}")
                col.module(f"registry_{name}", "failed", note=str(result)[:200])
        roles = results[0] if isinstance(results[0], list) else []
        units = results[1] if isinstance(results[1], list) else []
        accounts = results[2] if isinstance(results[2], list) else []
        return {"entity": entity, "roles": roles, "subunits": units, "accounts": accounts}
