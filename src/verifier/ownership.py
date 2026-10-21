"""Ownership proof: only scan an app whose owner has asked us to.

Scanning someone else's app without consent is the line this tool must never cross,
so the product door refuses to scan until the submitter proves they control the
app's domain. Proof is possession of the *site*, not a shared secret: you either
publish a token on the domain (a meta tag, a well-known file, or a DNS TXT record)
or you are not the owner.

The token is derived from the host, so we can show the exact value to publish; being
able to publish it on the host *is* the proof.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .http import Client

PREFIX = "tenantcheck-site-verification"
WELL_KNOWN_PATH = "/.well-known/tenantcheck-verification.txt"
DNS_NAME_PREFIX = "_verifier"

TxtResolver = Callable[[str], "list[str]"]


def normalise_host(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"//{url}")
    return (parsed.hostname or parsed.netloc or url).lower()


def site_token(url: str) -> str:
    """A stable token for a host. Publishing it on the host is the ownership proof."""
    return hashlib.sha256(f"verifier:{normalise_host(url)}".encode()).hexdigest()[:32]


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def is_loopback(url: str) -> bool:
    """True when the target is this machine — the runner inherently owns it."""
    host = normalise_host(url).strip("[]")
    if host in ("localhost", "::1") or host.endswith(".localhost"):
        return True
    address = _ip(host)
    return bool(address) and address.is_loopback


def is_private_target(url: str) -> bool:
    """True for loopback/private/link-local hosts — never scanned by the hosted API.

    Hostname-level only; a public name that resolves to a private address is a
    resolver-level concern (DNS rebinding) handled separately when we host this.
    """
    host = normalise_host(url).strip("[]")
    if host in ("localhost", "0.0.0.0") or host.endswith((".localhost", ".local", ".internal")):
        return True
    address = _ip(host)
    if address is not None:
        return (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_reserved
        )
    return False


@dataclass(frozen=True)
class OwnershipResult:
    url: str
    token: str
    verified: bool
    method: str | None = None
    attempts: dict[str, str] = field(default_factory=dict)

    def instructions(self) -> str:
        host = normalise_host(self.url)
        return "\n".join(
            [
                f"To prove you own {host}, publish this token, then run the scan again:",
                f"  token: {self.token}",
                "",
                f"  Option A - DNS: add a TXT record on {DNS_NAME_PREFIX}.{host} whose value is",
                f"             {PREFIX}={self.token}",
                f"  Option B - file: serve {WELL_KNOWN_PATH} containing the token",
                "  Option C - page: add this to your page's <head>",
                f'             <meta name="{PREFIX}" content="{self.token}">',
                "",
                "Any one of these is enough. This keeps the scanner from ever touching",
                "an app whose owner has not asked for it.",
            ]
        )


class OwnershipError(Exception):
    """Raised when a scan is refused because ownership was not proven."""

    def __init__(self, result: OwnershipResult) -> None:
        super().__init__("ownership not proven")
        self.result = result


_META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r"""([a-zA-Z-]+)\s*=\s*["']([^"']*)["']""")


def _has_meta_token(html: str, token: str) -> bool:
    for tag in _META_TAG.findall(html):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag)}
        if attrs.get("name", "").strip().lower() == PREFIX and token in attrs.get("content", ""):
            return True
    return False


def _default_txt_resolver(name: str) -> list[str]:  # pragma: no cover - needs DNS
    import dns.resolver

    answers = dns.resolver.resolve(name, "TXT")
    records: list[str] = []
    for rdata in answers:
        strings = getattr(rdata, "strings", None)
        records.append(b"".join(strings).decode() if strings else str(rdata).strip('"'))
    return records


async def verify_ownership(
    url: str,
    *,
    transport=None,
    timeout: float = 15.0,
    txt_resolver: TxtResolver | None = None,
) -> OwnershipResult:
    """Try each proof method; return on the first that verifies."""
    token = site_token(url)
    attempts: dict[str, str] = {}

    if is_loopback(url):
        attempts["loopback"] = "local machine is inherently owned"
        return OwnershipResult(url, token, True, "loopback", attempts)

    # Option C: meta tag on the served page.
    # Option B: well-known file.
    client = Client(url, transport=transport, timeout=timeout)
    try:
        try:
            page = await client.request("GET", "/")
            if _has_meta_token(page.text, token):
                return OwnershipResult(url, token, True, "meta_tag", attempts)
            attempts["meta_tag"] = "no matching <meta> tag on the page"
        except Exception as exc:  # noqa: BLE001 - any failure just rules the method out
            attempts["meta_tag"] = f"could not read page ({exc})"

        try:
            well_known = await client.request("GET", WELL_KNOWN_PATH)
            if well_known.status_code == 200 and token in well_known.text:
                return OwnershipResult(url, token, True, "well_known", attempts)
            attempts["well_known"] = "file missing or token not present"
        except Exception as exc:  # noqa: BLE001
            attempts["well_known"] = f"could not read file ({exc})"
    finally:
        await client.aclose()

    # Option A: DNS TXT record. Optional; skipped cleanly if DNS access is unavailable.
    resolver = txt_resolver or _default_txt_resolver
    name = f"{DNS_NAME_PREFIX}.{normalise_host(url)}"
    try:
        records = resolver(name)
        if any(token in record for record in records):
            return OwnershipResult(url, token, True, "dns_txt", attempts)
        attempts["dns_txt"] = "record present but token not found"
    except Exception as exc:  # noqa: BLE001
        attempts["dns_txt"] = f"no usable TXT record ({exc})"

    return OwnershipResult(url, token, False, None, attempts)
