"""Find a supported backend config from a deployed frontend.

The product door is "paste a URL". For Supabase-backed apps the project URL and
public (anon) key are necessarily shipped to the browser, so they can be read as a
*client* — no source access. This walks the page and its same-origin scripts,
concatenates them, and looks for the pair.

Same-origin only: we never fetch third-party bundles, so a submission cannot be
turned into a crawler for arbitrary hosts.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from .http import Client
from .supabase import SupabaseConfig, extract_config

_SCRIPT_SRC = re.compile(r"""<script[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_PRELOAD = re.compile(r"""<link[^>]*\bhref\s*=\s*["']([^"']+\.m?js[^"']*)["']""", re.IGNORECASE)


def _asset_refs(html: str) -> list[str]:
    refs = _SCRIPT_SRC.findall(html) + _PRELOAD.findall(html)
    seen: set[str] = set()
    ordered: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ordered


def _same_origin(base: str, candidate: str) -> bool:
    a, b = urlparse(base), urlparse(candidate)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


async def find_supabase(
    client: Client,
    path: str = "/",
    *,
    max_assets: int = 25,
    max_bytes: int = 4_000_000,
) -> SupabaseConfig | None:
    page = await client.request("GET", path)
    if page.status_code >= 400:
        return None

    base = str(page.url)
    chunks = [page.text]
    total = len(page.text)

    for ref in _asset_refs(page.text)[:max_assets]:
        target = urljoin(base, ref)
        if not _same_origin(base, target):
            continue
        asset = await client.request("GET", target)
        if asset.status_code >= 400:
            continue
        chunks.append(asset.text)
        total += len(asset.text)
        if total > max_bytes:
            break

    return extract_config("\n".join(chunks))
