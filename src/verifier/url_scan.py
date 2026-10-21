"""The product door: URL in, report out.

Stage 1 supports one stack — Supabase, because that is where the audience's
dominant bug lives (missing Row Level Security). The flow is: read the deployed
frontend as a client, find the Supabase config it necessarily ships, then run the
generic isolation scan against that project with two fresh accounts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from .behavior import BaselineStore
from .behavior import app_key as make_app_key
from .frontend import find_supabase
from .http import Client
from .models import ScanResult
from .ownership import OwnershipError, OwnershipResult, TxtResolver, verify_ownership
from .scan import scan
from .supabase import SupabaseConfig, supabase_target

BrowserScanFn = Callable[[str], Awaitable[ScanResult | None]]


@dataclass(frozen=True)
class UrlScan:
    result: ScanResult
    config: SupabaseConfig | None
    stack: str = "supabase"
    ownership: OwnershipResult | None = None


async def scan_url(
    url: str,
    *,
    frontend_transport: httpx.AsyncBaseTransport | None = None,
    supabase_transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 15.0,
    store: BaselineStore | None = None,
    require_ownership: bool = False,
    txt_resolver: TxtResolver | None = None,
    browser_scan: BrowserScanFn | None = None,
) -> UrlScan | None:
    """Return a scan of the app at `url`, or None if no supported stack is found.

    When `require_ownership` is set, the scan is refused (raises `OwnershipError`)
    unless the submitter has proven control of the domain. When the app is not
    Supabase-backed, `browser_scan` (if supplied) scans it through the browser.
    """
    ownership: OwnershipResult | None = None
    if require_ownership:
        ownership = await verify_ownership(
            url, transport=frontend_transport, timeout=timeout, txt_resolver=txt_resolver
        )
        if not ownership.verified:
            raise OwnershipError(ownership)

    frontend = Client(url, transport=frontend_transport, timeout=timeout)
    try:
        config = await find_supabase(frontend)
    finally:
        await frontend.aclose()

    if config is None:
        if browser_scan is None:
            return None
        result = await browser_scan(url)
        if result is None:
            return None
        return UrlScan(result=result, config=None, stack="browser", ownership=ownership)

    api = Client(
        config.url,
        transport=supabase_transport,
        timeout=timeout,
        default_headers={"apikey": config.anon_key},
    )
    try:
        result = await scan(
            api,
            supabase_target(config.anon_key, name=f"{url} (Supabase)"),
            store=store if store is not None else BaselineStore(),
            app_key=make_app_key(url),
        )
    finally:
        await api.aclose()

    return UrlScan(result=result, config=config, ownership=ownership)
