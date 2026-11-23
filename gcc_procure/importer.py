"""Import contacts / companies gathered by a human (LinkedIn manual research, trade-show exhibitor lists,
supplier-portal enquiries, existing CRM). Everything goes through the same validation, dedupe and scoring."""
from __future__ import annotations

import csv

from . import config, scoring
from .db import DB
from .normalize import (classify_designation, clean_name, department_for, name_key, normalize_email, normalize_phone,
                        normalize_url)
from .pipeline import PORTFOLIO_BY_CATEGORY, _placement, phase_for
from .search import linkedin_search_url
from .verify import email_status_unpublished

ALIASES = {
    "company": ["company", "company_name", "company name", "organisation", "organization"],
    "country": ["country"], "city": ["city", "emirate", "emirate / city"],
    "category": ["category", "business_category", "business category"],
    "website": ["website", "company website", "url"],
    "name": ["name", "contact", "contact_name", "contact person name", "contact person"],
    "designation": ["designation", "title", "job title", "job designation"],
    "email": ["email", "professional email", "business email"],
    "phone": ["phone", "business phone number", "telephone", "mobile"],
    "linkedin": ["linkedin", "linkedin profile", "linkedin_url"],
    "source": ["source", "contact source", "source_url", "source url"],
}


def _get(row: dict, key: str) -> str:
    low = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
    for a in ALIASES[key]:
        if low.get(a):
            return low[a]
    return ""


def import_contacts(db: DB, path: str, source_label: str = "") -> dict:
    stats = {"rows": 0, "contacts_added": 0, "merged": 0, "rejected": 0, "companies_created": 0, "errors": []}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            stats["rows"] += 1
            company, country = _get(row, "company"), _get(row, "country") or "UAE"
            source = _get(row, "source") or source_label
            if not company or country not in config.COUNTRIES:
                stats["rejected"] += 1
                stats["errors"].append(f"row {n}: missing company or unknown country '{country}'")
                continue
            if not source:
                stats["rejected"] += 1
                stats["errors"].append(f"row {n}: no source recorded (every lead needs a source)")
                continue
            cat = _get(row, "category")
            cid, created = db.upsert_company(
                company, country, city=_get(row, "city"), business_category=cat, website=normalize_url(_get(row, "website")),
                website_status="seed_unverified" if _get(row, "website") else "none",
                product_categories=PORTFOLIO_BY_CATEGORY.get(cat, ""), phase=phase_for(country, cat) if cat else None,
                source_url=source if source.startswith("http") else "", placement_opportunity=_placement(cat, PORTFOLIO_BY_CATEGORY.get(cat, "")))
            stats["companies_created"] += created
            email_raw = _get(row, "email")
            email = normalize_email(email_raw)
            if email_raw and not email:
                stats["errors"].append(f"row {n}: invalid email '{email_raw}' dropped")
            phone, pok = normalize_phone(_get(row, "phone"), config.COUNTRIES[country]["iso"]) if _get(row, "phone") else ("", False)
            name, desig = clean_name(_get(row, "name")), clean_name(_get(row, "designation"))
            if not (name or email or phone):
                stats["rejected"] += 1
                continue
            tier = classify_designation(desig)
            key = ("e:" + email) if email else ("n:" + name_key(name) + "|" + name_key(desig)) if name else "p:" + phone
            _, new = db.add_contact(
                cid, key, kind="person" if name else "department", contact_name=name, designation=desig,
                designation_tier=tier, department=department_for(desig), linkedin_url=_get(row, "linkedin"),
                email=email, email_role="named" if (email and name) else ("" if not email else _role(email)),
                email_status=email_status_unpublished(email) if email else "none",
                phone=phone, phone_status="format_valid" if pok else "none",
                contact_source=f"Manual import: {source_label or source}", source_url=source if source.startswith("http") else "",
                verification_status="Imported - Unverified", confidence=50 if email_raw and email else 35)
            stats["contacts_added" if new else "merged"] += 1
            scoring.rescore(db, cid)
            db.update_company(cid, needs_manual=0 if tier in (1, 2) else db.get_company(cid)["needs_manual"])
    return stats


def _role(email: str) -> str:
    from .extract import classify_email_role
    return classify_email_role(email)


def manual_research_queue(db: DB) -> list[dict]:
    """Companies that need a human: no website, unreachable/blocked/mismatched sites, or no buyer contact."""
    rows = []
    for c in db.companies("needs_manual=1 OR crawl_status IN ('pending','failed')"):
        reasons = []
        if not c["website"]:
            reasons.append("No official website identified")
        elif c["website_status"] in ("unreachable", "blocked", "mismatch"):
            reasons.append(f"Website {c['website_status']}")
        contacts = db.contacts_for(c["id"])
        if not any(k["designation_tier"] in (1, 2) or k["email_role"] == "procurement" for k in contacts):
            reasons.append("No procurement/buyer contact found")
        if c["crawl_status"] in ("pending", "failed"):
            reasons.append(f"Crawl {c['crawl_status']}")
        rows.append({
            "Company Name": c["company_name"], "Country": c["country"], "City": c["city"], "Category": c["business_category"],
            "Website": c["website"], "Reason(s)": "; ".join(reasons) or "Review", "Lead Score": c["lead_score"],
            "LinkedIn People Search (open manually)": linkedin_search_url(c["company_name"]),
            "Web Search (open manually)": "https://www.google.com/search?q=" + __import__("urllib.parse").parse.quote_plus(
                f'"{c["company_name"]}" {c["country"]} procurement OR "category buyer" OR "purchase manager" OR "supplier registration"'),
            "Notes": c["research_notes"]})
    return sorted(rows, key=lambda r: -r["Lead Score"])


def forget(db: DB, email: str, reason: str = "removal request") -> int:
    """Honour a removal request: delete and suppress so it is not re-collected."""
    email = email.strip().lower()
    n = db.conn.execute("DELETE FROM contacts WHERE email=?", (email,)).rowcount
    db.conn.execute("INSERT OR REPLACE INTO suppression(email,reason,added) VALUES (?,?,datetime('now'))", (email, reason))
    db.conn.commit()
    return n
