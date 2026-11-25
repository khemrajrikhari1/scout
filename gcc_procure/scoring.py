"""Lead scoring (0-100) using the agreed rubric:

  Retail business relevance ............ 25
  Relevant product categories .......... 25
  Procurement contact availability ..... 20
  Verified business email / phone ...... 15
  Supplier registration / purchasing ... 15
"""
from __future__ import annotations

from . import config

CORE_CATEGORIES = {config.CAT_SUPER, config.CAT_PHARMA, config.CAT_WHOLESALE, config.CAT_FOOD, config.CAT_NONFOOD}


def priority_label(score: int) -> str:
    if score >= 80:
        return "High Priority"
    if score >= 60:
        return "Medium Priority"
    if score >= 40:
        return "Low Priority"
    return "Research Required"


def _relevance(c) -> int:
    cat, trade = c["business_category"], c["trade_type"]
    if cat in CORE_CATEGORIES:
        pts = 25
    elif cat == config.CAT_DIST:
        pts = 20
    elif cat:
        pts = 12
    else:
        pts = 5
    if trade == "" and pts > 5:
        pts -= 3
    return pts


def _products(c) -> int:
    lines = {x for x in (c["product_categories"] or "").split(",") if x}
    return {0: 0, 1: 12, 2: 19}.get(len(lines), 25)


def _contact_availability(contacts) -> int:
    best = 0
    for k in contacts:
        if k["kind"] == "person":
            pts = {1: 20, 2: 15, 3: 10}.get(k["designation_tier"], 3)
        elif k["email_role"] == "procurement":
            pts = 14
        elif k["email_role"] in ("sales", "named"):
            pts = 7
        elif k["email"] or k["phone"]:
            pts = 4
        else:
            pts = 0
        best = max(best, pts)
    return best


def _verified(contacts) -> int:
    email_pts = phone_pts = 0
    for k in contacts:
        if k["email"]:
            relevant = k["kind"] == "person" or k["email_role"] in ("procurement", "sales")
            if k["email_status"] == "published_official":
                email_pts = max(email_pts, 10 if relevant else 7)
            elif k["email_status"] in ("published_third_party", "mx_valid"):
                email_pts = max(email_pts, 3)
        if k["phone"]:
            if k["phone_status"] == "published_official":
                phone_pts = max(phone_pts, 5)
            elif k["phone_status"] in ("published_third_party", "format_valid"):
                phone_pts = max(phone_pts, 2)
    return min(15, email_pts + phone_pts)


def _supplier(c, contacts) -> int:
    if c["supplier_registration_url"] or c["procurement_portal_url"]:
        return 15
    notes = (c["research_notes"] or "").lower()
    if "supplier page:" in notes or any(k["email_role"] == "procurement" for k in contacts):
        return 8
    return 0


def score_company(company, contacts) -> tuple[int, dict, str]:
    parts = {
        "retail_relevance": _relevance(company),
        "product_categories": _products(company),
        "procurement_contact": _contact_availability(contacts),
        "verified_email_phone": _verified(contacts),
        "supplier_registration": _supplier(company, contacts),
    }
    total = max(0, min(100, sum(parts.values())))
    return total, parts, priority_label(total)


def company_confidence(company, contacts) -> int:
    c = 0
    if company["website_status"] == "verified":
        c += 30
    if company["contact_page_url"]:
        c += 20
    if any(k["email_status"] == "published_official" for k in contacts):
        c += 20
    if any(k["kind"] == "person" and k["designation_tier"] in (1, 2) for k in contacts):
        c += 20
    if any(k["phone_status"] == "published_official" for k in contacts):
        c += 10
    return c


def rescore(db, company_id: int):
    import json
    company = db.get_company(company_id)
    contacts = db.contacts_for(company_id)
    score, parts, label = score_company(company, contacts)
    db.update_company(company_id, lead_score=score, score_breakdown=json.dumps(parts),
                      outreach_priority=label, confidence=company_confidence(company, contacts))
    return score, label
