import io

import pandas as pd
import pytest

from gcc_procure import config, exporter, importer, pipeline, queries, scoring
from gcc_procure.db import DB
from gcc_procure.fetcher import PoliteFetcher
from tests.fakeweb import FakeSession
from tests.test_extract_fetch import HOME, cf

SITE = {
    "https://testmart.ae": HOME % cf("info@testmart.ae"),
    "https://testmart.ae/contact-us": "<html><title>Contact Testmart</title><body>Testmart HQ Dubai. Call +971 4 345 6789. Email: sales@testmart.ae</body></html>",
    "https://testmart.ae/suppliers/register": "<html><title>Supplier registration - Testmart</title><body>Register your company.</body></html>",
    "https://testmart.ae/our-team": "<html><body><h1>Team</h1><div><p>Omar Saleh</p><p>Head of Procurement</p></div></body></html>",
    "https://othermart.ae": "<html><title>Totally Different Shop</title><body>hello zzz@othermart.ae</body></html>",
    "https://deadmart.ae": (403, "no"),
}


@pytest.fixture
def env(tmp_path):
    db = DB(tmp_path / "t.db")
    f = PoliteFetcher(db, session=FakeSession(SITE), sleep=lambda s: None, min_delay=0)
    yield db, f
    db.close()


def add(db, name, site="", cat=config.CAT_SUPER, country="UAE"):
    return db.upsert_company(name, country, business_category=cat, website=site, product_categories="food,nonfood,drugstore",
                             trade_type="Retailer", website_status="seed_unverified" if site else "none",
                             phase=pipeline.phase_for(country, cat))[0]


def test_seed_loading_is_idempotent_and_dedupes(tmp_path):
    db = DB(tmp_path / "s.db")
    a = pipeline.load_seeds(db)
    b = pipeline.load_seeds(db)
    assert a["added"] > 20 and b["added"] == 0 and b["merged_duplicates"] == a["added"] + a["merged_duplicates"]
    names = [r["company_name"] for r in db.companies("phase=1")]
    assert "Lulu Hypermarket" in names and "Nesto Hypermarket" in names
    ph = [r["company_name"] for r in db.companies("phase=4")]
    assert "Life Pharmacy" in ph
    assert db.companies("phase=6") and db.companies("phase=7")
    assert all(r["research_notes"].startswith("Target OTC") for r in db.companies("business_category=?", (config.CAT_PHARMA,)))


def test_full_company_research(env):
    db, f = env
    cid = add(db, "Testmart Hypermarket", "https://testmart.ae")
    res = pipeline.research_company(db, f, cid)
    assert res["status"] == "ok"
    co = db.get_company(cid)
    assert co["website_status"] == "verified"
    assert co["supplier_registration_url"] == "https://testmart.ae/suppliers/register"
    assert co["procurement_portal_url"] == "https://testmart.ariba.com/portal"
    assert co["contact_page_url"] == "https://testmart.ae/contact-us"
    assert co["branches"] == 120 and co["linkedin_company_url"].endswith("/company/testmart")
    assert co["lead_score"] >= 80 and co["outreach_priority"] == "High Priority"
    ks = db.contacts_for(cid)
    by = {k["email"] or k["contact_name"] or k["phone"]: k for k in ks}
    by.update({k["contact_name"]: k for k in ks if k["contact_name"]})
    # every contact has a source URL and is official-site verified
    assert all(k["source_url"].startswith("https://testmart.ae") for k in ks)
    assert by["Rania Haddad"]["designation_tier"] == 1 and by["Rania Haddad"]["verification_status"].startswith("Verified")
    assert by["Omar Saleh"]["verification_status"].startswith("Discovered")  # layout heuristic => needs review
    assert by["procurement@testmart.ae"]["email_role"] == "procurement"
    assert "careers@testmart.ae" not in by                                   # HR addresses excluded
    assert by["+97143456789"]["phone_status"] == "published_official"
    assert co["needs_manual"] == 0
    # nothing was invented: every stored email literally appears in the fixture pages
    corpus = " ".join(str(v) for v in SITE.values()) + cf("info@testmart.ae")
    for k in ks:
        if k["email"] and k["email"] not in ("info@testmart.ae", "buying@testmart.ae"):
            assert k["email"].split("@")[0] in corpus.lower()


def test_unreachable_mismatch_and_nowebsite_go_to_manual_queue(env):
    db, f = env
    ids = [add(db, "Deadmart", "https://deadmart.ae"), add(db, "Wrong Site Stores", "https://othermart.ae"),
           add(db, "No Site Market")]
    for i in ids:
        pipeline.research_company(db, f, i)
    st = {db.get_company(i)["company_name"]: db.get_company(i) for i in ids}
    assert st["Deadmart"]["website_status"] == "blocked"
    assert st["Wrong Site Stores"]["website_status"] == "mismatch"
    assert db.contacts_for(ids[1]) == []        # mismatched site is NOT harvested
    assert st["No Site Market"]["needs_manual"] == 1
    q = {r["Company Name"]: r for r in importer.manual_research_queue(db)}
    assert set(q) == {"Deadmart", "Wrong Site Stores", "No Site Market"}
    assert "linkedin.com/search" in q["Deadmart"]["LinkedIn People Search (open manually)"]


def test_resume_and_incremental(env):
    db, f = env
    a = add(db, "Testmart Hypermarket", "https://testmart.ae")
    add(db, "Deadmart", "https://deadmart.ae")
    n = {"calls": 0}
    orig = pipeline.research_company

    def boom(db_, f_, cid, provider=None, **kw):
        n["calls"] += 1
        if n["calls"] == 2:
            raise KeyboardInterrupt
        return orig(db_, f_, cid, provider, **kw)

    pipeline.research_company = boom
    try:
        s1 = pipeline.run_phase(db, f, 1)
    finally:
        pipeline.research_company = orig
    assert s1.get("interrupted") and s1["processed"] == 1
    assert db.conn.execute("SELECT status FROM jobs").fetchone()[0] == "interrupted"
    s2 = pipeline.run_phase(db, f, 1)                 # resume: only the unfinished company
    assert s2["resumed"] and s2["processed"] == 1 and s2["job_id"] == s1["job_id"]
    assert db.conn.execute("SELECT status FROM jobs").fetchone()[0] == "done"
    calls_before = len(f.session.calls)
    s3 = pipeline.run_phase(db, f, 1)                 # new job; recently crawled => skipped, zero HTTP calls
    assert s3["skipped"] == 2 and s3["processed"] == 0 and len(f.session.calls) == calls_before
    s4 = pipeline.run_phase(db, f, 1, force=True)
    assert s4["processed"] == 2
    cnt = db.conn.execute("SELECT COUNT(*) FROM contacts WHERE company_id=?", (a,)).fetchone()[0]
    pipeline.run_phase(db, f, 1, force=True)
    assert db.conn.execute("SELECT COUNT(*) FROM contacts WHERE company_id=?", (a,)).fetchone()[0] == cnt  # no duplicates


def test_failures_are_isolated_and_logged(env):
    db, f = env
    add(db, "Testmart Hypermarket", "https://testmart.ae")
    add(db, "Crashmart", "https://crash.ae")
    orig = pipeline.research_company

    def sometimes(db_, f_, cid, provider=None, **kw):
        if db_.get_company(cid)["company_name"] == "Crashmart":
            raise RuntimeError("parser exploded")
        return orig(db_, f_, cid, provider, **kw)

    pipeline.research_company = sometimes
    try:
        s = pipeline.run_phase(db, f, 1)
    finally:
        pipeline.research_company = orig
    assert s["failed"] == 1 and s["processed"] == 1
    row = db.conn.execute("SELECT error FROM job_items WHERE status='failed'").fetchone()
    assert "parser exploded" in row[0]


def test_scoring_rubric_bands_and_caps(tmp_path):
    db = DB(tmp_path / "x.db")
    cid = add(db, "Bare Co", cat="")
    assert scoring.rescore(db, cid)[1] == "Research Required"
    assert scoring.priority_label(80) == "High Priority" and scoring.priority_label(79) == "Medium Priority"
    assert scoring.priority_label(60) == "Medium Priority" and scoring.priority_label(59) == "Low Priority"
    assert scoring.priority_label(40) == "Low Priority" and scoring.priority_label(39) == "Research Required"
    # perfect lead = 100
    db.update_company(cid, business_category=config.CAT_SUPER, supplier_registration_url="https://x/reg")
    db.add_contact(cid, "n:a", kind="person", contact_name="A B", designation="Head of Procurement", designation_tier=1,
                   email="a@x.ae", email_status="published_official", phone="+97143456789", phone_status="published_official")
    s, label = scoring.rescore(db, cid)
    assert s == 100 and label == "High Priority"
    parts = __import__("json").loads(db.get_company(cid)["score_breakdown"])
    assert parts == {"retail_relevance": 25, "product_categories": 25, "procurement_contact": 20,
                     "verified_email_phone": 15, "supplier_registration": 15}


def test_importer_validates_dedupes_scores_and_requires_source(env, tmp_path):
    db, _ = env
    import gcc_procure.verify as v
    v._cache.update({"testmart.ae": True, "nomx.ae": False})
    csvp = tmp_path / "c.csv"
    csvp.write_text(
        "Company,Country,Category,Name,Designation,Email,Phone,LinkedIn,Source\n"
        "Gulf Foods Trading,UAE,Distributors & Importers,Lina Nasser,Purchase Manager,Lina@Testmart.ae,04 345 6789,https://linkedin.com/in/lina,Gulfood 2026 exhibitor list https://gulfood.example/x\n"
        "Gulf Foods Trading,UAE,Distributors & Importers,Lina Nasser,Purchase Manager,lina@testmart.ae,,,Gulfood 2026\n"
        "Gulf Foods Trading LLC,UAE,Distributors & Importers,,Buyer,bad-email,,,Gulfood 2026\n"
        "No Source Ltd,UAE,,X Y,Buyer,x@nomx.ae,,,\n"
        "Qatar Co,Narnia,,X Y,Buyer,x@y.ae,,,src\n"
        "Ghost Co,UAE,,Zed Q,Senior Buyer,zed@nomx.ae,,,LinkedIn manual research\n")
    res = importer.import_contacts(db, str(csvp))
    assert res["contacts_added"] == 2 and res["merged"] == 1 and res["rejected"] == 3
    assert any("no source" in e for e in res["errors"]) and any("invalid email" in e for e in res["errors"])
    gulf = db.companies("name_key='gulf foods'")
    assert len(gulf) == 1                                  # "LLC" variant merged
    k = db.contacts_for(gulf[0]["id"])[0]
    assert (k["email"], k["email_status"], k["phone"], k["verification_status"]) == (
        "lina@testmart.ae", "mx_valid", "+97143456789", "Imported - Unverified")
    ghost = db.contacts_for(db.companies("name_key='ghost'")[0]["id"])[0]
    assert ghost["email_status"] == "no_mx"
    assert gulf[0]["lead_score"] > 0
    # removal request
    assert importer.forget(db, "lina@testmart.ae") == 1
    res2 = importer.import_contacts(db, str(csvp))
    assert db.conn.execute("SELECT COUNT(*) FROM contacts WHERE email='lina@testmart.ae'").fetchone()[0] == 0


def test_export_has_all_sheets_and_dashboard_queries(env, tmp_path):
    db, f = env
    cid = add(db, "Testmart Hypermarket", "https://testmart.ae")
    add(db, "No Site Market")
    pipeline.research_company(db, f, cid)
    pipeline.research_company(db, f, db.companies("company_name='No Site Market'")[0]["id"])
    out = exporter.export_all(db, tmp_path / "out")
    xl = pd.ExcelFile(out["dir"] + "/gcc_procurement_leads.xlsx")
    assert xl.sheet_names == ["Supermarkets & Hypermarkets", "Food FMCG Buyers", "Non-Food FMCG Buyers", "Drug Stores & Pharmacies",
                              "FMCG Wholesalers", "Distributors & Importers", "Procurement Decision Makers", "High Priority Leads",
                              "Manual Research Needed"]
    sup = xl.parse("Supermarkets & Hypermarkets")
    assert {"Company Name", "Contact Person Name", "Source URL", "Email Verification Status", "Confidence Score",
            "Supplier Registration URL", "Suggested Outreach Priority"} <= set(sup.columns)
    assert sup["Source URL"].dropna().str.startswith("https://testmart.ae").all()
    assert len(xl.parse("High Priority Leads")) > 0 and len(xl.parse("Procurement Decision Makers")) >= 3
    assert xl.parse("Manual Research Needed")["Company Name"].tolist() == ["No Site Market"]
    import os
    assert os.path.exists(tmp_path / "out/csv/supermarkets_and_hypermarkets.csv")
    assert open(tmp_path / "out/google_sheets/high_priority_leads.csv", "rb").read(3) == b"\xef\xbb\xbf"
    k = queries.kpis(queries.leads_df(db))
    assert k["Total Companies Discovered"] == 2 and k["Companies Without Contacts"] == 1
    assert k["Supplier Registration Opportunities"] == 1 and k["High-Priority Leads"] == 1
    assert k["Verified Business Emails"] >= 3 and k["Public Business Phone Numbers"] >= 1


def test_search_provider_paths(env):
    from gcc_procure import search

    class Fake(search.SearchProvider):
        name = "fake"

        def search(self, q, count=10):
            if "official website" in q:
                return [{"title": "Testmart on Facebook", "url": "https://facebook.com/testmart", "snippet": ""},
                        {"title": "Testmart Hypermarket - Home", "url": "https://testmart.ae/en", "snippet": ""}]
            if "linkedin.com/in" in q:
                return [{"title": "Maya Rao - Senior Buyer - Testmart Hypermarket | LinkedIn", "url": "https://ae.linkedin.com/in/mayarao?x=1", "snippet": "Testmart"},
                        {"title": "Someone - Chef - Elsewhere | LinkedIn", "url": "https://linkedin.com/in/chef", "snippet": ""}]
            return [{"title": "Al Noor Supermarket | Dubai", "url": "https://alnoor.ae/home", "snippet": ""},
                    {"title": "Best supermarkets", "url": "https://yellowpages.ae/x", "snippet": ""}]

    db, f = env
    cid = add(db, "Testmart Hypermarket")
    res = pipeline.research_company(db, f, cid, Fake())
    assert db.get_company(cid)["website"] == "https://testmart.ae"
    li = [k for k in db.contacts_for(cid) if k["linkedin_url"]]
    assert len(li) == 1 and li[0]["linkedin_url"] == "https://ae.linkedin.com/in/mayarao"
    assert li[0]["verification_status"] == "Discovered - Needs Review" and li[0]["confidence"] <= 40
    d = pipeline.discover_phase(db, Fake(), 1)
    assert d["added"] == 1 and db.companies("company_name='Al Noor Supermarket'")[0]["confidence"] == 10


def test_transient_network_failure_is_retried_next_run(env):
    import requests
    db, f = env
    f.session.site["https://flaky.ae/robots.txt"] = requests.ConnectionError("dns down")
    add(db, "Flakymart", "https://flaky.ae")
    s1 = pipeline.run_phase(db, f, 1)
    co = db.companies("company_name='Flakymart'")[0]
    assert co["website_status"] == "unreachable" and co["last_crawled"] in (None, "") and co["crawl_status"] == "failed"
    s2 = pipeline.run_phase(db, f, 1)       # not skipped as "recently crawled"
    assert s2["skipped"] == 0 and s2["resumed"]
