# Sources, rights and access policy

| Source | Access | Rights / basis | Used for |
|---|---|---|---|
| Brønnøysundregistrene Enhetsregisteret API (`data.brreg.no/enhetsregisteret/api`) | public JSON API | NLOD 2.0 | identity, roles, subunits, group structure, update stream |
| Regnskapsregisteret API (`data.brreg.no/regnskapsregisteret`) | public JSON API; filing-copy years limited to ~30 req/min (dedicated lane, 2.1 s spacing) | NLOD 2.0 | annual accounts, filing history, copy links |
| Wikidata SPARQL (`query.wikidata.org`) | public endpoint, ≤1 request/s, batched 200 organisations per query | CC0 | exact (P2333) identity bridge, website and social handles |
| Company websites | robots.txt honoured for our UA; ≤8 pages + 1 feed + ≤3 sitemap files per site; 2 concurrent / 0.25 s spacing per host | company-published public pages | official site, brand, description, contacts, profiles, careers, dated news |
| NAV arbeidsplassen (`arbeidsplassen.nav.no`) | robots.txt `Disallow:` empty (allowed); 2 concurrent, ≥0.5 s spacing, adaptive back-off | public job ads published by NAV | active job postings verified by employer organisation number |
| Optional Brave Search API | only with `BRAVE_SEARCH_API_KEY` | per provider terms; results transient | website candidates only |

Not used: LinkedIn, Meta, Glassdoor, Indeed, Google Search/Maps scraping, Proff/Purehelp/Gulesider, Norid WHOIS
(holder data not exposed; commercial use prohibited), `w2.brreg.no/kunngjoring` (disallowed by robots.txt).

Search results and directories are never evidence. Company-owned text is labelled self-reported and is not used as
independent sentiment.
