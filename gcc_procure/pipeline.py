"""Automated research workflow (steps 1-10 in the brief).

discover -> resolve website -> crawl bounded set of official pages -> extract contacts ->
record sources -> validate/classify -> dedupe -> score -> save.
Resumable: every phase run is a job with per-company items.
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
import re
from pathlib import Path

from . import config, extract, scoring, search as search_mod
from .db import DB, now, today
from .fetcher import PoliteFetcher
from .normalize import (classify_designation, department_for, domain_of, name_key, normalize_email,
                        normalize_phone, normalize_url, same_site)
from .verify import email_status_unpublished

log = logging.getLogger("gcc_procure.pipeline")

SEED_FILES = ["uae.csv", "saudi.csv", "gcc_other.csv"]
PORTFOLIO_BY_CATEGORY = {
    config.CAT_SUPER: "food,nonfood,drugstore",
    config.CAT_FOOD: "food",
    config.CAT_NONFOOD: "nonfood",
    config.CAT_PHARMA: "drugstore,nonfood",
    config.CAT_WHOLESALE: "food,nonfood",
    config.CAT_DIST: "",
}
PHASE_OF = {}
for _p, _d in config.PHASES.items():
    for _c in _d["countries"]:
        for _cat in _d["categories"]:
            PHASE_OF.setdefault((_c, _cat), _p)


def phase_for(country: str, category: str) -> int | None:
    return PHASE_OF.get((country, category))


# ---------------------------------------------------------------------------------------------
# Step 1: seeding (user-supplied / curated lists - names only; nothing here is a contact)
# ---------------------------------------------------------------------------------------------
def load_seeds(db: DB, files: list[Path] | None = None) -> dict:
    files = files or [config.SEED_DIR / f for f in SEED_FILES]
    added = merged = 0
    for f in files:
        if not Path(f).exists():
            continue
        with open(f, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if not row.get("company_name") or row["company_name"].startswith("#"):
                    continue
                cat, country = row["category"].strip(), row["country"].strip()
                site = normalize_url(row.get("website", ""))
                cid, created = db.upsert_company(
                    row["company_name"].strip(), country,
                    parent_company=row.get("parent_company", "").strip(),
                    city=row.get("city", "").strip(),
                    business_category=cat, business_subcategory=row.get("subcategory", "").strip(),
                    website=site, website_status="seed_unverified" if site else "none",
                    trade_type=row.get("trade_type", "").strip(),
                    business_size=row.get("business_size", "").strip() or "Unknown",
                    product_categories=PORTFOLIO_BY_CATEGORY.get(cat, ""),
                    source_url=row.get("source", "curated seed list"), phase=phase_for(country, cat),
                    research_notes=_notes_for(cat),
                    placement_opportunity=_placement(cat, PORTFOLIO_BY_CATEGORY.get(cat, "")),
                )
                added += created
                merged += (not created)
    return {"added": added, "merged_duplicates": merged}


def _notes_for(cat: str) -> str:
    if cat == config.CAT_PHARMA:
        return "Target OTC / personal care / hygiene / general drugstore buyers only (no prescription medicines)."
    return ""


def _placement(cat: str, lines: str) -> str:
    names = {"food": "Food FMCG", "nonfood": "Non-Food FMCG", "drugstore": "Drug-store/OTC & personal care"}
    ls = [names[x] for x in lines.split(",") if x in names]
    if not ls:
        return "Distribution / re-selling partnership"
    return "Listing of " + ", ".join(ls) + (" via distributor/wholesale channel" if cat in (config.CAT_WHOLESALE, config.CAT_DIST)
                                            else " on retail shelves")


# ---------------------------------------------------------------------------------------------
# Steps 2-9: per-company research
# ---------------------------------------------------------------------------------------------
def _is_thin(pg) -> bool:
    return len(pg.text) < 300 and not any(pg.links.values())


def _brand_matches(name: str, pg) -> bool:
    toks = [t for t in name_key(name).split() if len(t) > 2]
    hay = (pg.title + " " + pg.text[:3000]).lower()
    return bool(toks) and toks[0] in hay


def _iso(country: str) -> str:
    return config.COUNTRIES.get(country, {}).get("iso", "AE")


def research_company(db: DB, fetcher: PoliteFetcher, company_id: int, provider=None, max_pages: int | None = None) -> dict:
    max_pages = max_pages or config.MAX_PAGES_PER_COMPANY
    c = db.get_company(company_id)
    summary = {"company": c["company_name"], "pages": 0, "emails": 0, "phones": 0, "people": 0, "status": ""}
    iso = _iso(c["country"])

    # Step 2: website
    website = c["website"]
    if not website and provider:
        found = search_mod.find_website(provider, c["company_name"], c["country"])
        if found:
            website = found[0]
            db.update_company(company_id, website=website, website_domain=domain_of(website),
                              website_status="seed_unverified", source_url=found[1])
    if not website:
        db.update_company(company_id, crawl_status="done", needs_manual=1, last_crawled=today(),
                          research_notes=_append(c["research_notes"], "No official website identified - manual research"))
        scoring.rescore(db, company_id)
        summary["status"] = "no_website"
        return summary

    # fetch homepage
    home = fetcher.fetch(website)
    summary["pages"] += 1
    if not home.ok:
        status = {"blocked": "blocked", "robots_disallowed": "blocked"}.get(home.status, "unreachable")
        # Transient/network failures are NOT marked as crawled, so the next run retries them.
        transient = status == "unreachable" and home.status == "error"
        db.update_company(company_id, website_status=status, crawl_status="failed" if transient else "done", needs_manual=1,
                          last_crawled="" if transient else today(),
                          research_notes=_append(c["research_notes"], f"Homepage not collected ({home.status}: {home.detail})"))
        scoring.rescore(db, company_id)
        summary["status"] = status
        summary["retry"] = transient
        return summary

    base = home.final_url or website
    pages = [extract.parse_page(home.text, base, iso)]
    rendered = False
    if _is_thin(pages[0]):   # JS-built site: render once with headless Chromium
        rr = fetcher.fetch_rendered(base)
        if rr.ok:
            pages = [extract.parse_page(rr.text, rr.final_url or base, iso)]
            rendered = True
        if _is_thin(pages[0]):
            db.update_company(company_id, website_status="js_required", crawl_status="done", needs_manual=1, last_crawled=today(),
                              research_notes=_append(c["research_notes"], "Website is JavaScript-built / empty to automated fetch - manual research"))
            scoring.rescore(db, company_id)
            summary["status"] = "js_required"
            return summary
    if not _brand_matches(c["company_name"], pages[0]):
        db.update_company(company_id, website_status="mismatch", crawl_status="done", needs_manual=1, last_crawled=today(),
                          research_notes=_append(c["research_notes"], "Website did not match company name - not harvested; confirm URL"))
        scoring.rescore(db, company_id)
        summary["status"] = "mismatch"
        return summary

    # Step 3: follow the highest-value internal pages (bounded)
    for u in extract.rank_candidate_pages(pages, base, max_pages - 1):
        r = fetcher.fetch_rendered(u) if rendered else fetcher.fetch(u)
        if not r.ok and not rendered and r.status == "ok":
            continue
        summary["pages"] += 1
        if r.ok:
            pages.append(extract.parse_page(r.text, r.final_url or u, iso))

    # Step 4-6: record contacts with source URLs, classify and validate
    site_domain = domain_of(base)
    n_e = n_p = n_pp = 0
    seen_e: set[str] = set()
    for pg in pages:
        for email, how in pg.emails:
            if email in seen_e:
                continue
            role = extract.classify_email_role(email)
            if role == "excluded":
                continue
            seen_e.add(email)
            official = _email_is_official(email, site_domain)
            kind = "department" if role in ("procurement", "sales") else "general"
            dept = {"procurement": "Procurement / Purchasing", "sales": "Commercial / Sales"}.get(role, "General enquiries")
            _, new = db.add_contact(
                company_id, "e:" + email, kind=kind, department=dept, email=email, email_role=role,
                email_status="published_official" if official else "published_third_party",
                contact_source=f"Company website ({how})", source_url=pg.url,
                verification_status="Verified - Published by company" if official else "Discovered - Needs Review",
                confidence=(80 if role == "procurement" else 70) if official else 40)
            n_e += new
        for ph in pg.phones[:3]:
            if db.conn.execute("SELECT 1 FROM contacts WHERE company_id=? AND dedupe_key=?", (company_id, "p:" + ph)).fetchone():
                continue
            if n_p >= 5:
                break
            _, new = db.add_contact(company_id, "p:" + ph, kind="general", department="Head office / general", phone=ph,
                                    phone_status="published_official", contact_source="Company website",
                                    source_url=pg.url, verification_status="Verified - Published by company", confidence=70)
            n_p += new
        for p in pg.people:
            tier = classify_designation(p["title"])
            if not tier:
                continue
            email = p.get("email", "")
            phone, ok = normalize_phone(p.get("phone", ""), iso) if p.get("phone") else ("", False)
            official = p["method"] in ("jsonld", "same_line")
            _, new = db.add_contact(
                company_id, "n:" + name_key(p["name"]) + "|" + name_key(p["title"]), kind="person",
                contact_name=p["name"], designation=p["title"], designation_tier=tier,
                department=department_for(p["title"]), email=email,
                email_role="named" if email else "", email_status=("published_official" if email else "none"),
                phone=phone, phone_status="published_official" if ok else "none",
                contact_source=f"Company website ({p['method']})", source_url=pg.url,
                verification_status="Verified - Published by company" if official else "Discovered - Needs Review",
                confidence=p["confidence"])
            n_pp += new

    # LinkedIn public-snippet people discovery (only if a search API key is configured)
    if provider:
        try:
            for p in search_mod.find_linkedin_people(provider, c["company_name"], c["country"]):
                db.add_contact(company_id, "n:" + name_key(p["name"]) + "|" + name_key(p["title"]), kind="person",
                               contact_name=p["name"], designation=p["title"],
                               designation_tier=classify_designation(p["title"]), department=department_for(p["title"]),
                               linkedin_url=p["linkedin_url"], contact_source=f"Search API snippet ({provider.name}) - LinkedIn public result",
                               source_url=p["source_url"], verification_status="Discovered - Needs Review", confidence=40)
                n_pp += 1
        except Exception as e:  # network/API error must not abort the company
            log.warning("linkedin snippet search failed for %s: %s", c["company_name"], e)

    # company-level facts
    def first(kind):
        for pg in pages:
            if pg.links.get(kind):
                return pg.links[kind][0]
        return ""

    contact_page = first("contact")
    sup_reg = first("supplier_reg")
    portal = first("procurement")
    sup_info = first("supplier_info")
    branches = next((pg.branches for pg in pages if pg.branches), None)
    snippet = next((pg.branch_snippet for pg in pages if pg.branches), "")
    li = next((pg.linkedin_company for pg in pages if pg.linkedin_company), "")
    detected = {k: sum(pg.category_hits.get(k, 0) for pg in pages) for k in ("food", "nonfood", "drugstore")}
    lines = set(filter(None, (c["product_categories"] or "").split(",")))
    lines |= {k for k, v in detected.items() if v >= 3}
    notes = c["research_notes"] or ""
    if sup_info and not sup_reg:
        notes = _append(notes, f"Supplier page: {sup_info}")
    if branches:
        notes = _append(notes, f"Website claims {branches} stores/branches (may be group-wide): '{snippet[:80]}'")
    db.update_company(
        company_id, website_status="verified", crawl_status="done", last_crawled=today(), last_verified=today(),
        contact_page_url=contact_page or c["contact_page_url"], supplier_registration_url=sup_reg or c["supplier_registration_url"],
        procurement_portal_url=portal or c["procurement_portal_url"], linkedin_company_url=li or c["linkedin_company_url"],
        branches=branches if branches else c["branches"], product_categories=",".join(sorted(lines)),
        research_notes=notes, source_url=base if not c["source_url"] or c["source_url"] == "curated seed list" else c["source_url"],
        placement_opportunity=_placement(c["business_category"], ",".join(sorted(lines))))

    score, label = scoring.rescore(db, company_id)
    has_buyer = db.conn.execute(
        "SELECT 1 FROM contacts WHERE company_id=? AND (designation_tier IN (1,2) OR email_role='procurement')", (company_id,)).fetchone()
    db.update_company(company_id, needs_manual=0 if has_buyer else 1)
    summary.update(emails=n_e, phones=n_p, people=n_pp, status="ok", score=score, priority=label)
    return summary


def _append(base: str, extra: str) -> str:
    base = base or ""
    return extra if not base else (base if extra in base else f"{base} | {extra}")


def _email_is_official(email: str, site_domain: str) -> bool:
    d = email.split("@", 1)[1]
    return d == site_domain or d.endswith("." + site_domain) or site_domain.endswith("." + d)


# ---------------------------------------------------------------------------------------------
# Phase runner with resume + incremental skip
# ---------------------------------------------------------------------------------------------
def run_phase(db: DB, fetcher: PoliteFetcher, phase: int, provider=None, force: bool = False,
              max_age_days: int | None = None, limit: int | None = None, on_progress=None) -> dict:
    max_age_days = config.RECRAWL_AFTER_DAYS if max_age_days is None else max_age_days
    spec = config.PHASES[phase]
    job_id, resumed = db.open_job(phase)
    if not resumed:
        for co in db.companies("phase=?", (phase,)):
            db.conn.execute("INSERT OR IGNORE INTO job_items(job_id,company_id,status,updated) VALUES (?,?, 'pending', ?)",
                            (job_id, co["id"], now()))
        db.conn.commit()
    items = db.conn.execute("SELECT company_id FROM job_items WHERE job_id=? AND status IN ('pending','failed') ORDER BY company_id",
                            (job_id,)).fetchall()
    if limit:
        items = items[:limit]
    cutoff = dt.date.today() - dt.timedelta(days=max_age_days)
    stats = {"job_id": job_id, "resumed": resumed, "processed": 0, "skipped": 0, "failed": 0}
    try:
        for i, it in enumerate(items, 1):
            cid = it["company_id"]
            co = db.get_company(cid)
            if not force and co["last_crawled"] and dt.date.fromisoformat(co["last_crawled"]) > cutoff:
                _mark(db, job_id, cid, "skipped", "recently crawled")
                stats["skipped"] += 1
                continue
            try:
                res = research_company(db, fetcher, cid, provider)
                if res.get("retry"):
                    _mark(db, job_id, cid, "failed", res["status"])
                    stats["failed"] += 1
                else:
                    _mark(db, job_id, cid, "done", res["status"])
                    stats["processed"] += 1
            except Exception as e:
                log.exception("company %s failed", co["company_name"])
                db.update_company(cid, crawl_status="failed")
                _mark(db, job_id, cid, "failed", f"{type(e).__name__}: {e}")
                stats["failed"] += 1
                res = {"company": co["company_name"], "status": "failed"}
            if on_progress:
                on_progress(i, len(items), res)
    except KeyboardInterrupt:
        db.conn.execute("UPDATE jobs SET status='interrupted' WHERE id=?", (job_id,))
        db.conn.commit()
        stats["interrupted"] = True
        return stats
    remaining = db.conn.execute("SELECT COUNT(*) FROM job_items WHERE job_id=? AND status IN ('pending','failed')",
                                (job_id,)).fetchone()[0]
    if remaining == 0:
        db.conn.execute("UPDATE jobs SET status='done', finished=? WHERE id=?", (now(), job_id))
    else:
        db.conn.execute("UPDATE jobs SET status='interrupted' WHERE id=?", (job_id,))
    db.conn.commit()
    return stats


def _mark(db, job_id, cid, status, error=""):
    db.conn.execute("UPDATE job_items SET status=?, error=?, updated=? WHERE job_id=? AND company_id=?",
                    (status, error, now(), job_id, cid))
    db.conn.commit()


def discover_phase(db: DB, provider, phase: int, per_query_limit: int = 15) -> dict:
    """Optional: use a search API to find additional companies for a phase. Results are flagged for review."""
    spec = config.PHASES[phase]
    added = 0
    for country in spec["countries"]:
        for cat in spec["categories"]:
            for cand in search_mod.discover_companies(provider, cat, country)[:per_query_limit]:
                _, created = db.upsert_company(
                    cand["name"], country, business_category=cat, website=cand["website"], website_status="seed_unverified",
                    business_size="Unknown", product_categories=PORTFOLIO_BY_CATEGORY.get(cat, ""), source_url=cand["source_url"],
                    phase=phase, research_notes=f"Auto-discovered via search '{cand['query']}' - confirm relevance",
                    placement_opportunity=_placement(cat, PORTFOLIO_BY_CATEGORY.get(cat, "")), confidence=10)
                added += created
    return {"added": added}


def phase_status(db: DB) -> list[dict]:
    out = []
    for p, spec in config.PHASES.items():
        total = db.conn.execute("SELECT COUNT(*) FROM companies WHERE phase=?", (p,)).fetchone()[0]
        done = db.conn.execute("SELECT COUNT(*) FROM companies WHERE phase=? AND crawl_status='done'", (p,)).fetchone()[0]
        failed = db.conn.execute("SELECT COUNT(*) FROM companies WHERE phase=? AND crawl_status='failed'", (p,)).fetchone()[0]
        job = db.conn.execute("SELECT status FROM jobs WHERE phase=? ORDER BY id DESC LIMIT 1", (p,)).fetchone()
        out.append({"phase": p, "name": spec["name"], "companies": total, "researched": done, "failed": failed,
                    "job": job["status"] if job else "not started"})
    return out
