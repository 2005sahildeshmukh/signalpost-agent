# Limitations

- **Website discovery without a search engine.** Without a licensed search API the agent finds sites only through
  official declarations, Wikidata, the registry e-mail domain, subunit declarations and name-derived domains. Brands
  that differ from the legal name are found only when declared somewhere official. Supplying `BRAVE_SEARCH_API_KEY`
  adds search candidates behind the same identity gate.
- **Strict gate costs recall.** Sites that never show the organisation number, phone or address are left `ambiguous`
  even when they are probably right. This is deliberate: a wrong-company match is worse than a miss.
- **JavaScript-only sites** are read from their server HTML; no headless browser is used.
- **Jobs** cover active ads on NAV arbeidsplassen (which also carries FINN-sourced ads) plus JobPosting markup on
  verified sites. Ads posted elsewhere only are missed.
- **Registry update stream** gives dated change events but not which field changed.
- **Financial institutions** sometimes return HTTP 500 from Regnskapsregisteret; this is reported as `failed`, never zero.
- **No sentiment, reviews or social engagement metrics**: no permitted, keyless source with exact entity matching was available.
- **Filing-copy years** use a rate-limited endpoint; on very large batches some companies may get `failed` for that
  module when the time budget runs out (the normalized accounts are unaffected).
