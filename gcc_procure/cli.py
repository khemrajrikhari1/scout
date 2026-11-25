"""Command line interface:  python procure.py <command>"""
from __future__ import annotations

import argparse
import logging
import sys

from . import config, importer, pipeline
from .db import DB
from .exporter import export_all
from .fetcher import PoliteFetcher
from .search import get_provider


def _setup_logging(verbose: bool):
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(config.LOG_DIR / "procure.log"), logging.StreamHandler(sys.stderr)])
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="procure", description="GCC Retail Procurement Intelligence (built on Scout)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--db", help="SQLite path (default data/procurement.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create database and load seed company lists")
    r = sub.add_parser("run", help="research one phase (resumable)")
    r.add_argument("--phase", type=int, required=True, choices=sorted(config.PHASES))
    r.add_argument("--limit", type=int, help="process at most N companies this run")
    r.add_argument("--force", action="store_true", help="re-crawl even if recently crawled")
    r.add_argument("--max-age-days", type=int)
    r.add_argument("--discover", action="store_true", help="also discover new companies via search API (needs key)")
    r.add_argument("--no-robots", action="store_true", help=argparse.SUPPRESS)
    d = sub.add_parser("discover-places", help="find stores of ALL sizes via Google Places API (needs GOOGLE_PLACES_API_KEY)")
    d.add_argument("--phase", type=int, required=True, choices=sorted(config.PHASES))
    d.add_argument("--cities", help="comma-separated cities (default: all major cities of the phase countries)")
    d.add_argument("--max-pages", type=int, default=3)
    sub.add_parser("status", help="progress per phase")
    i = sub.add_parser("import-contacts", help="import a CSV of manually researched contacts")
    i.add_argument("csv")
    i.add_argument("--source", default="", help="source label applied to rows lacking one")
    e = sub.add_parser("export", help="write xlsx + csv + google-sheets files")
    e.add_argument("--out")
    sub.add_parser("manual-queue", help="print companies that need manual research")
    f = sub.add_parser("forget", help="delete + suppress a contact on request")
    f.add_argument("email")
    sub.add_parser("dashboard", help="launch the Streamlit dashboard")
    a = ap.parse_args(argv)
    _setup_logging(a.verbose)
    db = DB(a.db)

    if a.cmd == "init":
        print(pipeline.load_seeds(db))
    elif a.cmd == "run":
        provider = get_provider()
        if a.discover:
            if not provider:
                print("--discover needs BRAVE_API_KEY or SERPAPI_KEY", file=sys.stderr)
                return 2
            print("discovery:", pipeline.discover_phase(db, provider, a.phase))
        fetcher = PoliteFetcher(db, respect_robots=not a.no_robots)
        st = pipeline.run_phase(db, fetcher, a.phase, provider, force=a.force, max_age_days=a.max_age_days, limit=a.limit,
                                on_progress=lambda i, n, res: print(f"[{i}/{n}] {res['company']}: {res['status']}"
                                                                    + (f" score={res.get('score')}" if 'score' in res else "")))
        print(st)
        if st.get("interrupted"):
            print("Interrupted - re-run the same command to resume.")
    elif a.cmd == "discover-places":
        if not config.GOOGLE_PLACES_API_KEY:
            print("Set GOOGLE_PLACES_API_KEY first", file=sys.stderr)
            return 2
        cities = [c.strip() for c in a.cities.split(",")] if a.cities else None
        print(pipeline.discover_places(db, a.phase, config.GOOGLE_PLACES_API_KEY, cities, max_pages=a.max_pages))
    elif a.cmd == "status":
        for s in pipeline.phase_status(db):
            print(f"Phase {s['phase']}: {s['name']:<38} {s['researched']}/{s['companies']} researched, {s['failed']} failed  [{s['job']}]")
    elif a.cmd == "import-contacts":
        res = importer.import_contacts(db, a.csv, a.source)
        for m in res.pop("errors"):
            print("  !", m)
        print(res)
    elif a.cmd == "export":
        print(export_all(db, a.out))
    elif a.cmd == "manual-queue":
        for r_ in importer.manual_research_queue(db):
            print(f"{r_['Company Name']} ({r_['Country']}): {r_['Reason(s)']}")
    elif a.cmd == "forget":
        print("deleted", importer.forget(db, a.email), "contact(s); email suppressed")
    elif a.cmd == "dashboard":
        import subprocess
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(config.ROOT / "gcc_procure" / "dashboard.py")])
    return 0
