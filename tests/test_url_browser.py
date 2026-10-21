"""The product door's browser fallback: any URL, not just Supabase-backed apps."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from harness import serve

from controls.webapp import make_webapp
from verifier.browser_door import scan_browser
from verifier.models import ScanResult
from verifier.ownership import OwnershipError
from verifier.url_scan import scan_url

URL = "http://app.test"


def _transport() -> httpx.ASGITransport:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return "<html><body><h1>Just an app</h1></body></html>"

    return httpx.ASGITransport(app=app)


def _no_dns(_name: str) -> list[str]:
    return []


async def test_browser_fallback_runs_when_no_supabase_config() -> None:
    seen: dict[str, str] = {}

    async def browser_scan(url: str) -> ScanResult:
        seen["url"] = url
        return ScanResult(target="browser", resources_probed=1)

    result = await scan_url(URL, frontend_transport=_transport(), browser_scan=browser_scan)
    assert result is not None
    assert result.stack == "browser"
    assert result.config is None
    assert seen["url"] == URL


async def test_without_a_fallback_an_unsupported_app_is_none() -> None:
    assert await scan_url(URL, frontend_transport=_transport()) is None


async def test_a_fallback_that_finds_nothing_is_unsupported() -> None:
    async def browser_scan(_url: str) -> ScanResult | None:
        return None

    assert await scan_url(URL, frontend_transport=_transport(), browser_scan=browser_scan) is None


async def test_ownership_is_required_before_the_browser_fallback() -> None:
    seen: dict[str, str] = {}

    async def browser_scan(url: str) -> ScanResult:
        seen["url"] = url
        return ScanResult(target="browser", resources_probed=1)

    with pytest.raises(OwnershipError):
        await scan_url(
            URL,
            frontend_transport=_transport(),
            require_ownership=True,
            txt_resolver=_no_dns,
            browser_scan=browser_scan,
        )
    assert seen == {}


async def test_browser_door_scans_a_real_url(browser_session: object) -> None:
    with serve(make_webapp(vulnerable=True)) as base:
        result = await scan_url(base, browser_scan=scan_browser)
    assert result is not None
    assert result.stack == "browser"
    assert any(finding.check == "isolation" for finding in result.result.findings)


async def test_browser_door_is_silent_on_the_secure_app(browser_session: object) -> None:
    with serve(make_webapp()) as base:
        result = await scan_url(base, browser_scan=scan_browser)
    assert result is not None
    assert result.result.findings == []
