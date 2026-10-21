"""The browser door: scan any URL by driving it as a person.

`scan_url` handles Supabase-backed apps by reading the config their frontend ships.
For everything else — an app with no discoverable API — this is the fallback: sign two
accounts up through the UI, learn the data endpoints from the app's own traffic, and
run the same isolation prober. It is the entry point the product door calls when no
Supabase config is found.
"""

from __future__ import annotations

import logging
import os

from .browser import BrowserConfig, BrowserSession
from .browser_target import BrowserClient, browser_target
from .models import ScanResult
from .scan import scan
from .ui import Credentials
from .vision import Fallback

log = logging.getLogger(__name__)


def _write_enabled() -> bool:
    """Write probing is on unless explicitly disabled (TENANTCHECK_BROWSER_WRITE=0)."""
    value = os.environ.get("TENANTCHECK_BROWSER_WRITE", "1").strip().lower()
    return value not in ("0", "false", "no", "off")


async def scan_browser(
    url: str,
    *,
    config: BrowserConfig | None = None,
    fallback: Fallback | None = None,
    accounts: list[Credentials] | None = None,
    write: bool | None = None,
) -> ScanResult:
    """Scan `url` entirely through the browser. Raises if accounts cannot be created.

    Pass `accounts` (two) to scan a login-only app: the scanner signs in as each
    instead of creating accounts. Credentials are used in memory only. `write`
    (default: TENANTCHECK_BROWSER_WRITE, on) creates one record of the scanner's own to
    test cross-account writes; turn it off to keep the target strictly read-only.
    """
    config = config or BrowserConfig.from_env()
    writable = _write_enabled() if write is None else write
    async with BrowserSession(config) as session:
        browser = BrowserClient(session, url, fallback=fallback, accounts=accounts)
        try:
            return await scan(
                browser, browser_target(browser, name=f"{url} (browser)", write=writable)
            )
        finally:
            await browser.aclose()


__all__ = ["scan_browser"]
