"""Wikidata (CC0) lookups keyed by the Norwegian organisation-number property P2333.

The organisation number is the join key, so a returned item is an exact-entity match by construction.
One SPARQL query covers up to 200 organisations, so a 1,000-company batch costs about five requests.
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any

from .claims import Collector
from .fetch import Fetcher, Response

ENDPOINT = "https://query.wikidata.org/sparql?format=json&query={query}"
SOURCE = "open_knowledge_base"
WEB = "web_presence"
LEGAL = "legal_identity"

QUERY = """SELECT ?org ?item ?itemLabel ?itemDescription
 (GROUP_CONCAT(DISTINCT STR(?web); separator="|") AS ?webs)
 (GROUP_CONCAT(DISTINCT ?fb; separator="|") AS ?fbs) (GROUP_CONCAT(DISTINCT ?ig; separator="|") AS ?igs)
 (GROUP_CONCAT(DISTINCT ?tw; separator="|") AS ?tws) (GROUP_CONCAT(DISTINCT ?li; separator="|") AS ?lis)
 (GROUP_CONCAT(DISTINCT ?yt; separator="|") AS ?yts) (SAMPLE(?inception) AS ?founded) (SAMPLE(?emp) AS ?employees)
WHERE {
  VALUES ?org { %s }
  ?item wdt:P2333 ?org .
  OPTIONAL { ?item wdt:P856 ?web }
  OPTIONAL { ?item wdt:P2013 ?fb } OPTIONAL { ?item wdt:P2003 ?ig } OPTIONAL { ?item wdt:P2002 ?tw }
  OPTIONAL { ?item wdt:P4264 ?li } OPTIONAL { ?item wdt:P2397 ?yt }
  OPTIONAL { ?item wdt:P571 ?inception } OPTIONAL { ?item wdt:P1128 ?emp }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nb,nn,en". }
} GROUP BY ?org ?item ?itemLabel ?itemDescription"""

SOCIAL_TEMPLATES = {
    "fbs": ("facebook", "https://facebook.com/{}"),
    "igs": ("instagram", "https://instagram.com/{}"),
    "tws": ("x", "https://x.com/{}"),
    "lis": ("linkedin", "https://linkedin.com/company/{}"),
    "yts": ("youtube", "https://youtube.com/channel/{}"),
}


class Wikidata:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        self.results: dict[str, list[tuple[dict[str, Any], Response, int]]] = {}
        self.state = "not_applicable"

    async def prefetch(self, orgs: list[str]) -> None:
        self.state = "available"
        for start in range(0, len(orgs), 200):
            chunk = orgs[start:start + 200]
            query = QUERY % " ".join(f'"{org}"' for org in chunk)
            url = ENDPOINT.format(query=urllib.parse.quote(query))
            response, body = await self.fetcher.get_json(url, retries=2, headers={"Accept": "application/sparql-results+json"})
            if response.status != 200 or not isinstance(body, dict):
                self.state = "failed"
                continue
            for index, binding in enumerate(body.get("results", {}).get("bindings", [])):
                row = {key: value.get("value") for key, value in binding.items()}
                self.results.setdefault(row.get("org", ""), []).append((row, response, index))

    def websites(self, org: str) -> list[str]:
        sites: list[str] = []
        for row, _, _ in self.results.get(org, []):
            for site in (row.get("webs") or "").split("|"):
                if site and site not in sites:
                    sites.append(site)
        # Prefer Norwegian / generic domains when an item lists many country sites.
        return sorted(sites, key=lambda s: (0 if ".no" in s else 1 if ".com" in s else 2, len(s)))

    def apply(self, col: Collector) -> dict[str, Any]:
        rows = self.results.get(col.organisation_number, [])
        if not rows:
            col.module("wikidata", "not_available" if self.state == "available" else self.state)
            return {}
        if len({row.get("item") for row, _, _ in rows}) > 1:
            col.module("wikidata", "ambiguous", note="several Wikidata items carry this organisation number")
            return {}
        row, response, index = rows[0]
        qid = (row.get("item") or "").rsplit("/", 1)[-1]
        ev = col.add_evidence(response, source_class=SOURCE, method="wikidata_sparql_p2333_v1", locator=f"json:/results/bindings/{index}",
                              span=json.dumps({"item": qid, "label": row.get("itemLabel"), "org": col.organisation_number}, ensure_ascii=False),
                              access_basis="Wikidata CC0; exact join on organisation-number property P2333")
        col.add(WEB, "wikidata_item", {"qid": qid, "url": f"https://www.wikidata.org/wiki/{qid}", "label": row.get("itemLabel")},
                source_class=SOURCE, evidence_ids=[ev], identity="Wikidata P2333 equals the organisation number")
        if row.get("itemDescription"):
            col.add(WEB, "knowledge_base_description", row["itemDescription"], source_class=SOURCE, evidence_ids=[ev])
        if row.get("founded"):
            col.add(LEGAL, "inception_knowledge_base", row["founded"][:10], source_class=SOURCE, evidence_ids=[ev])
        if row.get("employees"):
            col.add(WEB, "employees_knowledge_base", row["employees"], source_class=SOURCE, evidence_ids=[ev],
                    note="Group-level headcount may be reported; not the registry figure")
        socials = []
        for key, (platform, template) in SOCIAL_TEMPLATES.items():
            for handle in [h for h in (row.get(key) or "").split("|") if h][:2]:
                url = template.format(handle)
                socials.append({"platform": platform, "url": url})
                col.add(WEB, "social_profile", {"platform": platform, "url": url, "handle": handle}, source_class=SOURCE,
                        evidence_ids=[ev], key_suffix=f"{platform}.{url.lower()}", confidence=0.95,
                        identity="listed on the Wikidata item matched by organisation number")
        col.module("wikidata", "available", qid=qid)
        return {"qid": qid, "socials": socials}
