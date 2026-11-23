# GCC Retail Procurement Intelligence — AWS Distribution UAE

Built on the Scout repository. Finds supermarkets, pharmacies, wholesalers and distributors across the GCC and records
**publicly published** business contacts for procurement decision-makers, each with its source URL and verification status.

> Principle: accurate, actionable leads over volume. Nothing is guessed. If a contact is not published, it is not in the database —
> the company goes to the **manual research queue** instead.

## 1. Scout audit (what exists, what is reused)

| Area | Finding |
|---|---|
| Purpose | CLI lead tool for **social-media profiles** (Instagram, TikTok, LinkedIn, GitHub, YouTube, Twitch, Pinterest, Linktree). MIT licensed. |
| Architecture | `scout.py` (Rich interactive menu) + `app/scrapers/*` (one module per platform) + `enrichment.py` (`LeadEnricher`) + `stealth.py` (UA/proxy rotation). |
| Gaps for this project | No company/retailer discovery, no database, no dashboard, no xlsx export, no GCC phone handling (phone regexes are US-centric), no robots.txt handling, no resume/incremental logic. |
| Reused | `EMAIL_BLACKLIST` / `FILE_EXT_BLACKLIST` noise filters (`app/scrapers/enrichment.py`), the "crawl homepage + /contact + /about" approach, DNS-MX idea, `requests`/`rich`/`dnspython` deps, MIT licence. |
| Deliberately **not** reused | LinkedIn cookie scraper & proxy/UA rotation (`stealth.py`) — these bypass platform restrictions. SMTP-probing and email *pattern guessing* in `LeadEnricher` — guessed addresses are not "publicly available" and are excluded by your no-fabrication rule. |

## 2. Architecture

```
procure.py ─► gcc_procure/cli.py
                │
 seeds/*.csv ─► pipeline.load_seeds ─► SQLite (data/procurement.db)
 search API ──► search.py (optional: Brave / SerpAPI; metadata only)
                │
                ▼
 pipeline.run_phase (job + per-company items, resumable, incremental)
   fetcher.PoliteFetcher  robots.txt · per-host rate limit · retries/backoff · no bypass of 401/403/429/CAPTCHA
   extract.parse_page     emails (mailto/text/Cloudflare-encoded) · phones (libphonenumber) · JSON-LD people ·
                          team-page people · supplier-registration / procurement-portal / contact links · branch count
   verify.py              syntax + DNS MX only
   normalize.py           email / E.164 phone / company-name key / URL / designation tiers
   scoring.py             0-100 rubric + priority bands
   db.py                  upsert + duplicate merge + suppression list
 importer.py ─► manual CSV import (LinkedIn research done by a human, exhibitor lists, CRM) — same validation & scoring
 exporter.py ─► .xlsx (8 sheets + Manual Research) · CSV · Google-Sheets CSV
 dashboard.py ─► Streamlit (KPIs, filters, charts, tables, downloads, progress)
```

### Verification statuses (be strict about what "verified" means)
| Field | Value | Meaning |
|---|---|---|
| Verification Status | `Verified - Published by company` | Found on the company's own website (structured data or "Name – Title" on one line) |
| | `Discovered - Needs Review` | Heuristic pairing (name on line above title), email on a different domain, or a search-API LinkedIn snippet. **Check before outreach.** |
| | `Imported - Unverified` | Added by hand from another source |
| Email status | `published_official` / `published_third_party` / `mx_valid` / `no_mx` | Where it was seen; MX = the domain can receive mail (mailbox existence is **not** tested) |
| Phone status | `published_official` / `format_valid` | Valid per libphonenumber |

### Lead score (0-100)
Retail relevance 25 · Product-category fit 25 · Procurement-contact availability 20 · Verified email/phone 15 · Supplier registration/purchasing info 15.
80-100 High · 60-79 Medium · 40-59 Low · <40 Research Required. The per-component breakdown is stored in `score_breakdown`.

## 3. Setup

```bash
git clone <this repo> && cd scout
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PROCURE_USER_AGENT="AWS-Distribution-ProcurementResearch/1.0 (+https://your-site; you@yourcompany.com)"   # identify yourself
# optional (only enables website lookup + new-company discovery + LinkedIn *snippets* via search API):
export BRAVE_API_KEY=...        # or SERPAPI_KEY=...
python -m pytest tests -q       # 23 tests, no network needed
```

Settings (env vars): `PROCURE_MIN_DELAY` (2.5 s/host), `PROCURE_MAX_PAGES` (8/company), `PROCURE_RECRAWL_DAYS` (30), `PROCURE_DB`, `PROCURE_EXPORT_DIR`.

## 4. Running (batch by phase)

```bash
python procure.py init                       # create DB, load seed companies (names only - no contacts)
python procure.py run --phase 1 --limit 5    # trial: UAE supermarkets, 5 companies
python procure.py run --phase 1              # full phase. Ctrl-C any time; re-run the same command to RESUME
python procure.py status                     # progress by phase
python procure.py run --phase 2 --discover   # find more companies via search API (needs key); flagged for review
python procure.py import-contacts my_linkedin_research.csv --source "LinkedIn manual research"
python procure.py export                     # exports/gcc_procurement_leads.xlsx + csv/ + google_sheets/
python procure.py manual-queue               # companies needing a human
python procure.py dashboard                  # http://localhost:8501
python procure.py forget someone@company.ae  # honour a removal request (deleted + suppressed)
```
Phases: 1 UAE supermarkets · 2 UAE food FMCG · 3 UAE non-food FMCG · 4 UAE pharmacies · 5 UAE wholesalers/distributors · 6 Saudi Arabia · 7 Oman/Qatar/Kuwait/Bahrain.
Re-runs are incremental: companies crawled in the last 30 days are skipped (`--force` overrides). Network errors are retried on the next run, not recorded as crawled.

**Adding companies:** edit `gcc_procure/seeds/*.csv` (columns: company_name, country, city, category, subcategory, trade_type, parent_company, website, business_size, source), then `init` again (merges, never duplicates). Phases 2, 3 and 5 ship empty on purpose — populate them with `--discover`, exhibitor lists (`import-contacts`) or your own list rather than unverified guesses.

**Import CSV columns** (aliases accepted): Company, Country, City, Category, Website, Name, Designation, Email, Phone, LinkedIn, **Source** (required — rows without a source are rejected).

## 5. LinkedIn & other restricted platforms
The system never logs into or scrapes LinkedIn, Google Maps or directories that disallow automation. Instead the **Manual Research** sheet gives, per company, a ready-made LinkedIn people-search link and a web-search link for a human to open. Whatever the researcher finds can be loaded with `import-contacts`. If you have a Brave/SerpAPI key, public LinkedIn *search-result snippets* (name/title/company) can be recorded as `Discovered - Needs Review`.

## 6. Compliance notes (not legal advice)
- Only business contacts that the company itself publishes, or that a human researcher provides with a source. No guessed or pattern-generated addresses, no SMTP probing.
- robots.txt is honoured; 401/403/429/CAPTCHA pages are logged as `blocked` and skipped, never bypassed. One request every 2.5 s per host.
- Pharmacy targeting is limited to OTC / personal-care / hygiene buyers; prescription-related roles (e.g. pharmacists) are excluded.
- UAE PDPL (Federal Decree-Law 45/2021), Saudi PDPL and similar GCC laws apply to personal data even in a B2B context; PDPL and anti-spam rules also apply to outreach. Keep a lawful basis, identify yourself, offer opt-out, and use `forget` for removal requests. Have counsel confirm your outreach process.
- Every contact row keeps `Source URL`, `Date Collected`, `Last Verified Date`; the `fetch_log` table records every request.

## 7. Known limits
- SQLite only (PostgreSQL not implemented; schema is plain SQL and portable).
- Static HTML only (no Playwright/JS rendering yet) — JS-only sites land in the manual queue.
- Team/leadership extraction is heuristic; those rows are flagged `Needs Review`.
- Seed websites are unverified until crawled; a site whose title/body lacks the brand name is stored as `mismatch` and not harvested.
