# Smoke test — smoke-100

- Command: `uv run signalpost run --input smoke-100.jsonl --output-dir smoke --run-id smoke-100 --expected-count 100`
- Started 2026-10-14T13:49:05Z, completed 2026-10-14T13:52:40Z (215.5 s wall clock)
- Inputs: 100 · envelopes emitted: 100 · terminal states: {'completed': 100}
- Contract validation: **passed** — one_envelope_per_input=True, expected_count_matches=True, unique_organisation_numbers=True, terminal_status_valid=True, module_states_valid=True, section_states_valid=True, every_available_claim_has_evidence=True, every_evidence_has_retrieval_time=True
- Published claims: 8436 · requests: 1393 · p50/p95 per company: 6584 / 62076 ms
- Third-party cost: USD 0.0 · language-model calls: 0

## Coverage by field family

| Family | Companies with data | % | Facts | States |
|---|---:|---:|---:|---|
| legal_identity | 100 | 100% | 100 | available: 100 |
| public_brand | 11 | 11% | 11 | ambiguous: 1, available: 11, not_available: 88 |
| annual_accounts | 100 | 100% | 277 | available: 100 |
| account_history | 100 | 100% | 100 | available: 100 |
| leadership | 100 | 100% | 386 | available: 100 |
| registered_workplaces | 73 | 73% | 76 | available: 73, not_available: 27 |
| official_website | 12 | 12% | 12 | ambiguous: 27, available: 12, blocked: 2, not_available: 59 |
| social_profiles | 3 | 3% | 8 | ambiguous: 28, available: 3, blocked: 2, not_available: 67 |
| contact_details | 7 | 7% | 12 | ambiguous: 1, available: 7, not_available: 92 |
| job_postings | 0 | 0% | 0 | not_available: 100 |
| careers_channel | 0 | 0% | 0 | ambiguous: 1, not_available: 99 |
| company_news | 3 | 3% | 28 | ambiguous: 28, available: 3, blocked: 2, not_applicable: 59, not_available: 8 |
| registry_activity | 100 | 100% | 644 | available: 100 |

## Website identity

- Verdicts on published sites: {'exact': 11, 'declared_only': 1}
- How the published sites were found: {'registry_email_domain': 2, 'registry_declared': 4, 'name_derived_domain': 6}

| Org. no. | Legal name | Website | Identity proof |
|---|---|---|---|
| 985589003 | ARKITEKTFIRMA JON VIKØREN AS | https://www.arkjv.no/ | legal name on site plus address_match |
| 888567232 | AAS ELEKTRONIKK AS | http://www.aelektronikk.no/ | registry declared website corroborated on the site by phone_match |
| 982942942 | BRANDMAKER AS | https://brandmaker.no/ | legal name on site plus phone_match, address_match |
| 926790137 | MUSIKK I INNLANDET AS | https://musikkinnlandet.no/ | the exact organisation number appears on the site in an org-number context |
| 935013623 | MINKRYPTO AS | https://www.minkrypto.no/ | the exact organisation number appears on the site |
| 999288448 | BÆRUM EIENDOMSTAKSERING AS | http://www.betakst.no/ | declared by an official source, but the site does not restate this entity with local evidence (could be a grou |
| 827340162 | NOVOOI AS | https://novooi.com/ | the exact organisation number appears on the site in an org-number context |
| 988573949 | CARLIN INVEST AS | https://www.carlin.no/ | registry declared website corroborated on the site by phone_match, address_match |
| 933787141 | AIOTA AS | https://www.aiota.no/ | the exact organisation number appears on the site in an org-number context |
| 933989208 | A-SEAFOOD AS | https://a-seafood.com/ | the exact organisation number appears on the site in an org-number context |
| 927356783 | GANGSTAD BYGG AS | http://gangstadbygg.no/ | legal name on site plus email_domain_match |
| 924585463 | RB PROFF PARTNER AS | https://www.rbproffpartner.no/ | the exact organisation number appears on the site in an org-number context |

## Module states

| Module | State | Companies |
|---|---|---:|
| annual_account_copies | available | 100 |
| annual_accounts | available | 100 |
| company_site_activity | ambiguous | 28 |
| company_site_activity | available | 3 |
| company_site_activity | blocked | 2 |
| company_site_activity | not_applicable | 59 |
| company_site_activity | not_available | 8 |
| nav_jobs | not_available | 100 |
| registry_entity | available | 100 |
| registry_group | available | 8 |
| registry_group | not_available | 92 |
| registry_roles | available | 100 |
| registry_updates | available | 100 |
| registry_workplaces | available | 73 |
| registry_workplaces | not_available | 27 |
| website | ambiguous | 27 |
| website | available | 12 |
| website | blocked | 2 |
| website | not_available | 59 |
| website_identity | ambiguous | 1 |
| website_identity | available | 11 |
| wikidata | available | 1 |
| wikidata | not_available | 99 |

## Example summaries

**MUSIKK I INNLANDET AS (926790137)** — MUSIKK I INNLANDET AS (public brand “Musikk Innlandet”) is a private limited company (AS) based in Gjøvik, founded 2021-01-28, registered in the industry “Utøvende kunstnerisk virksomhet og underholdningsvirksomhet innenfor musikk” (90.201). Its verified website (https://musikkinnlandet.no/) describes the business as: “Lurer du på hva som skjer i Hamar, Lillehammer, Gjøvik og Innlandet? Finn konserter og unike musikkopplevelser med Musikk i Innlandet.” (self-reported). The register records its purpose/activity as: “Musikk i Innlandet er et samfunnsnyttig selskap som skal utvikle, produsere og formidle musikk på profesjonelt nivå i samarbeid med aktører i hele Innlandet fylke, nasjonalt og internasjonalt. Selskapet har ikke som…”. Its latest filed company accounts (period ending 2025-12-31) show revenue of NOK 17.4 m (-3% vs 2024, computed from filed figures), an operating result of NOK 4 k, an annual result of NOK 0, equity of NOK 1.0 m. Normalized accounts are available for 2023, 2024, 2025. Annual-account copies are on file for 5 years (2021–2025). Registered leadership: Solbjørg Tveiten (CEO (daglig leder)); Even Aleksander Hagen (chair of the board); TELL NORGE AS (auditor); the board has 5 members. 5 registered role holder(s) are also named on the company's own website. The register reports 10 employees. It has 1 registered workplace in Gjøvik. No active job ads with its organisation number were found in NAV's public job feed at retrieval time. Latest dated activity on its website: “Vi trenger et nasjonalt musikkløft!” (2026-10-08), and 9 more item(s). Company-owned profiles verified on: facebook, instagram, linkedin.

**NOVOOI AS (827340162)** — NOVOOI AS is a private limited company (AS) based in Oslo, founded 2021-05-09, registered in the industry “Industri-, produkt- og motedesignvirksomhet” (74.110). Its verified website (https://novooi.com/) describes the business as: “Hospitality at Work Gjør arbeidsplassen personlig Hospitality at Work Gjør arbeidsplassen personlig NORSKE DESIGNMØBLER The Royal Norwegian Embassy in” (self-reported). The register records its purpose/activity as: “Selge varer og tjenester innen design og håndverk - og annet som naturlig sammenfaller med dette - samt deltakelse i andre selskaper, investeringer og nærliggende virksomhet.”. Its latest filed company accounts (period ending 2025-12-31) show revenue of NOK 541 k (+192% vs 2024, computed from filed figures), an operating result of NOK -2.2 m, an annual result of NOK -2.2 m, equity of NOK 508 k. Normalized accounts are available for 2023, 2024, 2025. Annual-account copies are on file for 5 years (2021–2025). Registered leadership: Lillian Ayla B Ersoy (CEO (daglig leder)); Peter Torgeir Nylund (chair of the board); the board has 5 members. It has 1 registered workplace in Oslo. No active job ads with its organisation number were found in NAV's public job feed at retrieval time. Latest dated activity on its website: “Design Dialogues Afterwork” (2026-10-07), and 9 more item(s). Company-owned profiles verified on: facebook, instagram, linkedin. The latest filed annual result was a loss.

**ARKITEKTFIRMA JON VIKØREN AS (985589003)** — ARKITEKTFIRMA JON VIKØREN AS is a private limited company (AS) based in Vik, founded 2003-02-05, registered in the industry “Arkitektvirksomhet” (71.110). Its verified website (https://www.arkjv.no/) describes the business as: “Powered by SmugMug” (self-reported). The register records its purpose/activity as: “Yta rådgjevande arkitektfaglege tenester. Selskapet kan også gjennom aksjekjøp eller på andre måtar engasjera seg i andre føretak uansett føremål og selskapsform.”. Its latest filed company accounts (period ending 2025-12-31) show revenue of NOK 4.2 m (-11% vs 2024, computed from filed figures), an operating result of NOK -127 k, an annual result of NOK 5 k, equity of NOK 3.1 m. Normalized accounts are available for 2023, 2024, 2025. Annual-account copies are on file for 15 years (2011–2025). Registered leadership: Lin Marie Vikøren (CEO (daglig leder)); Lin Marie Vikøren (chair of the board); the board has 2 members. It has 1 registered workplace in Vik. No active job ads with its organisation number were found in NAV's public job feed at retrieval time.

**BRANDMAKER AS (982942942)** — BRANDMAKER AS (public brand “Brandmaker.no”) is a private limited company (AS) based in Bergen, founded 2000-12-28, registered in the industry “Grafisk design og visuell kommunikasjonsdesign” (74.120). Its verified website (https://brandmaker.no/) describes the business as: “Webdesign og Emballasjesdesign i Bergen. Brandmaker designer nettsider, 3D animasjoner, nettbutikk reklame og grafisk design.” (self-reported). The register records its purpose/activity as: “Reklamebyrå og annen næringsvirksomhet.”. Its latest filed company accounts (period ending 2025-12-31) show revenue of NOK 212 k (+11% vs 2024, computed from filed figures), an operating result of NOK 20 k, an annual result of NOK 15 k, equity of NOK 163 k. Normalized accounts are available for 2023, 2024, 2025. Annual-account copies are on file for 15 years (2011–2025). Registered leadership: Jarle Indrøy (CEO (daglig leder)); Jarle Indrøy (chair of the board); the board has 1 member. 1 registered role holder(s) are also named on the company's own website. It has 1 registered workplace in Bergen. No active job ads with its organisation number were found in NAV's public job feed at retrieval time.

**MINKRYPTO AS (935013623)** — MINKRYPTO AS (public brand “MinKrypto V2”) is a private limited company (AS) based in Oslo, founded 2025-01-07, registered in the industry “Dataprogrammeringstjenester” (62.100). Its verified website (https://www.minkrypto.no/) describes the business as: “MinKrypto hjelper deg å rapportere skatt på krypto. Vårt verktøy er tilpasset norske forhold og utviklet i dialog med Skatteetaten.” (self-reported). The register records its purpose/activity as: “Salg av softwaretjenester.”. Its latest filed company accounts (period ending 2025-12-31) show revenue of NOK 0, an operating result of NOK -36 k, an annual result of NOK -36 k, equity of NOK -12 k. Annual-account copies are on file for 1 years (2025–2025). Registered leadership: Olav Sæle Bjørlykke (chair of the board); the board has 3 members. It has 1 registered workplace in Oslo. No active job ads with its organisation number were found in NAV's public job feed at retrieval time. Latest dated activity on its website: “Forvaltningskostnader - det du trenger å vite for å få riktig fradrag” (2025-03-01), and 7 more item(s). Equity was negative in the latest filed accounts. The latest filed annual result was a loss.

