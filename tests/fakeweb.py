"""In-memory stand-in for the web so tests never touch the network."""
import requests


class FakeResp:
    def __init__(self, url, status=200, body="", ctype="text/html; charset=utf-8", headers=None):
        self.url, self.status_code, self.encoding = url, status, "utf-8"
        self._b = body.encode() if isinstance(body, str) else body
        self.headers = {"Content-Type": ctype, **(headers or {})}

    @property
    def text(self):
        return self._b.decode("utf-8", "replace")

    def iter_content(self, n):
        yield self._b


class FakeSession:
    def __init__(self, site: dict):
        self.site, self.calls = site, []

    def get(self, url, headers=None, timeout=None, **kw):
        self.calls.append(url)
        v = self.site.get(url)
        if v is None:
            return FakeResp(url, 404, "nope")
        if isinstance(v, Exception):
            raise v
        if callable(v):
            v = v(len([c for c in self.calls if c == url]))
        if isinstance(v, str):
            return FakeResp(url, 200, v)
        return FakeResp(url, *v)
