"""Optional search-API integration (Brave Search API or SerpAPI). Used only when an API key is configured.

Only API-returned metadata (title / URL / snippet) is used. We never scrape search-engine result pages,
and we never fetch LinkedIn pages: a LinkedIn URL is stored only if it appears as a search-API result.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

import requests

from . import config
from .normalize import clean_name, classify_designation, domain_of, name_key

BLOCKED_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "youtube.com", "tiktok.com",
    "wikipedia.org", "yellowpages.ae", "yellowpages.com", "tripadvisor.com", "justdial.com", "indeed.com",
    "glassdoor.com", "amazon.ae", "amazon.com", "noon.com", "alibaba.com", "pinterest.com", "google.com",
    "zawya.com", "crunchbase.com", "bloomberg.com", "dnb.com", "zoominfo.com", "kompass.com", "waze.com",
}

QUERY_TEMPLATES = {
    "Supermarkets & Hypermarkets": ["supermarket {country}", "hypermarket {city_or_country}", "grocery chain {country}"],
    "Food FMCG Retail": ["packaged food retailer {country}", "frozen food store {country}", "grocery store {city_or_country}"],
    "Non-Food FMCG Retail": ["household products retailer {country}", "cleaning products store {country}"],
    "Drug Stores & Pharmacies": ["pharmacy chain {country}", "drugstore {country}", "health and beauty store {country}"],
    "FMCG Wholesalers": ["FMCG wholesaler {country}", "cash and carry {country}", "non food FMCG wholesaler {country}"],
    "Distributors & Importers": ["FMCG distributor {country}", "food importer {country}", "general trading company FMCG {country}"],
}


class SearchProvider:
    name = "none"

    def search(self, query: str, count: int = 10) -> list[dict]:  # -> [{title,url,snippet}]
        raise NotImplementedError


class BraveSearch(SearchProvider):
    name = "brave"

    def __init__(self, key: str, session=None):
        self.key, self.session = key, session or requests.Session()

    def search(self, query, count=10):
        r = self.session.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": count},
                             headers={"X-Subscription-Token": self.key, "Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("description", "")}
                for x in r.json().get("web", {}).get("results", [])]


class SerpApiSearch(SearchProvider):
    name = "serpapi"

    def __init__(self, key: str, session=None):
        self.key, self.session = key, session or requests.Session()

    def search(self, query, count=10):
        r = self.session.get("https://serpapi.com/search.json",
                             params={"q": query, "num": count, "api_key": self.key, "engine": "google"}, timeout=30)
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
                for x in r.json().get("organic_results", [])]


def get_provider() -> SearchProvider | None:
    if config.BRAVE_API_KEY:
        return BraveSearch(config.BRAVE_API_KEY)
    if config.SERPAPI_KEY:
        return SerpApiSearch(config.SERPAPI_KEY)
    return None


def _usable(url: str) -> bool:
    d = domain_of(url)
    return bool(d) and not any(d == b or d.endswith("." + b) for b in BLOCKED_DOMAINS)


def _brand_in(text: str, company: str) -> bool:
    toks = [t for t in name_key(company).split() if len(t) > 2]
    return bool(toks) and toks[0] in re.sub(r"[^a-z0-9 ]", " ", text.lower())


def find_website(provider: SearchProvider, name: str, country: str) -> tuple[str, str] | None:
    """Return (website_url, evidence_url) or None."""
    for r in provider.search(f"{name} {country} official website", 8):
        if _usable(r["url"]) and _brand_in(r["title"] + " " + r["url"], name):
            p = r["url"].split("/")
            return f"{p[0]}//{p[2]}", r["url"]
    return None


def discover_companies(provider: SearchProvider, category: str, country: str, city: str = "") -> list[dict]:
    out, seen = [], set()
    for tpl in QUERY_TEMPLATES.get(category, []):
        q = tpl.format(country=country, city_or_country=city or country)
        for r in provider.search(q, 10):
            if not _usable(r["url"]):
                continue
            d = domain_of(r["url"])
            if d in seen:
                continue
            seen.add(d)
            nm = clean_name(re.split(r"\s[|\-–—:]\s", r["title"])[0])
            if 2 < len(nm) < 60:
                p = r["url"].split("/")
                out.append({"name": nm, "website": f"{p[0]}//{p[2]}", "source_url": r["url"], "query": q})
    return out


_LI_TITLE = re.compile(r"^(?P<name>[^|\-–—]+?)\s[-–—]\s(?P<title>.+?)(?:\s(?:at|@|-|–|—)\s(?P<co>.+?))?(?:\s*[|·].*)?$")


def find_linkedin_people(provider: SearchProvider, company: str, country: str) -> list[dict]:
    """Public LinkedIn snippets returned by the search API. Unverified by definition."""
    out = []
    for role in ("procurement manager", "category buyer", "purchase manager"):
        q = f'site:linkedin.com/in "{company}" "{role}" {country}'
        for r in provider.search(q, 5):
            if "linkedin.com/in/" not in r["url"]:
                continue
            m = _LI_TITLE.match(clean_name(r["title"].replace("| LinkedIn", "")))
            if not m:
                continue
            title = clean_name(m.group("title"))
            if not classify_designation(title) or not _brand_in(r["title"] + " " + r["snippet"], company):
                continue
            out.append({"name": clean_name(m.group("name")), "title": title, "linkedin_url": r["url"].split("?")[0],
                        "source_url": r["url"], "query": q})
    return out


def linkedin_search_url(company: str, keywords: str = "procurement OR buyer OR purchase") -> str:
    """A link the human researcher can open while logged in. We never fetch it."""
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f'"{company}" {keywords}')


def google_search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query)
