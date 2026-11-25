"""SQLite storage layer (single file, zero setup). Schema mirrors the requested field list."""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config
from .normalize import domain_of, name_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    parent_company TEXT DEFAULT '',
    country TEXT NOT NULL,
    city TEXT DEFAULT '',
    business_category TEXT DEFAULT '',
    business_subcategory TEXT DEFAULT '',
    website TEXT DEFAULT '',
    website_domain TEXT DEFAULT '',
    website_status TEXT DEFAULT 'none',      -- none | seed_unverified | verified | unreachable | mismatch | blocked
    branches INTEGER,
    business_size TEXT DEFAULT '',           -- Large | Medium | Small | Unknown
    trade_type TEXT DEFAULT '',              -- Retailer | Wholesaler | Distributor
    product_categories TEXT DEFAULT '',      -- comma list of: food, nonfood, drugstore
    placement_opportunity TEXT DEFAULT '',
    supplier_registration_url TEXT DEFAULT '',
    procurement_portal_url TEXT DEFAULT '',
    contact_page_url TEXT DEFAULT '',
    linkedin_company_url TEXT DEFAULT '',
    outreach_priority TEXT DEFAULT '',
    research_notes TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    date_collected TEXT,
    last_verified TEXT,
    last_crawled TEXT,
    crawl_status TEXT DEFAULT 'pending',     -- pending | done | failed | skipped
    duplicate_status TEXT DEFAULT 'unique',  -- unique | possible_duplicate_of:<id>
    confidence INTEGER DEFAULT 0,
    lead_score INTEGER DEFAULT 0,
    score_breakdown TEXT DEFAULT '',
    phase INTEGER,
    needs_manual INTEGER DEFAULT 0,
    UNIQUE(name_key, country)
);
CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(website_domain);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'person',     -- person | department | general
    contact_name TEXT DEFAULT '',
    designation TEXT DEFAULT '',
    designation_tier INTEGER DEFAULT 0,      -- 1,2,3 or 0
    department TEXT DEFAULT '',
    linkedin_url TEXT DEFAULT '',
    email TEXT DEFAULT '',
    email_role TEXT DEFAULT '',              -- procurement | sales | general | named | other
    email_status TEXT DEFAULT 'none',        -- none | published_official | published_third_party | mx_valid | no_mx | invalid
    phone TEXT DEFAULT '',
    phone_status TEXT DEFAULT 'none',        -- none | published_official | published_third_party | format_valid | invalid
    contact_source TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    verification_status TEXT DEFAULT 'Unverified',
    date_collected TEXT,
    last_verified TEXT,
    duplicate_status TEXT DEFAULT 'unique',
    confidence INTEGER DEFAULT 0,
    dedupe_key TEXT NOT NULL,
    UNIQUE(company_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phase INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',  -- running | interrupted | done
    started TEXT, finished TEXT
);
CREATE TABLE IF NOT EXISTS job_items (
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed | skipped
    error TEXT DEFAULT '',
    updated TEXT,
    PRIMARY KEY (job_id, company_id)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL, status TEXT, http_status INTEGER, fetched_at TEXT, detail TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fetch_url ON fetch_log(url);
CREATE TABLE IF NOT EXISTS suppression (
    email TEXT PRIMARY KEY, reason TEXT DEFAULT '', added TEXT
);
"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return dt.date.today().isoformat()


class DB:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or config.DB_PATH)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()

    # ---- companies ------------------------------------------------------------
    def find_company(self, name: str, country: str, website: str = ""):
        key = name_key(name)
        row = self.conn.execute(
            "SELECT * FROM companies WHERE name_key=? AND country=?", (key, country)).fetchone()
        if row:
            return row
        dom = domain_of(website)
        if dom:
            row = self.conn.execute(
                "SELECT * FROM companies WHERE website_domain=? AND country=?", (dom, country)).fetchone()
        return row

    def upsert_company(self, name: str, country: str, **fields) -> tuple[int, bool]:
        """Insert or merge. Existing non-empty values are never overwritten by empty ones.
        Returns (company_id, created)."""
        fields = {k: v for k, v in fields.items() if v is not None}
        website = fields.get("website", "")
        existing = self.find_company(name, country, website)
        if website:
            fields["website_domain"] = domain_of(website)
        if existing:
            updates = {}
            for k, v in fields.items():
                if v == "" or v is None:
                    continue
                cur = existing[k] if k in existing.keys() else None
                if k in ("research_notes",) and cur and v not in cur:
                    updates[k] = f"{cur} | {v}"
                elif cur in (None, "", 0, "none", "unique") or k in (
                        "website_status", "crawl_status", "lead_score", "score_breakdown", "outreach_priority",
                        "confidence", "last_crawled", "last_verified", "needs_manual", "duplicate_status"):
                    updates[k] = v
            if updates:
                cols = ", ".join(f"{k}=?" for k in updates)
                self.conn.execute(f"UPDATE companies SET {cols} WHERE id=?", (*updates.values(), existing["id"]))
                self.conn.commit()
            return existing["id"], False
        data = {"company_name": name, "name_key": name_key(name), "country": country,
                "date_collected": today(), **fields}
        cols = ", ".join(data)
        qs = ", ".join("?" for _ in data)
        cur = self.conn.execute(f"INSERT INTO companies ({cols}) VALUES ({qs})", tuple(data.values()))
        self.conn.commit()
        return cur.lastrowid, True

    def update_company(self, company_id: int, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE companies SET {cols} WHERE id=?", (*fields.values(), company_id))
        self.conn.commit()

    def get_company(self, company_id: int):
        return self.conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()

    def companies(self, where: str = "1=1", params: tuple = ()):
        return self.conn.execute(f"SELECT * FROM companies WHERE {where} ORDER BY id", params).fetchall()

    def contacts_for(self, company_id: int):
        return self.conn.execute("SELECT * FROM contacts WHERE company_id=? ORDER BY id", (company_id,)).fetchall()

    # ---- contacts -------------------------------------------------------------
    def is_suppressed(self, email: str) -> bool:
        return bool(email) and self.conn.execute(
            "SELECT 1 FROM suppression WHERE email=?", (email.lower(),)).fetchone() is not None

    def add_contact(self, company_id: int, dedupe_key: str, **fields) -> tuple[int | None, bool]:
        """Insert contact unless a duplicate exists (then merge missing/better fields)."""
        email = fields.get("email", "")
        if email and self.is_suppressed(email):
            return None, False
        row = self.conn.execute(
            "SELECT * FROM contacts WHERE company_id=? AND dedupe_key=?", (company_id, dedupe_key)).fetchone()
        if row:
            updates = {}
            for k, v in fields.items():
                if v in ("", None):
                    continue
                cur = row[k]
                if cur in ("", None, 0, "none", "Unverified"):
                    updates[k] = v
                elif k == "confidence" and v > (cur or 0):
                    updates[k] = v
                elif k in ("email_status", "phone_status", "verification_status") and _rank(k, v) > _rank(k, cur):
                    updates[k] = v
            updates["last_verified"] = today()
            cols = ", ".join(f"{k}=?" for k in updates)
            self.conn.execute(f"UPDATE contacts SET {cols} WHERE id=?", (*updates.values(), row["id"]))
            self.conn.commit()
            return row["id"], False
        data = {"company_id": company_id, "dedupe_key": dedupe_key, "date_collected": today(),
                "last_verified": today(), **fields}
        cols = ", ".join(data)
        qs = ", ".join("?" for _ in data)
        cur = self.conn.execute(f"INSERT INTO contacts ({cols}) VALUES ({qs})", tuple(data.values()))
        self.conn.commit()
        return cur.lastrowid, True

    # ---- fetch log / incremental ------------------------------------------------
    def log_fetch(self, url: str, status: str, http_status: int | None, detail: str = ""):
        self.conn.execute(
            "INSERT INTO fetch_log(url,status,http_status,fetched_at,detail) VALUES (?,?,?,?,?)",
            (url, status, http_status, now(), detail[:500]))
        self.conn.commit()

    # ---- jobs -----------------------------------------------------------------
    def open_job(self, phase: int):
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE phase=? AND status IN ('running','interrupted') ORDER BY id DESC LIMIT 1",
            (phase,)).fetchone()
        if row:
            self.conn.execute("UPDATE jobs SET status='running' WHERE id=?", (row["id"],))
            self.conn.commit()
            return row["id"], True
        cur = self.conn.execute("INSERT INTO jobs(phase,status,started) VALUES (?, 'running', ?)", (phase, now()))
        self.conn.commit()
        return cur.lastrowid, False

    def job_progress(self, job_id: int) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) n FROM job_items WHERE job_id=? GROUP BY status", (job_id,)).fetchall()
        return {r["status"]: r["n"] for r in rows}


_RANKS = {
    "email_status": ["none", "invalid", "no_mx", "mx_valid", "published_third_party", "published_official"],
    "phone_status": ["none", "invalid", "format_valid", "published_third_party", "published_official"],
    "verification_status": ["Unverified", "Imported - Unverified", "Discovered - Needs Review",
                            "Verified - Published by company"],
}


def _rank(field: str, value) -> int:
    try:
        return _RANKS[field].index(value)
    except (KeyError, ValueError):
        return -1
