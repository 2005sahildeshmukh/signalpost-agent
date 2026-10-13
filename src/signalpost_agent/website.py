"""Official-website discovery, bounded crawl, exact-entity identity gate and company-owned extraction.

Candidates (never evidence on their own):
  1. website declared in Enhetsregisteret (official declaration)
  2. official website on the Wikidata item carrying this organisation number (P2333)
  3. domain of the registry e-mail address
  4. websites declared on the entity's registered subunits
  5. domains derived from the legal name (.no/.com), only if DNS resolves

A candidate is published only after its own fetched pages tie it back to the exact legal entity:
organisation number on the site, or the registry phone / e-mail domain / address plus the legal name.
A site that shows a *different* organisation number in an org-number context is never treated as
exact for a derived-name candidate.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import extruct
import tldextract
import trafilatura
from bs4 import BeautifulSoup

from .claims import Collector
from .fetch import Fetcher, Response
from .textutil import (clean_space, distinctive_tokens, domain_label_variants, fold, name_tokens, norwegian_phone,
                       org_numbers_in, phones_in, unique)

WEB = "web_presence"
ACT = "hiring_activity"
LEAD = "leadership_workplaces"
LEGAL = "legal_identity"
COMPANY = "company_owned"
EXTRACTOR = "site_extractor_v1"

_tld = tldextract.TLDExtract(suffix_list_urls=())  # bundled public-suffix snapshot: no runtime download

FREE_MAIL = {"gmail.com", "hotmail.com", "outlook.com", "live.no", "online.no", "yahoo.com", "yahoo.no", "icloud.com",
             "me.com", "msn.com", "hotmail.no", "frisurf.no", "start.no", "broadpark.no", "getmail.no", "c2i.net",
             "live.com", "outlook.no", "protonmail.com", "proton.me", "mail.com", "gmx.com", "altibox.no", "lyse.net"}
NON_COMPANY_HOSTS = {"facebook.com", "instagram.com", "linkedin.com", "google.com", "gulesider.no", "proff.no", "1881.no",
                     "finn.no", "wix.com", "wixsite.com", "squarespace.com", "brreg.no", "youtube.com", "x.com", "twitter.com",
                     "tiktok.com", "purehelp.no", "bedriftsdatabasen.no", "forvalt.no", "blogspot.com", "wordpress.com",
                     "business.site", "linktr.ee", "vg.no", "nav.no", "regnskapstall.no", "kununu.com"}

SOCIAL_HOSTS = {
    "linkedin.com": "linkedin", "facebook.com": "facebook", "fb.com": "facebook", "instagram.com": "instagram",
    "x.com": "x", "twitter.com": "x", "youtube.com": "youtube", "tiktok.com": "tiktok", "pinterest.com": "pinterest",
    "vimeo.com": "vimeo", "github.com": "github", "threads.net": "threads", "snapchat.com": "snapchat",
}
PAGE_KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("contact", ("kontakt", "contact", "finn-oss", "finn oss", "kontakt-oss")),
    ("about", ("om-oss", "om oss", "omoss", "about", "hvem-er-vi", "hvem er vi", "selskapet", "firmaet", "company")),
    ("privacy", ("personvern", "privacy", "cookie", "vilkar", "vilkår", "terms", "impressum")),
    ("careers", ("karriere", "ledige-stillinger", "ledige stillinger", "stilling", "jobb", "career", "jobs", "join-us", "rekruttering", "jobbe-hos")),
    ("news", ("nyheter", "aktuelt", "news", "blogg", "blog", "presse", "press", "artikler", "nyhet", "media")),
    ("team", ("ansatte", "team", "ledelse", "medarbeidere", "people", "styret", "management", "vare-folk", "våre ansatte", "kontaktpersoner")),
    ("locations", ("avdelinger", "kontorer", "locations", "butikker", "lokasjoner", "vare-kontorer", "våre kontorer", "avdeling")),
]
ATS_HOSTS = ("teamtailor.com", "webcruiter", "jobbnorge.no", "recman", "hrmanager.no", "jobylon", "reachmee", "easycruit",
             "smartrecruiters", "workday", "varbi", "talentech", "emply", "jobindex", "career.webcruiter", "catalystone",
             "recruitee", "lever.co", "greenhouse.io", "homerun.co", "talentsoft", "mynetworkglobal")
NEWSY_PATH = re.compile(r"/(nyhet|nyheter|aktuelt|news|blogg?|blog|artikkel|artikler|article|articles|presse|press|post|posts|innlegg)/", re.I)
PARKED_MARKERS = (
    "domain is for sale", "domene er til salgs", "domenet er til salgs", "this domain is parked", "parked free",
    "hugedomains", "sedoparking", "dan.com", "buy this domain", "kjøp dette domenet", "domeneshop.no/parkering",
    "this site is under construction", "under konstruksjon", "kommer snart", "coming soon", "default web page",
    "welcome to nginx", "apache2 ubuntu default page", "it works!", "index of /", "webhotell", "account suspended",
    "her kommer en ny nettside", "nettsiden er under oppbygging", "denne siden er parkert", "domenet er registrert",
)


@dataclass
class Page:
    kind: str
    url: str
    response: Response
    soup: BeautifulSoup
    text: str
    title: str
    jsonld: list[Any]
    opengraph: dict[str, Any]


@dataclass
class SiteCrawl:
    candidate_url: str
    origin: str
    pages: list[Page] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    blocked: str | None = None
    final_domain: str | None = None
    feeds: list[str] = field(default_factory=list)

    @property
    def home(self) -> Page | None:
        return self.pages[0] if self.pages else None


@dataclass
class IdentityInfo:
    org: str
    legal_name: str
    former_names: list[str]
    phones: set[str]
    email_domains: set[str]
    postal_codes: set[str]
    street_tokens: set[str]
    municipality: str | None
    person_names: list[str]


def registered_domain(url: str) -> str:
    host = urllib.parse.urlparse(url if "//" in url else "https://" + url).hostname or ""
    ext = _tld(host)
    return ext.top_domain_under_public_suffix or host


def normalize_homepage(value: str | None) -> str | None:
    value = str(value or "").strip().strip("/")
    if not value or " " in value or "@" in value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    if not parsed.hostname or "." not in parsed.hostname:
        return None
    return urllib.parse.urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", "", ""))


def normalize_social_url(url: str) -> dict[str, str] | None:
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    host = re.sub(r"^(www|m|mobile|no|nb-no|web|business)\.", "", host)
    platform = next((label for domain, label in SOCIAL_HOSTS.items() if host == domain or host.endswith("." + domain)), None)
    if not platform:
        return None
    parts = [urllib.parse.unquote(p).strip() for p in parsed.path.split("/") if p.strip()]
    low = [p.casefold() for p in parts]
    if not parts:
        return None
    reject_first = {
        "facebook": {"sharer", "sharer.php", "share.php", "share", "dialog", "policy.php", "privacy", "events", "groups", "plugins", "tr", "login", "help", "hashtag", "watch", "story.php", "photo.php", "permalink.php", "home.php", "ads", "business", "legal", "l.php", "photo", "photos", "media"},
        "instagram": {"p", "reel", "reels", "stories", "explore", "accounts", "tv", "about", "legal"},
        "x": {"intent", "share", "home", "search", "i", "hashtag", "privacy", "tos", "login", "settings", "explore"},
        "linkedin": {"shareArticle".casefold(), "sharing", "feed", "in", "pub", "posts", "pulse", "jobs", "login", "help", "learning", "legal"},
        "youtube": {"watch", "embed", "results", "playlist", "shorts", "redirect", "about", "t", "howyoutubeworks"},
        "tiktok": {"tag", "music", "video", "embed", "discover"},
        "pinterest": {"pin", "search", "_"},
        "vimeo": {"video", "channels", "groups", "categories", "showcase", "manage", "player"},
        "github": {"features", "about", "login", "orgs", "marketplace", "topics"},
    }
    if low[0] in reject_first.get(platform, set()):
        return None
    if platform == "facebook":
        if low[0] == "profile.php":
            ids = urllib.parse.parse_qs(parsed.query).get("id")
            if not ids:
                return None
            return {"platform": "facebook", "url": f"https://facebook.com/profile.php?id={ids[0]}", "handle": ids[0]}
        if low[0] == "pages" and len(parts) >= 2:
            parts = parts[1:3] if len(parts) >= 3 else parts[1:2]
        elif low[0] == "people" and len(parts) >= 3:
            parts = parts[1:3]
        else:
            parts = parts[:1]
    elif platform == "linkedin":
        if low[0] not in {"company", "showcase", "school"} or len(parts) < 2:
            return None
        parts = parts[:2]
    elif platform == "youtube":
        if parts[0].startswith("@"):
            parts = parts[:1]
        elif low[0] in {"channel", "user", "c"} and len(parts) >= 2:
            parts = parts[:2]
        elif len(parts) == 1 and re.fullmatch(r"[A-Za-z0-9_.-]{3,}", parts[0]):
            parts = parts[:1]
        else:
            return None
    elif platform == "tiktok":
        if not parts[0].startswith("@"):
            return None
        parts = parts[:1]
    elif platform in ("x", "instagram", "pinterest", "vimeo", "github", "threads", "snapchat"):
        if platform == "snapchat" and low[0] == "add" and len(parts) >= 2:
            parts = parts[:2]
        else:
            parts = parts[:1]
    handle = parts[-1]
    if not re.search(r"[A-Za-z0-9]", handle) or handle.endswith((".php", ".html")):
        return None
    canonical = {"x": "x.com", "facebook": "facebook.com"}.get(platform, {"linkedin": "linkedin.com", "instagram": "instagram.com",
                 "youtube": "youtube.com", "tiktok": "tiktok.com", "pinterest": "pinterest.com", "vimeo": "vimeo.com",
                 "github": "github.com", "threads": "threads.net", "snapchat": "snapchat.com"}.get(platform, host))
    return {"platform": platform, "url": f"https://{canonical}/{'/'.join(parts)}", "handle": handle}


def _is_parked(page: Page) -> bool:
    haystack = fold(page.title + " " + page.text[:3000])
    if len(page.text.strip()) < 40 and not page.jsonld:
        return True
    return any(fold(marker) in haystack for marker in PARKED_MARKERS)


def _iter_jsonld(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_jsonld(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_jsonld(child)


def _types(node: dict[str, Any]) -> set[str]:
    kind = node.get("@type")
    return {str(k) for k in (kind if isinstance(kind, list) else [kind]) if k}


ORG_TYPES = {"Organization", "Corporation", "LocalBusiness", "Store", "Restaurant", "ProfessionalService", "LegalService",
             "HomeAndConstructionBusiness", "AutomotiveBusiness", "MedicalBusiness", "FinancialService", "RealEstateAgent",
             "HealthAndBeautyBusiness", "FoodEstablishment", "LodgingBusiness", "Electrician", "Plumber", "GeneralContractor",
             "RoofingContractor", "HVACBusiness", "Dentist", "AccountingService", "TravelAgency", "SportsActivityLocation",
             "EntertainmentBusiness", "Physician", "Hotel", "CafeOrCoffeeShop", "BeautySalon", "HairSalon", "AutoRepair",
             "MovingCompany", "Attorney", "InsuranceAgency", "EmploymentAgency", "NGO", "EducationalOrganization"}


class Website:
    def __init__(self, fetcher: Fetcher, *, max_pages: int = 8, guess_domains: bool = True, search: Any = None):
        self.fetcher = fetcher
        self.search = search
        self.max_pages = max_pages
        self.guess_domains = guess_domains

    # ------------------------------------------------------------------ crawl
    async def _fetch_page(self, url: str, kind: str, crawl: SiteCrawl) -> Page | None:
        response = await self.fetcher.get(url)
        if response.blocked_reason:
            crawl.errors.append({"url": url, "error": response.blocked_reason, "blocked": True})
            if kind == "home":
                crawl.blocked = response.blocked_reason
            return None
        if response.status != 200 or not response.content:
            crawl.errors.append({"url": url, "error": response.error or f"HTTP {response.status}"})
            return None
        if "html" not in response.content_type.lower() and not response.content.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            crawl.errors.append({"url": url, "error": f"non-HTML content-type {response.content_type}"})
            return None
        html = response.text()
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception as exc:  # pragma: no cover - parser failure is rare
            crawl.errors.append({"url": url, "error": f"parse error {exc}"})
            return None
        try:
            structured = extruct.extract(html, base_url=response.final_url, syntaxes=["json-ld", "opengraph"], errors="ignore")
        except Exception:
            structured = {}
        jsonld = structured.get("json-ld") or []
        og: dict[str, Any] = {}
        for item in structured.get("opengraph") or []:
            for key, value in item.get("properties", []) if isinstance(item, dict) else []:
                og.setdefault(key, value)
        for node in soup(["script", "style", "noscript", "svg", "template"]):
            node.decompose()
        text = clean_space(soup.get_text(" "))
        title = clean_space(soup.title.get_text(" ") if soup.title else "", 300)
        page = Page(kind, response.final_url, response, soup, text, title, jsonld, og)
        crawl.pages.append(page)
        return page

    def _links(self, page: Page) -> list[tuple[str, str]]:
        out = []
        for anchor in page.soup.select("a[href]"):
            href = str(anchor.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:")):
                continue
            out.append((urllib.parse.urljoin(page.url, href), clean_space(anchor.get_text(" "), 120)))
        return out

    def _plan_pages(self, home: Page, domain: str) -> list[tuple[str, str]]:
        ranked: dict[str, tuple[int, str]] = {}
        for url, text in self._links(home):
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https") or registered_domain(url) != domain:
                continue
            if re.search(r"\.(pdf|jpe?g|png|gif|svg|zip|docx?|xlsx?|mp4|webp)$", parsed.path, re.I):
                continue
            clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
            if clean.rstrip("/") == home.url.rstrip("/") or clean.rstrip("/") == urllib.parse.urljoin(home.url, "/").rstrip("/"):
                continue
            haystack = fold(urllib.parse.unquote(parsed.path) + " " + text)
            for rank, (kind, terms) in enumerate(PAGE_KINDS):
                if any(fold(term) in haystack for term in terms):
                    depth = parsed.path.strip("/").count("/")
                    score = rank * 10 + depth
                    if clean not in ranked or ranked[clean][0] > score:
                        ranked[clean] = (score, kind)
                    break
        chosen: list[tuple[str, str]] = []
        kinds_taken: dict[str, int] = {}
        for url, (score, kind) in sorted(ranked.items(), key=lambda item: item[1][0]):
            if kinds_taken.get(kind, 0) >= (2 if kind in ("contact", "about") else 1):
                continue
            kinds_taken[kind] = kinds_taken.get(kind, 0) + 1
            chosen.append((url, kind))
        return chosen

    async def crawl(self, url: str, origin: str, info: IdentityInfo, *, verify_only: bool = False) -> SiteCrawl:
        crawl = SiteCrawl(candidate_url=url, origin=origin)
        home = await self._fetch_page(url, "home", crawl)
        if not home and not crawl.blocked and url.startswith("https://"):
            home = await self._fetch_page("http://" + url[len("https://"):], "home", crawl)
        if not home:
            return crawl
        crawl.final_domain = registered_domain(home.url)
        if _is_parked(home):
            return crawl
        plan = self._plan_pages(home, crawl.final_domain)
        budget = 3 if verify_only else self.max_pages - 1
        identity_first = [p for p in plan if p[1] in ("contact", "about", "privacy")]
        rest = [p for p in plan if p[1] not in ("contact", "about", "privacy")]
        fetched = 0
        for page_url, kind in identity_first:
            if fetched >= min(budget, 3):
                break
            if self._identity_signals(crawl, info)["org_number_match"] and fetched >= 1:
                break
            await self._fetch_page(page_url, kind, crawl)
            fetched += 1
        signals = self._identity_signals(crawl, info)
        if not signals["org_number_match"] and not _is_parked(home) and (
                signals["legal_name_on_site"] or signals["name_in_domain"] or origin in ("registry_declared", "wikidata_org_number", "registry_email_domain")):
            # Many small sites hide contact/privacy pages behind JS menus; probe the conventional paths directly.
            fetched_paths = {urllib.parse.urlparse(p.url).path.rstrip("/").lower() for p in crawl.pages}
            probes = 0
            for path, kind in (("/kontakt", "contact"), ("/kontakt-oss", "contact"), ("/om-oss", "about"), ("/personvern", "privacy")):
                if probes >= 2 or self._identity_signals(crawl, info)["org_number_match"]:
                    break
                if path in fetched_paths:
                    continue
                page = await self._fetch_page(urllib.parse.urljoin(home.url, path), kind, crawl)
                probes += 1
                if page and urllib.parse.urlparse(page.url).path.rstrip("/").lower() in ("", "/"):
                    crawl.pages.remove(page)  # redirected to the homepage: not a distinct page
        if verify_only:
            return crawl
        remaining = [p for p in rest][: max(0, budget - fetched)]
        await asyncio.gather(*(self._fetch_page(page_url, kind, crawl) for page_url, kind in remaining))
        for link in home.soup.select('link[rel~="alternate"][href]'):
            kind = str(link.get("type") or "").lower()
            if "rss" in kind or "atom" in kind:
                feed = urllib.parse.urljoin(home.url, str(link.get("href")))
                if registered_domain(feed) == crawl.final_domain and "comments" not in feed:
                    crawl.feeds.append(feed)
        return crawl

    # ------------------------------------------------------------------ identity
    def _identity_signals(self, crawl: SiteCrawl, info: IdentityInfo) -> dict[str, Any]:
        all_text = " ".join(page.text for page in crawl.pages)
        found, contextual = org_numbers_in(all_text)
        jsonld_ids: set[str] = set()
        for page in crawl.pages:
            for node in _iter_jsonld(page.jsonld):
                for key in ("vatID", "taxID", "identifier", "leiCode", "duns"):
                    digits = re.sub(r"\D", "", json.dumps(node.get(key, "")))
                    if len(digits) >= 9:
                        jsonld_ids.update(d for d in re.findall(r"\d{9}", digits))
        found |= jsonld_ids
        contextual |= jsonld_ids
        other_contextual = sorted(contextual - {info.org})
        site_phones = phones_in(all_text)
        for page in crawl.pages:
            for anchor in page.soup.select('a[href^="tel:"]'):
                phone = norwegian_phone(str(anchor.get("href"))[4:])
                if phone:
                    site_phones.add(phone)
        emails = self._emails(crawl)
        folded = fold(all_text)
        identity_parts = []
        for page in crawl.pages[:4]:
            identity_parts.extend([page.title, str(page.opengraph.get("og:site_name") or ""), str(page.opengraph.get("og:title") or "")])
            for node in _iter_jsonld(page.jsonld):
                if _types(node) & ORG_TYPES:
                    identity_parts.extend(str(node.get(k) or "") for k in ("name", "legalName", "alternateName"))
        identity_folded = fold(" ".join(identity_parts))
        tokens = distinctive_tokens(info.legal_name)
        all_tokens = name_tokens(info.legal_name)
        token_set = set(re.findall(r"[a-z0-9]+", folded))

        def name_in(text_tokens: set[str], text: str) -> bool:
            if not all_tokens:
                return False
            if all(t in text_tokens for t in all_tokens):
                return True
            joined = "".join(all_tokens)
            return len(joined) >= 6 and joined in re.sub(r"[^a-z0-9]", "", text)

        legal_name_on_site = name_in(token_set, folded)
        former_name_on_site = False
        for former in info.former_names:
            former_tokens = name_tokens(former)
            if former_tokens and all(t in token_set for t in former_tokens):
                former_name_on_site = True
        identity_tokens = set(re.findall(r"[a-z0-9]+", identity_folded))
        name_in_identity = bool(tokens) and all(t in identity_tokens or t in re.sub(r"[^a-z0-9]", "", identity_folded) for t in tokens)
        domain_label = fold((crawl.final_domain or "").split(".")[0]).replace("-", "")
        name_in_domain = bool(tokens) and all(t in domain_label for t in tokens if len(t) >= 3)
        postal_match = any(re.search(rf"(?<!\d){code}(?!\d)", all_text) for code in info.postal_codes)
        street_match = any(tok in token_set for tok in info.street_tokens)
        person_hits = [name for name in info.person_names if name and fold(name) in folded]
        return {
            "org_number_match": info.org in found,
            "org_number_contextual": info.org in contextual,
            "other_org_numbers": other_contextual[:5],
            "phone_match": bool(info.phones & site_phones),
            "email_domain_match": bool(info.email_domains & {e.split("@")[-1] for e in emails}) or (crawl.final_domain in info.email_domains),
            "legal_name_on_site": legal_name_on_site,
            "former_name_on_site": former_name_on_site,
            "name_in_identity_fields": name_in_identity,
            "name_in_domain": name_in_domain,
            "address_match": postal_match and street_match,
            "postal_code_match": postal_match,
            "registered_people_on_site": person_hits[:10],
            "pages_checked": [page.url for page in crawl.pages],
        }

    def assess(self, crawl: SiteCrawl, info: IdentityInfo) -> dict[str, Any]:
        home = crawl.home
        if crawl.blocked:
            return {"status": "blocked", "publishable": False, "reasons": [crawl.blocked], "score": 0.0}
        if not home:
            return {"status": "unreachable", "publishable": False, "reasons": [e["error"] for e in crawl.errors][:3], "score": 0.0}
        if _is_parked(home):
            return {"status": "parked_or_placeholder", "publishable": False, "reasons": ["placeholder, parked or near-empty homepage"], "score": 0.05}
        if crawl.final_domain in NON_COMPANY_HOSTS:
            return {"status": "not_company_site", "publishable": False, "reasons": [f"{crawl.final_domain} is a platform/directory host"], "score": 0.1}
        s = self._identity_signals(crawl, info)
        reasons: list[str] = []
        name_ok = s["legal_name_on_site"] or s["name_in_identity_fields"] or s["former_name_on_site"]
        corroborations = [k for k in ("phone_match", "email_domain_match", "address_match") if s[k]]
        if s["registered_people_on_site"]:
            corroborations.append("registered_people_on_site")
        declared = crawl.origin in ("registry_declared", "wikidata_org_number")
        local = [k for k in ("phone_match", "address_match") if s[k]]
        norwegian_domain = (crawl.final_domain or "").endswith(".no")
        if s["org_number_match"]:
            score, status = 1.0, "exact"
            reasons.append("the exact organisation number appears on the site" + (" in an org-number context" if s["org_number_contextual"] else ""))
        elif s["other_org_numbers"]:
            other = s["other_org_numbers"][0]
            if declared or crawl.origin == "registry_email_domain":
                score, status = 0.6, "declared_related"
                reasons.append(f"officially declared, but the site shows organisation number {other} and not this one (likely parent, manager, brand owner or group site)")
            else:
                score, status = 0.2, "conflict"
                reasons.append(f"site shows a different organisation number {other}")
        elif declared and (local or (name_ok and norwegian_domain) or (corroborations and norwegian_domain)):
            score, status = 0.96, "exact"
            basis = local or (["legal name"] if name_ok else corroborations)
            reasons.append(f"{crawl.origin.replace('_', ' ')} website corroborated on the site by {', '.join(basis)}")
        elif declared:
            score, status = 0.85, "declared_only"
            reasons.append("declared by an official source, but the site does not restate this entity with local evidence (could be a group or brand site)")
        elif name_ok and (local or (norwegian_domain and corroborations)):
            score, status = 0.95, "exact"
            reasons.append(f"legal name on site plus {', '.join(local or corroborations)}")
        elif norwegian_domain and s["name_in_domain"] and ("phone_match" in corroborations or "email_domain_match" in corroborations):
            score, status = 0.93, "exact"
            reasons.append(f"name-derived .no domain corroborated by {', '.join(corroborations)}")
        elif name_ok and corroborations and crawl.origin == "registry_email_domain":
            score, status = 0.85, "declared_only"
            reasons.append("the entity's registry e-mail domain, but the site has no local evidence for this entity (could be a group site)")
        elif name_ok:
            score, status = 0.6, "name_only"
            reasons.append("legal name appears but no independent local corroboration (org number, phone, address)")
        else:
            score, status = 0.2, "no_match"
            reasons.append("no exact-entity evidence on the site")
        return {"status": status, "publishable": status == "exact", "website_publishable": status == "exact" or (status == "declared_only" and declared),
                "score": score, "reasons": reasons, "signals": s, "method": "identity_gate_v2"}

    # ------------------------------------------------------------------ discovery
    async def discover(self, col: Collector, info: IdentityInfo, *, registry_site: str | None, wikidata_sites: list[str],
                       registry_email: str | None, subunit_sites: list[str], employees: int | None) -> tuple[SiteCrawl | None, dict[str, Any] | None]:
        candidates: list[tuple[str, str]] = []
        if normalize_homepage(registry_site):
            candidates.append((normalize_homepage(registry_site), "registry_declared"))
        for site in wikidata_sites[:2]:
            if normalize_homepage(site):
                candidates.append((normalize_homepage(site), "wikidata_org_number"))
        if registry_email and "@" in registry_email:
            domain = registry_email.split("@")[-1].strip().lower()
            if domain and domain not in FREE_MAIL and registered_domain(domain) not in FREE_MAIL:
                candidates.append((f"https://{registered_domain(domain)}/", "registry_email_domain"))
        for site in subunit_sites[:2]:
            if normalize_homepage(site):
                candidates.append((normalize_homepage(site), "subunit_declared"))
        seen_domains: set[str] = set()
        ordered: list[tuple[str, str]] = []
        for url, origin in candidates:
            domain = registered_domain(url)
            if domain in seen_domains or domain in NON_COMPANY_HOSTS:
                continue
            seen_domains.add(domain)
            ordered.append((url, origin))
        best: tuple[SiteCrawl, dict[str, Any]] | None = None
        fallback: tuple[SiteCrawl, dict[str, Any]] | None = None
        for url, origin in ordered:
            crawl = await self.crawl(url, origin, info)
            verdict = self.assess(crawl, info)
            col.candidates.append({"type": "website", "url": url, "origin": origin, "final_url": crawl.home.url if crawl.home else None,
                                   "verdict": verdict["status"], "score": verdict["score"], "reasons": verdict["reasons"]})
            if verdict["publishable"]:
                return crawl, verdict
            if verdict.get("website_publishable") and fallback is None:
                fallback = (crawl, verdict)
        if fallback:
            best = fallback
        if best is None and self.guess_domains:
            guessed = await self._guess(col, info, seen_domains, employees)
            if guessed:
                return guessed
        if best is None and self.search is not None and self.search.enabled:
            for url in await self.search.website_candidates(info.legal_name, info.municipality):
                domain = registered_domain(url)
                if domain in seen_domains or domain in NON_COMPANY_HOSTS or any(domain.endswith("." + h) for h in NON_COMPANY_HOSTS):
                    continue
                seen_domains.add(domain)
                root = urllib.parse.urljoin(url, "/")
                crawl = await self.crawl(root, "search_candidate", info)
                verdict = self.assess(crawl, info)
                col.candidates.append({"type": "website", "url": root, "origin": "search_candidate", "final_url": crawl.home.url if crawl.home else None,
                                       "verdict": verdict["status"], "score": verdict["score"], "reasons": verdict["reasons"]})
                if verdict["publishable"]:
                    return crawl, verdict
        return best if best else (None, None)

    async def _guess(self, col: Collector, info: IdentityInfo, seen: set[str], employees: int | None):
        labels = domain_label_variants(info.legal_name)
        limit = 6 if employees else 4
        hosts: list[str] = []
        for label in labels[:limit]:
            for tld in ("no", "com"):
                host = f"{label}.{tld}"
                if host not in seen:
                    hosts.append(host)
        if not hosts:
            return None
        resolved = await asyncio.gather(*(self.fetcher.resolves(host) for host in hosts))
        live = [host for host, ok in zip(hosts, resolved) if ok]
        for host in live[:4]:
            crawl = await self.crawl(f"https://{host}/", "name_derived_domain", info, verify_only=True)
            verdict = self.assess(crawl, info)
            col.candidates.append({"type": "website", "url": f"https://{host}/", "origin": "name_derived_domain",
                                   "final_url": crawl.home.url if crawl.home else None, "verdict": verdict["status"],
                                   "score": verdict["score"], "reasons": verdict["reasons"]})
            if verdict["publishable"]:
                if crawl.final_domain:
                    full = await self.crawl(crawl.home.url, "name_derived_domain", info)
                    full_verdict = self.assess(full, info)
                    if full_verdict["publishable"]:
                        return full, full_verdict
                return crawl, verdict
        return None

    # ------------------------------------------------------------------ extraction
    def _emails(self, crawl: SiteCrawl) -> list[str]:
        emails: list[str] = []
        for page in crawl.pages:
            for anchor in page.soup.select('a[href^="mailto:"]'):
                address = urllib.parse.unquote(str(anchor.get("href"))[7:].split("?")[0]).strip().lower()
                if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", address):
                    emails.append(address)
            for match in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", page.text):
                emails.append(match.lower())
        return unique(e for e in emails if not e.endswith((".png", ".jpg", ".webp")) and "example" not in e and "sentry" not in e)

    def extract(self, col: Collector, crawl: SiteCrawl, verdict: dict[str, Any], info: IdentityInfo) -> dict[str, Any]:
        home = crawl.home
        assert home is not None
        signals = verdict.get("signals") or {}
        identity_basis = "; ".join(verdict.get("reasons") or [])
        root = urllib.parse.urljoin(home.url, "/")
        ev_home = col.add_evidence(home.response, source_class=COMPANY, method=EXTRACTOR, locator="html:title",
                                   span=home.title or root, access_basis="robots.txt permits; public page")
        proof_ev = [ev_home]
        if signals.get("org_number_match"):
            for page in crawl.pages:
                idx = page.text.find(info.org[:3])
                found, _ = org_numbers_in(page.text)
                if info.org in found:
                    match = re.search(r"\d{3}[\s. \-]?\d{3}[\s. \-]?\d{3}", page.text[max(0, idx - 5):]) if idx >= 0 else None
                    pos = page.text.find(match.group(0)) if match else -1
                    span = page.text[max(0, pos - 60): pos + 30] if pos >= 0 else info.org
                    proof_ev = [col.add_evidence(page.response, source_class=COMPANY, method="org_number_text_match_v1",
                                                 locator="text:org_number", span=span, access_basis="robots.txt permits; public page")]
                    break
        summary: dict[str, Any] = {"url": root, "domain": crawl.final_domain, "identity": verdict["status"], "origin": crawl.origin}
        col.add(WEB, "official_website", root, source_class=COMPANY,
                evidence_ids=proof_ev, confidence=verdict["score"], identity=identity_basis,
                note=None if verdict["publishable"] else "Declared by an official source; site content does not independently restate the entity")
        col.add(WEB, "website_discovery_method", crawl.origin, source_class=COMPANY, evidence_ids=proof_ev)
        if not verdict["publishable"]:
            # Only the declared URL is published; site-derived facts would risk wrong-company attribution.
            col.module("website_identity", "ambiguous", status=verdict["status"], reasons=verdict["reasons"])
            return summary
        col.module("website_identity", "available", status=verdict["status"], score=verdict["score"])

        # ---- brand, description, structured organisation data
        brand = self._brand(crawl, info)
        if brand:
            col.add(LEGAL, "public_brand", brand["name"], source_class=COMPANY,
                    evidence_ids=[col.add_evidence(brand["page"].response, source_class=COMPANY, method=EXTRACTOR, locator=brand["locator"], span=brand["name"])],
                    confidence=0.9, identity=identity_basis)
            summary["brand"] = brand["name"]
        description = None
        for page in crawl.pages:
            for key in ("og:description",):
                if page.opengraph.get(key):
                    description = (page, f"opengraph:{key}", str(page.opengraph[key]))
                    break
            if description:
                break
            meta = page.soup.select_one('meta[name="description" i]')
            if meta and clean_space(meta.get("content")) and len(clean_space(meta.get("content"))) > 30:
                description = (page, "html:meta[name=description]", clean_space(meta.get("content")))
                break
        if description:
            text = clean_space(description[2], 600)
            col.add(WEB, "site_description", text, source_class=COMPANY,
                    evidence_ids=[col.add_evidence(description[0].response, source_class=COMPANY, method=EXTRACTOR, locator=description[1], span=text)],
                    confidence=0.9, note="Company-authored description (self-reported)")
            summary["description"] = text
        about = next((p for p in crawl.pages if p.kind == "about"), None)
        try:
            if about is None:
                raise LookupError("no about page")
            main = trafilatura.extract(about.response.text(), url=about.url, include_links=False, include_tables=False, favor_precision=True) or ""
        except Exception:
            main = ""
        main = clean_space(main, 900)
        about = about or home
        if len(main) > 80:
            col.add(WEB, "about_text", main, source_class=COMPANY,
                    evidence_ids=[col.add_evidence(about.response, source_class=COMPANY, method="trafilatura_main_text_v2", locator="text:main", span=main[:400])],
                    confidence=0.85, note="Company-authored text (self-reported)")
            summary["about"] = main
        self._structured(col, crawl, summary)

        # ---- social profiles linked from the verified site
        socials = self._socials(col, crawl, info, brand["name"] if brand else None)
        summary["socials"] = socials

        # ---- contact details shown on the verified site
        contact_pages = [p for p in crawl.pages if p.kind in ("home", "contact", "about")]
        contact_crawl = SiteCrawl(crawl.candidate_url, crawl.origin, pages=contact_pages)
        emails = [e for e in self._emails(contact_crawl) if e.split("@")[-1].endswith(crawl.final_domain or "~") or e.split("@")[-1] in info.email_domains]
        generic = ("post", "info", "kontakt", "contact", "firmapost", "hei", "hello", "salg", "sales", "kundeservice", "service", "office", "kontor")
        emails.sort(key=lambda e: (0 if e.split("@")[0] in generic else 1, len(e)))
        for email in emails[:3]:
            page = next((p for p in crawl.pages if email in p.text.lower() or f"mailto:{email}" in str(p.soup).lower()), home)
            col.add(WEB, "contact_email", email, source_class=COMPANY, key_suffix=email,
                    evidence_ids=[col.add_evidence(page.response, source_class=COMPANY, method=EXTRACTOR, locator="html:a[href^=mailto]|text", span=email)])
        site_phones: list[tuple[str, Page]] = []
        for page in contact_pages:
            for anchor in page.soup.select('a[href^="tel:"]'):
                phone = norwegian_phone(str(anchor.get("href"))[4:])
                if phone and phone not in [p for p, _ in site_phones]:
                    site_phones.append((phone, page))
        site_phones.sort(key=lambda item: 0 if item[0] in info.phones else 1)
        for phone, page in site_phones[:3]:
            pretty = f"+47 {phone[:3]} {phone[3:5]} {phone[5:]}" if phone[0] in "49" else f"+47 {phone[:2]} {phone[2:4]} {phone[4:6]} {phone[6:]}"
            col.add(WEB, "contact_phone", pretty, source_class=COMPANY, key_suffix=phone,
                    evidence_ids=[col.add_evidence(page.response, source_class=COMPANY, method=EXTRACTOR, locator='html:a[href^="tel:"]', span=pretty)])

        # ---- registered people confirmed on the company's own site
        for name in signals.get("registered_people_on_site") or []:
            page = next((p for p in crawl.pages if fold(name) in fold(p.text)), home)
            idx = fold(page.text).find(fold(name))
            span = page.text[max(0, idx - 40): idx + len(name) + 60] if idx >= 0 else name
            col.add(LEAD, "person_on_company_site", {"name": name, "page": page.url}, source_class=COMPANY, key_suffix=name,
                    evidence_ids=[col.add_evidence(page.response, source_class=COMPANY, method="registered_name_text_match_v1", locator="text:name", span=span)],
                    note="Registry role holder named on the verified company website")

        # ---- hiring channels and job postings on the site
        summary["careers"] = self._careers(col, crawl)
        summary["site_pages"] = [{"kind": p.kind, "url": p.url, "title": p.title} for p in crawl.pages]
        return summary

    def _brand(self, crawl: SiteCrawl, info: IdentityInfo) -> dict[str, Any] | None:
        home = crawl.home
        if not home:
            return None
        legal_folded = fold(info.legal_name)
        for node in _iter_jsonld(home.jsonld):
            if _types(node) & ORG_TYPES and isinstance(node.get("name"), str) and 2 <= len(node["name"]) <= 80:
                return {"name": clean_space(node["name"]), "page": home, "locator": "jsonld:Organization.name"}
        site_name = home.opengraph.get("og:site_name")
        if isinstance(site_name, str) and 2 <= len(site_name.strip()) <= 80:
            return {"name": clean_space(site_name), "page": home, "locator": "opengraph:og:site_name"}
        domain_label = fold((crawl.final_domain or "").split(".")[0]).replace("-", "")
        for segment in re.split(r"\s[|\-–—:·•]\s", home.title or ""):
            seg = clean_space(segment)
            compact = re.sub(r"[^a-z0-9]", "", fold(seg))
            if 2 <= len(seg) <= 60 and compact and (compact in domain_label or domain_label in compact or compact in re.sub(r"[^a-z0-9]", "", legal_folded)):
                return {"name": seg, "page": home, "locator": "html:title"}
        return None

    def _structured(self, col: Collector, crawl: SiteCrawl, summary: dict[str, Any]) -> None:
        done: set[str] = set()
        for page in crawl.pages:
            for node in _iter_jsonld(page.jsonld):
                if not (_types(node) & ORG_TYPES):
                    continue
                for key, field_name in (("foundingDate", "founding_date_self_reported"), ("numberOfEmployees", "employees_self_reported"),
                                        ("logo", "logo"), ("slogan", "slogan"), ("areaServed", "area_served"), ("priceRange", "price_range")):
                    value = node.get(key)
                    if isinstance(value, dict):
                        value = value.get("value") or value.get("url") or value.get("name") or value.get("@id")
                    if isinstance(value, list):
                        value = ", ".join(str(v.get("name") if isinstance(v, dict) else v) for v in value[:8])
                    if value in (None, "") or field_name in done:
                        continue
                    done.add(field_name)
                    col.add(WEB, field_name, value, source_class=COMPANY,
                            evidence_ids=[col.add_evidence(page.response, source_class=COMPANY, method="jsonld_extruct_v1", locator=f"jsonld:{key}", span=str(value)[:200])],
                            confidence=0.9, note="Structured data published by the company (self-reported)")
                address = node.get("address")
                for addr in (address if isinstance(address, list) else [address]):
                    if isinstance(addr, dict):
                        text = ", ".join(str(addr.get(k)) for k in ("streetAddress", "postalCode", "addressLocality") if addr.get(k))
                        if text:
                            col.add(LEAD, "site_listed_address", text, source_class=COMPANY, key_suffix=text,
                                    evidence_ids=[col.add_evidence(page.response, source_class=COMPANY, method="jsonld_extruct_v1", locator="jsonld:address", span=text)])
                hours = node.get("openingHours") or node.get("openingHoursSpecification")
                if hours and "opening_hours" not in done:
                    done.add("opening_hours")
                    rendered = hours if isinstance(hours, str) else json.dumps(hours, ensure_ascii=False)[:400]
                    col.add(WEB, "opening_hours", rendered, source_class=COMPANY,
                            evidence_ids=[col.add_evidence(page.response, source_class=COMPANY, method="jsonld_extruct_v1", locator="jsonld:openingHours", span=rendered[:200])])
        if "logo" not in done:
            home = crawl.home
            image = home.opengraph.get("og:image") if home else None
            if isinstance(image, str) and image.startswith("http"):
                col.add(WEB, "share_image", image, source_class=COMPANY,
                        evidence_ids=[col.add_evidence(home.response, source_class=COMPANY, method=EXTRACTOR, locator="opengraph:og:image", span=image)])

    def _socials(self, col: Collector, crawl: SiteCrawl, info: IdentityInfo, brand: str | None) -> list[dict[str, Any]]:
        found: dict[str, tuple[dict[str, str], Page, str]] = {}
        for page in crawl.pages:
            candidates: list[tuple[str, str]] = []
            for anchor in page.soup.select("a[href]"):
                candidates.append((str(anchor.get("href") or ""), "html:a[href]"))
            for node in page.soup.select("iframe[src]"):
                candidates.append((str(node.get("src") or ""), "html:iframe[src]"))
            for node in _iter_jsonld(page.jsonld):
                same = node.get("sameAs")
                for url in (same if isinstance(same, list) else [same]):
                    if isinstance(url, str):
                        candidates.append((url, "jsonld:sameAs"))
            for raw, locator in candidates:
                url = urllib.parse.urljoin(page.url, raw.strip())
                parsed = urllib.parse.urlparse(url)
                if (parsed.hostname or "").endswith("facebook.com") and parsed.path.startswith("/plugins/"):
                    embedded = urllib.parse.parse_qs(parsed.query).get("href", [])
                    url = embedded[0] if embedded else url
                social = normalize_social_url(url)
                if social and social["url"] not in found:
                    found[social["url"]] = (social, page, locator)
        name_keys = {t for t in distinctive_tokens(info.legal_name) if len(t) >= 3}
        brand_compact = re.sub(r"[^a-z0-9]", "", fold(brand or ""))
        domain_label = re.sub(r"[^a-z0-9]", "", fold((crawl.final_domain or "").split(".")[0]))
        out = []
        per_platform: dict[str, int] = {}
        for url, (social, page, locator) in found.items():
            handle = re.sub(r"[^a-z0-9]", "", fold(social["handle"]))
            matches_name = bool(handle) and (any(t in handle for t in name_keys if len(t) >= 4)
                                            or (brand_compact and len(brand_compact) >= 3 and (brand_compact in handle or (len(handle) >= 4 and handle in brand_compact)))
                                            or (domain_label and len(domain_label) >= 3 and (domain_label in handle or (len(handle) >= 4 and handle in domain_label))))
            numeric_id = social["handle"].isdigit() or social["url"].startswith("https://facebook.com/profile.php")
            platform = social["platform"]
            if not (matches_name or (numeric_id and platform == "facebook")):
                col.candidates.append({"type": "social", "url": social["url"], "verdict": "handle_not_tied_to_entity", "page": page.url})
                continue
            per_platform[platform] = per_platform.get(platform, 0) + 1
            if per_platform[platform] > 2:
                continue
            ev = col.add_evidence(page.response, source_class=COMPANY, method="verified_site_outbound_link_v1", locator=locator,
                                  span=social["url"], access_basis="link published on the verified official website")
            col.add(WEB, "social_profile", {"platform": platform, "url": social["url"], "handle": social["handle"]},
                    source_class=COMPANY, evidence_ids=[ev], key_suffix=f"{platform}.{social['url'].lower()}",
                    confidence=0.95 if matches_name else 0.9,
                    identity="linked from the verified official website; handle matches the legal name, brand or domain" if matches_name else "linked from the verified official website")
            out.append({"platform": platform, "url": social["url"]})
        return out

    def _careers(self, col: Collector, crawl: SiteCrawl) -> dict[str, Any]:
        info: dict[str, Any] = {"pages": [], "ats": [], "postings": []}
        for page in crawl.pages:
            if page.kind == "careers":
                info["pages"].append(page.url)
                col.add(ACT, "careers_page", page.url, source_class=COMPANY, key_suffix=page.url,
                        evidence_ids=[col.add_evidence(page.response, source_class=COMPANY, method=EXTRACTOR, locator="html:title", span=page.title or page.url)])
            for url, text in self._links(page):
                host = (urllib.parse.urlparse(url).hostname or "").lower()
                if any(marker in host for marker in ATS_HOSTS) and url not in info["ats"] and len(info["ats"]) < 3:
                    info["ats"].append(url)
                    col.add(ACT, "recruitment_channel", {"url": url, "provider": host}, source_class=COMPANY, key_suffix=host,
                            evidence_ids=[col.add_evidence(page.response, source_class=COMPANY, method="verified_site_outbound_link_v1", locator="html:a[href]", span=f"{text} {url}"[:200])])
            for node in _iter_jsonld(page.jsonld):
                if "JobPosting" in _types(node) and node.get("title"):
                    posting = {"title": clean_space(node.get("title"), 200), "date_posted": node.get("datePosted"),
                               "valid_through": node.get("validThrough"), "url": node.get("url") or page.url,
                               "employment_type": node.get("employmentType") if isinstance(node.get("employmentType"), str) else None}
                    info["postings"].append(posting)
                    col.add(ACT, "job_posting", {k: v for k, v in posting.items() if v}, source_class=COMPANY,
                            key_suffix=f"site.{posting['title']}.{posting['date_posted']}", effective=str(posting["date_posted"] or "")[:10] or None,
                            evidence_ids=[col.add_evidence(page.response, source_class=COMPANY, method="jsonld_extruct_v1", locator="jsonld:JobPosting", span=posting["title"])])
        return info

    def _listing_items(self, page: Page, crawl: SiteCrawl, cutoff: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page_path = urllib.parse.urlparse(page.url).path.rstrip("/")
        seen: set[str] = set()
        for anchor in page.soup.select("a[href]")[:600]:
            href = urllib.parse.urljoin(page.url, str(anchor.get("href") or ""))
            parsed_href = urllib.parse.urlparse(href)
            path = parsed_href.path.rstrip("/")
            if registered_domain(href) != crawl.final_domain or path in ("", page_path) or href in seen:
                continue
            newsy = bool(NEWSY_PATH.search(path + "/")) or (page.kind == "news" and path.startswith(page_path + "/"))
            if not newsy or re.search(r"/(page|side|category|kategori|tag|tags|author|forfatter)/", path + "/", re.I):
                continue
            container = anchor
            for _ in range(4):
                parent = container.parent
                if parent is None or len(parent.get_text(" ", strip=True)) > 450:
                    break
                container = parent
            context = clean_space(container.get_text(" "))
            found = None
            time_node = container.select_one("time[datetime]")
            if time_node is not None:
                parsed = _parse_date(str(time_node.get("datetime")))
                found = (parsed, "html:time[datetime]") if parsed else None
            if not found:
                text_date = find_text_date(context)
                found = (text_date[0], "text:date-near-link") if text_date else None
            if not found and url_date(href):
                found = (url_date(href), "url:date-in-path")
            if not found or found[0] < cutoff:
                continue
            heading = container.select_one("h1,h2,h3,h4,h5")
            title = clean_space(heading.get_text(" ") if heading else anchor.get_text(" "), 200)
            text_date = find_text_date(title)
            if text_date:
                title = clean_space(title.replace(text_date[1], ""), 200)
            title = re.sub(r"^[|·\-–\s]+|\b\d{1,2}:\d{2}\s*(\(\w+\))?", "", title).strip(" |·-–")
            if len(title) < 8 or fold(title) in ("les mer", "read more", "les hele saken", "mer", "more", "nyheter", "news"):
                continue
            seen.add(href)
            out.append({"title": title, "date": found[0].date().isoformat(), "url": href, "_page": page,
                        "_locator": found[1], "kind": "news"})
        return out[:20]

    async def _sitemap_items(self, crawl: SiteCrawl, cutoff: datetime) -> list[dict[str, Any]]:
        home = crawl.home
        if not home:
            return []
        sitemaps = [u for u in self.fetcher.sitemaps_from_robots(home.url) if registered_domain(u) == crawl.final_domain][:2]
        if not sitemaps:
            sitemaps = [urllib.parse.urljoin(home.url, "/sitemap.xml")]
        out: list[dict[str, Any]] = []
        fetched = 0
        queue = list(sitemaps)
        while queue and fetched < 3:
            url = queue.pop(0)
            response = await self.fetcher.get(url, accept="application/xml,text/xml", max_bytes=4_000_000)
            fetched += 1
            if not response.ok:
                continue
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError:
                continue
            tag = root.tag.split("}")[-1]
            if tag == "sitemapindex":
                children = [el.text.strip() for el in root.iter() if el.tag.split("}")[-1] == "loc" and el.text]
                preferred = [c for c in children if re.search(r"(post|news|nyhet|article|artikkel|blog|aktuelt|press)", c, re.I)]
                queue.extend((preferred or children)[:2])
                continue
            for node in root:
                loc = lastmod = None
                for child in node:
                    ctag = child.tag.split("}")[-1]
                    if ctag == "loc":
                        loc = (child.text or "").strip()
                    elif ctag == "lastmod":
                        lastmod = (child.text or "").strip()
                if not loc or not NEWSY_PATH.search(urllib.parse.urlparse(loc).path.rstrip("/") + "/"):
                    continue
                if urllib.parse.urlparse(loc).path.rstrip("/").count("/") < 2:
                    continue
                parsed = url_date(loc) or _parse_date(lastmod)
                if not parsed or parsed < cutoff:
                    continue
                slug = urllib.parse.unquote(urllib.parse.urlparse(loc).path.rstrip("/").rsplit("/", 1)[-1])
                slug = re.sub(r"^\d{6,8}[-_]", "", slug)
                title = clean_space(re.sub(r"[-_]+", " ", re.sub(r"\.(html?|php|aspx)$", "", slug)), 200)
                if len(title) < 6:
                    continue
                out.append({"title": title[:1].upper() + title[1:], "date": parsed.date().isoformat(), "url": loc, "_response": response,
                            "_locator": "xml:url/lastmod" if not url_date(loc) else "url:date-in-path", "kind": "sitemap_entry"})
        return out

    # ------------------------------------------------------------------ dated activity on the company site
    async def activity(self, col: Collector, crawl: SiteCrawl, *, max_items: int = 10) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=730)
        home = crawl.home
        if not home or not crawl.final_domain:
            return items
        # 1. Structured articles on fetched pages.
        for page in crawl.pages:
            for node in _iter_jsonld(page.jsonld):
                if _types(node) & {"NewsArticle", "Article", "BlogPosting", "Event", "PressRelease"}:
                    date = node.get("datePublished") or node.get("startDate")
                    title = node.get("headline") or node.get("name")
                    if isinstance(title, str) and isinstance(date, str) and _parse_date(date) and _parse_date(date) >= cutoff:
                        items.append({"title": clean_space(title, 200), "date": _parse_date(date).date().isoformat(), "url": node.get("url") or page.url,
                                      "_page": page, "_locator": "jsonld:datePublished", "kind": "article"})
            # Dated links on news listings (and news-like links on the homepage).
            if page.kind in ("news", "home"):
                items.extend(self._listing_items(page, crawl, cutoff))
        # 2. RSS/Atom feeds (WordPress sites expose /feed/ by default).
        feeds = list(crawl.feeds)
        if not feeds and "wp-content" in home.response.text()[:200000]:
            feeds.append(urllib.parse.urljoin(home.url, "/feed/"))
        for feed in feeds[:1]:
            response = await self.fetcher.get(feed, accept="application/rss+xml,application/atom+xml,application/xml,text/xml")
            if response.ok:
                for entry in _parse_feed(response.content)[:max_items]:
                    parsed = _parse_date(entry.get("date"))
                    if parsed and parsed >= cutoff and entry.get("title"):
                        items.append({"title": clean_space(entry["title"], 200), "date": parsed.date().isoformat(), "url": entry.get("link") or feed,
                                      "_response": response, "_locator": "xml:item/pubDate", "kind": "feed_item"})
        if len(items) < 3:
            items.extend(await self._sitemap_items(crawl, cutoff))
        # deduplicate by url/title, newest first
        seen: set[str] = set()
        unique_items = []
        for item in sorted(items, key=lambda x: x["date"], reverse=True):
            key = (item.get("url") or "") + "|" + fold(item["title"])
            if key in seen:
                continue
            seen.add(key)
            unique_items.append(item)
        published = []
        for item in unique_items[:max_items]:
            response = item.get("_response") or item["_page"].response
            ev = col.add_evidence(response, source_class=COMPANY, method="site_activity_extractor_v1", locator=item["_locator"],
                                  span=f"{item['date']} {item['title']}")
            record = {"title": item["title"], "date": item["date"], "url": item["url"], "kind": item["kind"]}
            col.add(ACT, "company_news", record, source_class=COMPANY, evidence_ids=[ev], effective=item["date"],
                    key_suffix=f"{item['date']}.{item['url']}", note="Published on the verified company website")
            published.append(record)
        col.module("company_site_activity", "available" if published else "not_available", items=len(published))
        return published


MONTHS = {"jan": 1, "januar": 1, "january": 1, "feb": 2, "februar": 2, "february": 2, "mar": 3, "mars": 3, "march": 3,
          "apr": 4, "april": 4, "mai": 5, "may": 5, "jun": 6, "juni": 6, "june": 6, "jul": 7, "juli": 7, "july": 7,
          "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10, "oktober": 10, "oct": 10, "october": 10,
          "nov": 11, "november": 11, "des": 12, "desember": 12, "dec": 12, "december": 12}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
TEXT_DATE_PATTERNS = [
    (re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)"), ("y", "m", "d")),
    (re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](20\d{2})(?!\d)"), ("d", "m", "y")),
    (re.compile(rf"(?<!\w)(\d{{1,2}})\.?\s+({_MONTH_RE})\.?,?\s+(20\d{{2}})(?!\d)", re.I), ("d", "M", "y")),
    (re.compile(rf"(?<!\w)({_MONTH_RE})\.?\s+(\d{{1,2}}),?\s+(20\d{{2}})(?!\d)", re.I), ("M", "d", "y")),
]
URL_DATE = [re.compile(r"/(20\d{2})/(\d{2})/(\d{2})/"), re.compile(r"/(20\d{2})(\d{2})(\d{2})[-_/]")]


def find_text_date(text: str) -> tuple[datetime, str] | None:
    for pattern, order in TEXT_DATE_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        parts = dict(zip(order, match.groups()))
        try:
            month = MONTHS[parts["M"].lower().rstrip(".")] if "M" in parts else int(parts["m"])
            parsed = datetime(int(parts["y"]), month, int(parts["d"]), tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if parsed <= datetime.now(timezone.utc) + timedelta(days=1):
            return parsed, match.group(0)
    return None


def url_date(url: str) -> datetime | None:
    for pattern in URL_DATE:
        match = pattern.search(url)
        if match:
            try:
                parsed = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
            except ValueError:
                continue
            if parsed <= datetime.now(timezone.utc) + timedelta(days=1):
                return parsed
    return None


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
            if not match:
                return None
            try:
                parsed = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed > datetime.now(timezone.utc) + timedelta(days=2) or parsed.year < 1990:
        return None
    return parsed


def _parse_feed(content: bytes) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    entries = []
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        record: dict[str, str] = {}
        for child in item:
            ctag = child.tag.split("}")[-1]
            if ctag == "title":
                record["title"] = (child.text or "").strip()
            elif ctag in ("pubDate", "published", "updated", "date") and "date" not in record:
                record["date"] = (child.text or "").strip()
            elif ctag == "link":
                record["link"] = (child.text or child.get("href") or "").strip()
        entries.append(record)
    return entries
