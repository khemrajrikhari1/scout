"""Store discovery through the official Google Places API (Text Search, New).

This is how small/independent stores are found: city x category queries return business name, address,
public listing phone and website. Requires GOOGLE_PLACES_API_KEY (billing-enabled Google Cloud project).
Compliance note: Google's terms limit how long Places content may be cached/stored; keep place_id as the durable key
and refresh details periodically.
"""
from __future__ import annotations

import requests

from . import config

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ("places.id,places.displayName,places.formattedAddress,places.websiteUri,places.internationalPhoneNumber,"
          "places.googleMapsUri,places.businessStatus,places.userRatingCount,nextPageToken")

CITIES = {
    "UAE": ["Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah", "Fujairah", "Umm Al Quwain", "Al Ain"],
    "Saudi Arabia": ["Riyadh", "Jeddah", "Dammam", "Al Khobar", "Makkah", "Madinah", "Taif", "Tabuk", "Abha", "Buraidah"],
    "Oman": ["Muscat", "Salalah", "Sohar", "Nizwa", "Sur"],
    "Qatar": ["Doha", "Al Wakrah", "Al Khor", "Al Rayyan"],
    "Kuwait": ["Kuwait City", "Hawalli", "Salmiya", "Farwaniya", "Ahmadi"],
    "Bahrain": ["Manama", "Riffa", "Muharraq", "Isa Town"],
}
QUERIES = {
    config.CAT_SUPER: ["supermarket", "hypermarket"],
    config.CAT_FOOD: ["grocery store", "foodstuff trading"],
    config.CAT_NONFOOD: ["household goods store", "cleaning products store"],
    config.CAT_PHARMA: ["pharmacy", "drugstore"],
    config.CAT_WHOLESALE: ["FMCG wholesaler", "cash and carry", "foodstuff wholesale"],
    config.CAT_DIST: ["FMCG distributor", "food importer", "general trading company"],
}


def search_places(query: str, key: str, max_pages: int = 3, session=None) -> list[dict]:
    s = session or requests.Session()
    out, token = [], None
    for _ in range(max_pages):
        body = {"textQuery": query, "pageSize": 20}
        if token:
            body["pageToken"] = token
        r = s.post(ENDPOINT, json=body, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS}, timeout=20)
        r.raise_for_status()
        data = r.json()
        for p in data.get("places", []):
            if p.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
                continue
            out.append({"place_id": p.get("id", ""), "name": (p.get("displayName") or {}).get("text", ""),
                        "address": p.get("formattedAddress", ""), "website": p.get("websiteUri", ""),
                        "phone": p.get("internationalPhoneNumber", ""), "maps_url": p.get("googleMapsUri", ""),
                        "reviews": p.get("userRatingCount", 0)})
        token = data.get("nextPageToken")
        if not token:
            break
    return out


def size_from_reviews(n: int) -> str:
    return "Large" if n >= 2000 else "Medium" if n >= 300 else "Small"
