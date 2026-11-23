"""Polite, compliant HTTP fetcher.

* Honest User-Agent (no browser spoofing, no proxy rotation).
* Honours robots.txt (4xx => allowed, 5xx/unreachable => treated as disallowed, per RFC 9309).
* Per-host rate limiting, bounded retries with exponential backoff, Retry-After support.
* Never tries to defeat CAPTCHAs, logins or bot-challenges: such pages are logged as 'blocked' and skipped.
"""
from __future__ import annotations

import logging
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from . import config

log = logging.getLogger("gcc_procure.fetch")

BLOCK_MARKERS = ("captcha", "cf-challenge", "just a moment...", "attention required", "access denied",
                 "verify you are human", "are you a robot", "unusual traffic")
RETRY_STATUS = {500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    status: str            # ok | blocked | robots_disallowed | not_found | error | non_html
    http_status: int | None = None
    text: str = ""
    final_url: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class PoliteFetcher:
    def __init__(self, db=None, session: requests.Session | None = None, user_agent: str | None = None,
                 min_delay: float | None = None, timeout: float | None = None, retries: int | None = None,
                 respect_robots: bool = True, sleep=time.sleep, clock=time.monotonic):
        self.db = db
        self.session = session or requests.Session()
        self.ua = user_agent or config.USER_AGENT
        self.min_delay = config.MIN_DELAY_PER_HOST if min_delay is None else min_delay
        self.timeout = timeout or config.REQUEST_TIMEOUT
        self.retries = retries or config.MAX_RETRIES
        self.respect_robots = respect_robots
        self._sleep, self._clock = sleep, clock
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None | bool] = {}

    # -- robots ---------------------------------------------------------------
    def _robots_allows(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        rp = self._robots[origin]
        if rp is True:
            return True
        if rp is False:
            return False
        if rp == "unreachable":
            return False
        return rp.can_fetch(self.ua, url)

    def _load_robots(self, origin: str):
        self._throttle(origin)
        try:
            r = self.session.get(origin + "/robots.txt", headers={"User-Agent": self.ua}, timeout=self.timeout)
        except requests.RequestException as e:
            log.warning("robots.txt unreachable for %s (%s) - not crawling", origin, e)
            return "unreachable"
        if 400 <= r.status_code < 500:
            return True
        if r.status_code >= 500:
            return False
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp

    def _throttle(self, origin_or_url: str):
        host = urlparse(origin_or_url).netloc
        wait = self._last_hit.get(host, -1e9) + self.min_delay - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_hit[host] = self._clock()

    # -- fetch ----------------------------------------------------------------
    def fetch(self, url: str) -> FetchResult:
        res = self._fetch(url)
        if self.db is not None:
            self.db.log_fetch(url, res.status, res.http_status, res.detail)
        return res

    def _fetch(self, url: str) -> FetchResult:
        if not self._robots_allows(url):
            p = urlparse(url)
            if self._robots.get(f"{p.scheme}://{p.netloc}") == "unreachable":
                return FetchResult(url, "error", None, detail="robots.txt unreachable (network/DNS) - will retry later")
            return FetchResult(url, "robots_disallowed", detail="disallowed by robots.txt")
        last_err = ""
        for attempt in range(1, self.retries + 1):
            self._throttle(url)
            try:
                r = self.session.get(url, headers={"User-Agent": self.ua, "Accept": "text/html,application/xhtml+xml"},
                                     timeout=self.timeout, allow_redirects=True, stream=True)
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
                log.warning("fetch %s failed (attempt %d/%d): %s", url, attempt, self.retries, last_err)
                self._sleep(min(2 ** attempt, 30))
                continue
            code = r.status_code
            if code in (401, 403):
                return FetchResult(url, "blocked", code, detail="access refused - not bypassed")
            if code == 429:
                ra = r.headers.get("Retry-After", "")
                if attempt < self.retries and ra.isdigit() and int(ra) <= 60:
                    self._sleep(int(ra))
                    continue
                return FetchResult(url, "blocked", code, detail="rate limited - backing off")
            if code in (404, 410):
                return FetchResult(url, "not_found", code)
            if code in RETRY_STATUS:
                last_err = f"HTTP {code}"
                self._sleep(min(2 ** attempt, 30))
                continue
            if code >= 400:
                return FetchResult(url, "error", code, detail=f"HTTP {code}")
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                return FetchResult(url, "non_html", code, detail=ctype)
            raw = b""
            for chunk in r.iter_content(65536):
                raw += chunk
                if len(raw) > config.MAX_BYTES:
                    break
            text = raw.decode(r.encoding or "utf-8", errors="replace")
            low = text[:6000].lower()
            if any(m in low for m in BLOCK_MARKERS) and len(text) < 20000:
                return FetchResult(url, "blocked", code, detail="bot-challenge/captcha page - not bypassed")
            return FetchResult(url, "ok", code, text, final_url=str(r.url))
        return FetchResult(url, "error", None, detail=last_err or "retries exhausted")
