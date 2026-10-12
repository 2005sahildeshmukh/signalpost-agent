# Identity resolution

The organisation number is the only anchor. Names, domains and handles are candidates until the evidence ties them
back to that number. When the match is uncertain the agent returns `ambiguous` or `not_available`; it never guesses.

## Candidate generation (`website.py`)

| Order | Candidate | Why it is a candidate |
|---|---|---|
| 1 | `hjemmeside` in Enhetsregisteret | declared by the entity to the official register |
| 2 | P856 on the Wikidata item whose P2333 equals the organisation number | exact join on the number |
| 3 | domain of the registry e-mail address (free-mail providers excluded) | the entity's own declared contact domain |
| 4 | `hjemmeside` on registered subunits | declared for a registered establishment of the entity |
| 5 | `.no` / `.com` labels derived from the legal name (transliterated æøå, hyphenated, generic words removed) | only if DNS resolves |
| 6 | optional search API (only with an evaluator-supplied key) | transient candidates; raw results discarded |

## Proof on the site

The crawl fetches the homepage, then identity pages first (contact, about, privacy; conventional paths are probed if
the menu is script-rendered), then careers, news, team and locations pages (max eight pages per site).

Signals computed from fetched pages only:

- organisation number present (mod-11 validated; also in JSON-LD `vatID`/`taxID`) and whether it is in an org-number context
- other valid organisation numbers in an org-number context
- registry phone / mobile on the site (text or `tel:` links)
- registry e-mail domain equals the site domain or an e-mail on the site
- registry street token **and** postal code on the site
- full legal name (or former name) in the text; legal-name tokens in title, `og:site_name` or JSON-LD Organization name
- registered role holders (full names) on the site

## Verdicts

| Verdict | Rule | Published |
|---|---|---|
| `exact` (1.0) | organisation number on the site | URL + all company-owned facts |
| `exact` (0.95–0.97) | official declaration + name or corroboration on the site | URL + all company-owned facts |
| `exact` (0.93–0.96) | derived candidate + legal name + independent corroboration | URL + all company-owned facts |
| `declared_only` | official declaration, site reachable, no restatement | declared URL only (`ambiguous` for site facts) |
| `declared_related` | official declaration but the site shows another organisation number | declared URL only; likely parent/brand |
| `conflict` | derived candidate shows another organisation number | nothing |
| `name_only` | name matches, no corroboration | nothing (`ambiguous`) |
| `parked_or_placeholder`, `not_company_site`, `unreachable`, `blocked` | — | nothing |

## Other connectors

- **Social profiles:** only links found on a verified site (anchors, iframes, JSON-LD `sameAs`) whose handle matches the
  legal name, brand or domain, or handles listed on the Wikidata item matched by organisation number.
- **Jobs:** NAV search by name generates candidates; each ad page is fetched and accepted only when its
  `employer.orgnr` equals the entity or one of its registered subunits.
- **Batch guard:** a social profile or a non-org-number-proven website attributed to several entities in the same
  batch is demoted to `ambiguous` for all of them (agencies, shared group sites).
