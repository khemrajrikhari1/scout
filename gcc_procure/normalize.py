"""Normalisation + validation helpers (emails, phones, names, URLs, designations)."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse, urlunparse

try:  # phonenumbers gives real validity checks; fall back to a light parser if absent
    import phonenumbers
except ImportError:  # pragma: no cover
    phonenumbers = None

# Reuse Scout's existing noise filters when the repo is importable.
try:
    from app.scrapers.enrichment import EMAIL_BLACKLIST, FILE_EXT_BLACKLIST
except Exception:  # pragma: no cover
    EMAIL_BLACKLIST = ["example.com", "test.com", "email.com", "sentry.io", "wixpress.com",
                       "googleapis.com", "w3.org", "schema.org", "gravatar.com", "wordpress.com"]
    FILE_EXT_BLACKLIST = [".png", ".jpg", ".gif", ".css", ".js", ".svg", ".webp", ".ico"]

EMAIL_BLACKLIST = list(EMAIL_BLACKLIST) + ["domain.com", "yourdomain.com", "yoursite.com", "sentry-next.wixpress.com"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_STRICT_EMAIL = re.compile(r"^[a-z0-9._%+\-]{1,64}@(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


def normalize_email(raw: str | None) -> str:
    """Lower-case, strip and validate an email. Returns '' when invalid."""
    if not raw:
        return ""
    e = raw.strip().strip(".,;:<>()[]\"'").lower()
    if e.startswith("mailto:"):
        e = e[7:].split("?")[0]
    if not _STRICT_EMAIL.match(e) or ".." in e:
        return ""
    if any(e.endswith(x) for x in FILE_EXT_BLACKLIST):
        return ""
    domain = e.split("@", 1)[1]
    if any(domain == b or domain.endswith("." + b) for b in EMAIL_BLACKLIST):
        return ""
    return e


def is_valid_email_format(raw: str | None) -> bool:
    return bool(normalize_email(raw))


def normalize_phone(raw: str | None, default_iso: str = "AE") -> tuple[str, bool]:
    """Return (E.164 string, is_valid). Empty string if unparseable."""
    if not raw:
        return "", False
    raw = raw.strip()
    if phonenumbers is not None:
        try:
            num = phonenumbers.parse(raw, default_iso)
        except phonenumbers.NumberParseException:
            return "", False
        valid = phonenumbers.is_valid_number(num)
        if not valid:
            return "", False
        return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164), True
    digits = re.sub(r"[^\d+]", "", raw)  # pragma: no cover
    return (digits, 9 <= len(digits) <= 15) if digits else ("", False)  # pragma: no cover


def find_phones(text: str, default_iso: str = "AE") -> list[str]:
    """Find valid phone numbers in free text. Returns unique E.164 values."""
    out: list[str] = []
    if not text:
        return out
    if phonenumbers is None:  # pragma: no cover
        return out
    for m in phonenumbers.PhoneNumberMatcher(text, default_iso, leniency=phonenumbers.Leniency.VALID):
        e164 = phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.E164)
        if e164 not in out:
            out.append(e164)
    return out


_CORP_SUFFIX = re.compile(
    r"\b(l\.?l\.?c\.?|llc|fze|fzco|fz-llc|co\.?|company|ltd\.?|limited|inc\.?|wll|w\.l\.l\.?|"
    r"s\.?a\.?o\.?c\.?|saoc|k\.?s\.?c\.?|ksc|bsc|est\.?|establishment|group|holding|holdings|"
    r"international|trading|the)\b",
    re.I,
)


def name_key(name: str) -> str:
    """Normalised key used for duplicate detection of company names."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = _CORP_SUFFIX.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_url(url: str | None) -> str:
    if not url:
        return ""
    url = url.strip()
    if re.match(r"^(javascript|mailto|tel|data|sms|whatsapp):", url, re.I):
        return ""
    if not re.match(r"^[a-z][a-z0-9+.\-]*://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "", "", p.query, ""))


def domain_of(url: str | None) -> str:
    u = normalize_url(url)
    if not u:
        return ""
    host = urlparse(u).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def same_site(a: str, b: str) -> bool:
    da, db = domain_of(a), domain_of(b)
    return bool(da and db and (da == db or da.endswith("." + db) or db.endswith("." + da)))


def clean_name(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"\s+", " ", name).strip(" ,.-–—")


# --- Designations -----------------------------------------------------------------

# (regex, tier) ordered most specific first. Tier 1 = procurement core, 2 = adjacent, 3 = leadership.
_DESIGNATIONS: list[tuple[re.Pattern, int]] = [
    (re.compile(p, re.I), t) for p, t in [
        (r"head of (procurement|purchas\w*|buying|sourcing)", 1),
        (r"(procurement|purchas\w*|buying)\s+(manager|head|director|lead|officer|executive)", 1),
        (r"(director|vp|vice president)\s+(of\s+)?(procurement|purchasing|buying)", 1),
        (r"(senior|sr\.?|chief|lead|assistant|asst\.?)?\s*(category\s+)?(buyer)\b", 1),
        (r"category\s+(manager|head|director|lead)", 1),
        (r"(sourcing|supply chain|merchandis\w*|commercial|trading)\s+(manager|head|director|lead)", 2),
        (r"head of (supply chain|merchandis\w*|commercial|trading|category)", 2),
        (r"(fmcg|food|non[\s\-]?food|grocery|pharmacy|health\s*&?\s*beauty|general merchandise)\s+(category\s+)?(buyer|manager|head)", 2),
        (r"(business development)\s+(manager|head|director|executive)", 3),
        (r"(managing director|general manager|\bmd\b|\bceo\b|chief executive|chairman|owner|co-?owner|founder|co-?founder|partner|proprietor|\bcoo\b)", 3),
    ]
]
_EXCLUDED_ROLES = re.compile(r"pharmacist|prescription|\bhr\b|human resources|recruit|marketing|\bit\b|accountant|finance", re.I)


def classify_designation(title: str | None) -> int:
    """0 = not a target designation, 1/2/3 = outreach priority tier."""
    if not title:
        return 0
    t = re.sub(r"\s+", " ", title).strip()
    if _EXCLUDED_ROLES.search(t) and not re.search(r"procure|purchas|buyer|sourcing", t, re.I):
        return 0
    for rx, tier in _DESIGNATIONS:
        if rx.search(t):
            return tier
    return 0


DESIGNATION_FINDER = re.compile("|".join(f"(?:{rx.pattern})" for rx, _ in _DESIGNATIONS), re.I)


def department_for(title: str | None) -> str:
    t = (title or "").lower()
    if re.search(r"procure|purchas|buyer|buying", t):
        return "Procurement"
    if re.search(r"category|merchandis", t):
        return "Category Management"
    if re.search(r"sourcing|supply chain", t):
        return "Supply Chain"
    if re.search(r"commercial|trading|business development", t):
        return "Commercial"
    if re.search(r"director|manager|owner|founder|ceo|chairman|partner", t):
        return "Management"
    return ""
