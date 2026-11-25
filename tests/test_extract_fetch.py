import requests

from gcc_procure import extract
from gcc_procure.fetcher import PoliteFetcher
from tests.fakeweb import FakeSession

HOME = """<html><head><title>Testmart Hypermarket | Home</title>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","name":"Testmart","telephone":"+971 4 345 6789","sameAs":["https://www.linkedin.com/company/testmart"]},
 {"@type":"Person","name":"Rania Haddad","jobTitle":"Category Manager - Food","email":"rania.haddad@testmart.ae"}]}</script></head>
<body><nav><a href="/contact-us">Contact Us</a><a href="/suppliers/register">Become a Supplier</a>
<a href="/our-team">Our Team</a><a href="https://testmart.ariba.com/portal">Supplier Portal</a><a href="/cart">Cart</a>
<a href="https://evil.example.org/contact">Contact elsewhere</a></nav>
<p>Over 120 stores across the UAE. Fresh groceries, frozen food, household cleaning and laundry products.</p>
<a href="mailto:Procurement@testmart.ae?subject=hi">Write to us</a>
<span class="__cf_email__" data-cfemail="%s">[email protected]</span>
<p>Reach buying: buying [at] testmart [dot] ae  | Careers: careers@testmart.ae</p></body></html>"""


def cf(email, key=0x42):
    return (bytes([key]) + bytes(b ^ key for b in email.encode())).hex()


def test_parse_page_extracts_everything():
    pd = extract.parse_page(HOME % cf("info@testmart.ae"), "https://testmart.ae", "AE")
    emails = dict(pd.emails)
    assert emails["procurement@testmart.ae"] == "mailto"
    assert emails["info@testmart.ae"] == "cfemail"
    assert "buying@testmart.ae" in emails            # [at]/[dot] de-obfuscation of what the page itself shows
    assert "+97143456789" in pd.phones
    assert pd.linkedin_company.endswith("/company/testmart")
    assert pd.branches == 120
    assert pd.links["supplier_reg"] == ["https://testmart.ae/suppliers/register"]
    assert pd.links["contact"] == ["https://testmart.ae/contact-us"]
    assert pd.links["procurement"] == ["https://testmart.ariba.com/portal"]
    assert not any("evil.example.org" in u for v in pd.links.values() for u in v)
    assert not any("cart" in u for v in pd.links.values() for u in v)
    p = pd.people[0]
    assert (p["name"], p["method"], p["confidence"]) == ("Rania Haddad", "jsonld", 85)
    assert pd.category_hits["food"] > 0 and pd.category_hits["nonfood"] > 0


def test_email_roles():
    r = extract.classify_email_role
    assert r("procurement@x.ae") == "procurement" and r("suppliers@x.ae") == "procurement"
    assert r("info@x.ae") == "general" and r("careers@x.ae") == "excluded" and r("john.smith@x.ae") == "named"
    assert r("sales@x.ae") == "sales"


def test_people_from_text_patterns():
    html = """<html><body><h2>Our Leadership</h2>
    <div><p>Ahmed Khan</p><p>Purchase Manager</p></div>
    <div><p>Sara Ali - Head of Procurement</p></div>
    <div><p>Meet the Head of Procurement</p></div>
    <div><p>Joe Bloggs</p><p>Head Pharmacist</p></div></body></html>"""
    people = {p["name"]: p for p in extract.parse_page(html, "https://x.ae").people}
    assert people["Ahmed Khan"]["method"] == "adjacent_line" and people["Sara Ali"]["method"] == "same_line"
    assert set(people) == {"Ahmed Khan", "Sara Ali"}  # not "Meet the...", not pharmacist


def fetcher(site, **kw):
    sleeps = []
    f = PoliteFetcher(session=FakeSession(site), sleep=sleeps.append, min_delay=2.0, **kw)
    f.sleeps = sleeps
    return f


def test_fetcher_respects_robots():
    f = fetcher({"https://a.ae/robots.txt": "User-agent: *\nDisallow: /private", "https://a.ae/private/x": "secret",
                 "https://a.ae/ok": "fine"})
    assert f.fetch("https://a.ae/private/x").status == "robots_disallowed"
    assert f.fetch("https://a.ae/ok").ok
    assert "https://a.ae/private/x" not in f.session.calls


def test_fetcher_robots_5xx_means_disallow_and_404_means_allow():
    f = fetcher({"https://a.ae/robots.txt": (503, "", {}) and (503, "x"), "https://a.ae/": "hi"})
    assert f.fetch("https://a.ae/").status == "robots_disallowed"
    f2 = fetcher({"https://b.ae/": "hi"})  # robots 404
    assert f2.fetch("https://b.ae/").ok


def test_fetcher_never_bypasses_blocks():
    f = fetcher({"https://a.ae/forbidden": (403, "no"), "https://a.ae/cap": "<html><title>Just a moment...</title>captcha</html>",
                 "https://a.ae/rl": (429, "slow down", "text/html", {"Retry-After": "999"})})
    assert f.fetch("https://a.ae/forbidden").status == "blocked"
    assert f.fetch("https://a.ae/cap").status == "blocked"
    r = f.fetch("https://a.ae/rl")
    assert r.status == "blocked" and f.session.calls.count("https://a.ae/rl") == 1  # no hammering


def test_fetcher_retries_then_succeeds_and_rate_limits():
    f = fetcher({"https://a.ae/flaky": lambda n: (503, "x") if n < 3 else "ok now", "https://a.ae/b": "b"})
    assert f.fetch("https://a.ae/flaky").ok
    assert f.session.calls.count("https://a.ae/flaky") == 3
    assert any(s >= 2 for s in f.sleeps)  # backoff happened


def test_fetcher_handles_network_errors_and_nonhtml():
    f = fetcher({"https://a.ae/down": requests.ConnectionError("boom"), "https://a.ae/f.pdf": (200, "%PDF", "application/pdf")}, retries=2)
    r = f.fetch("https://a.ae/down")
    assert r.status == "error" and "ConnectionError" in r.detail
    assert f.fetch("https://a.ae/f.pdf").status == "non_html"
