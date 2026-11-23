"""Lightweight, non-intrusive email verification: syntax + DNS MX only.

We deliberately do NOT run SMTP RCPT probing or guess addresses (Scout's enrichment module can do both;
it is not used here because guessed addresses cannot be called 'publicly available' business contacts).
"""
from __future__ import annotations

import dns.exception
import dns.resolver

_cache: dict[str, bool] = {}


def has_mx(domain: str, resolver=None) -> bool:
    domain = domain.lower()
    if domain in _cache:
        return _cache[domain]
    r = resolver or dns.resolver
    try:
        ans = r.resolve(domain, "MX", lifetime=5)
        ok = len(list(ans)) > 0
    except (dns.exception.DNSException, OSError):
        ok = False
    _cache[domain] = ok
    return ok


def email_status_unpublished(email: str, resolver=None) -> str:
    """Status for an email that was NOT seen on an official page (e.g. manually imported)."""
    return "mx_valid" if has_mx(email.split("@", 1)[1], resolver) else "no_mx"
