"""Page parsing: emails, phones, people, useful links, branch counts, product categories."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .normalize import (DESIGNATION_FINDER, EMAIL_RE, classify_designation, clean_name, domain_of,
                        find_phones, normalize_email, normalize_url, same_site)

SUPPLIER_REG_RE = re.compile(
    r"(become\s+a\s+)?(supplier|vendor)s?[\s\-_/]*(regist|onboard|application|enrol)|new[\s\-_]?(supplier|vendor)|"
    r"become[\s\-_]+(a[\s\-_]+)?(supplier|vendor)|sell[\s\-_]+with[\s\-_]+us|list[\s\-_]+your[\s\-_]+products|"
    r"trade[\s\-_]+with[\s\-_]+us|supplier[\s\-_]+form|vendor[\s\-_]+form", re.I)
SUPPLIER_INFO_RE = re.compile(r"supplier|vendor|partner[\s\-_]+with[\s\-_]+us|doing[\s\-_]+business[\s\-_]+with", re.I)
PROCUREMENT_RE = re.compile(r"procurement|e-?tender|tenders?\b|sourcing|supplier[\s\-_]+portal|vendor[\s\-_]+portal|"
                            r"ariba|coupa|jaggaer|purchasing|b2b[\s\-_]+portal", re.I)
CONTACT_RE = re.compile(r"contact|reach[\s\-_]+us|get[\s\-_]+in[\s\-_]+touch|enquir", re.I)
ABOUT_RE = re.compile(r"about|team|leadership|management|board|our[\s\-_]+people|who[\s\-_]+we[\s\-_]+are", re.I)
LINKEDIN_CO_RE = re.compile(r"https?://([a-z]{2,3}\.)?linkedin\.com/company/[A-Za-z0-9_\-%.]+", re.I)
SKIP_PATH_RE = re.compile(r"\.(pdf|jpe?g|png|gif|svg|zip|docx?|xlsx?|mp4)(\?|$)|/(cart|checkout|login|account|wishlist)\b", re.I)

BRANCH_RE = re.compile(
    r"(?:over|more than|\+)?\s*(\d[\d,]{0,4})\s*\+?\s*(?:retail\s+)?(stores|branches|outlets|supermarkets|hypermarkets|"
    r"pharmacies|locations|shops)\b", re.I)

CATEGORY_KEYWORDS = {
    "food": ["grocery", "groceries", "fresh produce", "frozen", "bakery", "snacks", "beverages", "canned",
             "rice", "cooking oil", "pulses", "dairy", "supermarket", "hypermarket", "food"],
    "nonfood": ["household", "cleaning", "detergent", "laundry", "tissue", "home care", "kitchenware",
                "disposable", "homeware", "paper products", "non-food", "non food", "housewares"],
    "drugstore": ["pharmacy", "pharmacies", "otc", "health & beauty", "health and beauty", "wellness", "vitamins",
                  "supplements", "baby care", "hygiene", "personal care", "drugstore", "skincare"],
}

NOT_NAMES = {"our", "team", "management", "leadership", "contact", "about", "board", "the", "head", "office",
             "meet", "read", "more", "view", "all", "us", "directors", "managers", "message", "from", "welcome",
             "careers", "latest", "news", "department"}
_NAME_RE = re.compile(r"^(?:(?:Mr|Ms|Mrs|Dr|Eng|Sheikh|Haji)\.?\s+)?[A-Z][A-Za-z'’\-\.]{1,20}(?:\s+[A-Z][A-Za-z'’\-\.]{1,20}){1,3}$")


@dataclass
class PageData:
    url: str
    title: str = ""
    emails: list = field(default_factory=list)   # (email, how)
    phones: list = field(default_factory=list)   # e164
    people: list = field(default_factory=list)   # dict(name,title,method,confidence)
    links: dict = field(default_factory=dict)    # kind -> [urls]
    linkedin_company: str = ""
    branches: int | None = None
    branch_snippet: str = ""
    category_hits: dict = field(default_factory=dict)
    text: str = ""


def decode_cfemail(hexstr: str) -> str:
    """Decode Cloudflare's on-page email obfuscation (the page's own published address)."""
    try:
        data = bytes.fromhex(hexstr)
        key = data[0]
        return bytes(b ^ key for b in data[1:]).decode()
    except Exception:
        return ""


def _deobfuscate(text: str) -> str:
    t = re.sub(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", "@", text, flags=re.I)
    t = re.sub(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", ".", t, flags=re.I)
    return t


def classify_email_role(email: str) -> str:
    local = email.split("@")[0].lower()
    if re.search(r"procure|purchas|buying|buyer|sourcing|supplier|vendor|trade|tender|contract", local):
        return "procurement"
    if re.search(r"^(sales|commercial|business|bd|marketing)\b|sales", local):
        return "sales"
    if re.search(r"^(hr|career|careers|jobs|recruit|talent|hiring|noreply|no-reply|donotreply|webmaster|privacy|abuse|press|media)", local):
        return "excluded"
    if re.search(r"^(info|contact|hello|enquir|inquir|support|customer|care|admin|office|mail|feedback|service)", local):
        return "general"
    return "named"


def _jsonld_blocks(soup: BeautifulSoup):
    for s in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except Exception:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                yield d
                for v in d.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)


def _looks_like_name(s: str) -> bool:
    s = clean_name(s)
    if not s or len(s) > 40 or re.search(r"\d|@", s):
        return False
    if not _NAME_RE.match(s) or classify_designation(s):
        return False
    return not any(w.lower().strip(".") in NOT_NAMES for w in s.split())


def _people_from_text(lines: list[str]) -> list[dict]:
    people = []
    for i, line in enumerate(lines):
        if len(line) > 90 or not DESIGNATION_FINDER.search(line):
            continue
        # same-line patterns: "Name - Title", "Name, Title", "Title: Name"
        parts = [p.strip() for p in re.split(r"\s[-–—|]\s|,\s|:\s", line) if p.strip()]
        found = None
        if len(parts) >= 2:
            for a, b in ((parts[0], parts[1]), (parts[1], parts[0])):
                if _looks_like_name(a) and classify_designation(b):
                    found = (a, b, "same_line", 75)
                    break
        # card layout "Name / Title". Only the preceding line is trusted (accuracy over recall).
        if not found and classify_designation(line) and i > 0 and _looks_like_name(lines[i - 1]):
            found = (lines[i - 1], line, "adjacent_line", 55)
        if found:
            name, title, method, conf = found
            people.append({"name": clean_name(name), "title": clean_name(title), "method": method, "confidence": conf})
    return people


def parse_page(html: str, url: str, default_iso: str = "AE") -> PageData:
    soup = BeautifulSoup(html, "lxml")
    pd = PageData(url=url, title=clean_name(soup.title.get_text()) if soup.title else "")

    # ---- structured data -------------------------------------------------------
    for d in _jsonld_blocks(soup):
        t = d.get("@type")
        types = t if isinstance(t, list) else [t]
        if "Person" in types and d.get("name") and d.get("jobTitle") and classify_designation(d["jobTitle"]):
            pd.people.append({"name": clean_name(d["name"]), "title": clean_name(d["jobTitle"]),
                              "method": "jsonld", "confidence": 85,
                              "email": normalize_email(d.get("email")), "phone": d.get("telephone", "")})
        if any(x in types for x in ("Organization", "LocalBusiness", "Store", "GroceryStore", "Pharmacy", "Corporation")):
            if d.get("email"):
                pd.emails.append((normalize_email(d["email"]), "jsonld"))
            if d.get("telephone"):
                pd.phones.extend(find_phones(str(d["telephone"]), default_iso))
            for s in ([d.get("sameAs")] if isinstance(d.get("sameAs"), str) else d.get("sameAs") or []):
                if isinstance(s, str) and LINKEDIN_CO_RE.match(s):
                    pd.linkedin_company = s

    # ---- links ----------------------------------------------------------------
    links: dict[str, list[str]] = {"contact": [], "supplier_reg": [], "supplier_info": [], "procurement": [], "about": []}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            e = normalize_email(href)
            if e:
                pd.emails.append((e, "mailto"))
            continue
        if low.startswith("tel:"):
            pd.phones.extend(find_phones(href[4:], default_iso))
            continue
        if low.startswith(("javascript:", "#")):
            continue
        absu = normalize_url(urljoin(url, href))
        if not absu:
            continue
        if LINKEDIN_CO_RE.match(absu) and not pd.linkedin_company:
            pd.linkedin_company = LINKEDIN_CO_RE.match(absu).group(0)
            continue
        text = a.get_text(" ", strip=True)
        blob = f"{urlparse(absu).path} {text}"
        if SKIP_PATH_RE.search(absu):
            continue
        external_portal = not same_site(absu, url) and re.search(r"ariba|coupa|jaggaer|supplier|vendor|procure", urlparse(absu).netloc, re.I)
        if not same_site(absu, url) and not external_portal:
            continue
        if SUPPLIER_REG_RE.search(blob):
            links["supplier_reg"].append(absu)
        elif PROCUREMENT_RE.search(blob) or external_portal:
            links["procurement"].append(absu)
        elif SUPPLIER_INFO_RE.search(blob):
            links["supplier_info"].append(absu)
        elif CONTACT_RE.search(blob):
            links["contact"].append(absu)
        elif ABOUT_RE.search(blob):
            links["about"].append(absu)
    pd.links = {k: list(dict.fromkeys(v)) for k, v in links.items()}

    # ---- cloudflare-protected emails -----------------------------------------------
    for tag in soup.select("[data-cfemail]"):
        e = normalize_email(decode_cfemail(tag.get("data-cfemail", "")))
        if e:
            pd.emails.append((e, "cfemail"))
    for tag in soup.select("a[href*='/cdn-cgi/l/email-protection#']"):
        e = normalize_email(decode_cfemail(tag["href"].split("#", 1)[1]))
        if e:
            pd.emails.append((e, "cfemail"))

    # ---- visible text ---------------------------------------------------------------
    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    pd.text = "\n".join(lines)
    flat = _deobfuscate(" ".join(lines))
    for m in EMAIL_RE.findall(flat):
        e = normalize_email(m)
        if e:
            pd.emails.append((e, "text"))
    pd.phones.extend(find_phones(flat, default_iso))

    seen = set()
    pd.emails = [(e, h) for e, h in pd.emails if e and not (e in seen or seen.add(e))]
    pd.phones = list(dict.fromkeys(pd.phones))

    # ---- people -----------------------------------------------------------------------
    pd.people.extend(_people_from_text(lines))
    uniq = {}
    for p in pd.people:
        key = (p["name"].lower(), p["title"].lower())
        if key not in uniq or p["confidence"] > uniq[key]["confidence"]:
            uniq[key] = p
    pd.people = list(uniq.values())

    # ---- branches + categories --------------------------------------------------------
    for m in BRANCH_RE.finditer(flat):
        n = int(m.group(1).replace(",", ""))
        if 2 <= n <= 5000:
            pd.branches, pd.branch_snippet = n, flat[max(0, m.start() - 30): m.end() + 40].strip()
            break
    low = flat.lower()
    pd.category_hits = {k: sum(low.count(w) for w in ws) for k, ws in CATEGORY_KEYWORDS.items()}
    return pd


def rank_candidate_pages(pages: list[PageData], base_url: str, limit: int) -> list[str]:
    """Pick the most valuable same-site pages to crawl next (contact > supplier > procurement > about)."""
    order = ["supplier_reg", "contact", "procurement", "supplier_info", "about"]
    picked: list[str] = []
    for kind in order:
        for pg in pages:
            for u in pg.links.get(kind, []):
                if u not in picked and same_site(u, base_url) and u.rstrip("/") != base_url.rstrip("/"):
                    picked.append(u)
    return picked[:limit]
