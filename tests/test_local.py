"""Local mode: this machine is inherently owned; the hosted API refuses private targets."""

from __future__ import annotations

from fastapi.testclient import TestClient
from harness import serve

from controls.webapp import make_webapp
from verifier.api import STATUS_BLOCKED, SubmissionService, create_app
from verifier.models import ScanResult
from verifier.ownership import (
    OwnershipResult,
    is_loopback,
    is_private_target,
    site_token,
    verify_ownership,
)
from verifier.store import SubmissionStore
from verifier.url_scan import scan_url


def test_loopback_and_private_hosts_are_recognised() -> None:
    assert is_loopback("http://127.0.0.1:3000")
    assert is_loopback("http://localhost:8080")
    assert not is_loopback("https://my-app.example")
    assert is_private_target("http://192.168.1.20:3000")
    assert is_private_target("http://10.0.0.5")
    assert not is_private_target("https://my-app.example")


async def test_loopback_needs_no_published_token() -> None:
    result = await verify_ownership("http://127.0.0.1:9/", txt_resolver=lambda _name: [])
    assert result.verified is True
    assert result.method == "loopback"


async def test_local_app_scans_through_the_normal_door() -> None:
    seen: list[str] = []

    async def browser_scan(url: str) -> ScanResult:
        seen.append(url)
        return ScanResult(target="browser", resources_probed=1)

    with serve(make_webapp()) as base:
        found = await scan_url(base, require_ownership=True, browser_scan=browser_scan)
    assert found is not None
    assert found.stack == "browser"
    assert found.ownership is not None and found.ownership.method == "loopback"
    assert seen == [base]


def test_hosted_api_refuses_local_targets(tmp_path) -> None:
    scanned: list[str] = []

    async def ownership(url: str) -> OwnershipResult:
        return OwnershipResult(url, site_token(url), True, "loopback", {})

    async def scan(_url: str) -> None:
        scanned.append(_url)

    service = SubmissionService(
        SubmissionStore(tmp_path / "submissions.db"), ownership=ownership, scan=scan
    )
    with TestClient(create_app(service)) as client:
        body = client.post("/api/submissions", json={"url": "http://127.0.0.1:3000"}).json()
    assert body["status"] == STATUS_BLOCKED
    assert scanned == []
