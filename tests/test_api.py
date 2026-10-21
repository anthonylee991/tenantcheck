"""The submission API: URL in, status/report out, ownership enforced."""

from __future__ import annotations

from fastapi.testclient import TestClient

from verifier.api import (
    STATUS_COMPLETE,
    STATUS_OWNERSHIP_REQUIRED,
    STATUS_UNSUPPORTED,
    SubmissionService,
    _browser_enabled,
    create_app,
)
from verifier.models import Finding, ScanResult
from verifier.ownership import OwnershipResult, site_token
from verifier.store import SubmissionStore
from verifier.supabase import SupabaseConfig
from verifier.url_scan import UrlScan

URL = "https://my-app.example"


def _scan() -> UrlScan:
    result = ScanResult(
        target=f"{URL} (Supabase)",
        resources_probed=2,
        findings=[Finding(title="items readable by anyone", detail="proof", evidence={})],
        baseline_status="baseline established (first run)",
    )
    return UrlScan(result=result, config=SupabaseConfig(url="https://x.supabase.co", anon_key="k"))


def _service(tmp_path, *, verified: bool = True, scan_found: bool = True) -> SubmissionService:
    async def ownership(url: str) -> OwnershipResult:
        if verified:
            return OwnershipResult(url, site_token(url), True, "meta_tag", {})
        return OwnershipResult(url, site_token(url), False, None, {"meta_tag": "missing"})

    async def scan(url: str) -> UrlScan | None:
        return _scan() if scan_found else None

    return SubmissionService(
        SubmissionStore(tmp_path / "submissions.db"), ownership=ownership, scan=scan
    )


def test_verified_submission_returns_report(tmp_path) -> None:
    app = create_app(_service(tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/submissions", json={"url": URL})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == STATUS_COMPLETE
    assert body["findings_count"] == 1
    assert "items readable by anyone" in body["report"]
    assert body["baseline_status"] == "baseline established (first run)"
    assert body["ownership_method"] == "meta_tag"


def test_unproven_owner_is_not_scanned(tmp_path) -> None:
    app = create_app(_service(tmp_path, verified=False))
    with TestClient(app) as client:
        body = client.post("/api/submissions", json={"url": URL}).json()
    assert body["status"] == STATUS_OWNERSHIP_REQUIRED
    assert body["report"] is None
    assert site_token(URL) in body["instructions"]


def test_unsupported_stack_is_reported(tmp_path) -> None:
    app = create_app(_service(tmp_path, scan_found=False))
    with TestClient(app) as client:
        body = client.post("/api/submissions", json={"url": URL}).json()
    assert body["status"] == STATUS_UNSUPPORTED


def test_unknown_submission_is_404(tmp_path) -> None:
    app = create_app(_service(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/submissions/nope").status_code == 404
        assert client.post("/api/submissions/nope/recheck").status_code == 404


def test_recheck_transitions_once_ownership_is_proven(tmp_path) -> None:
    state = {"verified": False}

    async def ownership(url: str) -> OwnershipResult:
        if state["verified"]:
            return OwnershipResult(url, site_token(url), True, "dns_txt", {})
        return OwnershipResult(url, site_token(url), False, None, {})

    async def scan(url: str) -> UrlScan | None:
        return _scan()

    service = SubmissionService(
        SubmissionStore(tmp_path / "submissions.db"), ownership=ownership, scan=scan
    )
    app = create_app(service)
    with TestClient(app) as client:
        created = client.post("/api/submissions", json={"url": URL}).json()
        assert created["status"] == STATUS_OWNERSHIP_REQUIRED
        state["verified"] = True
        after = client.post(f"/api/submissions/{created['id']}/recheck").json()
    assert after["status"] == STATUS_COMPLETE
    assert after["ownership_method"] == "dns_txt"


def test_store_persists_across_instances(tmp_path) -> None:
    path = tmp_path / "submissions.db"
    created = SubmissionStore(path).create(URL)
    reloaded = SubmissionStore(path).get(created.id)
    assert reloaded is not None
    assert reloaded.url == URL


def test_browser_scan_is_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("TENANTCHECK_BROWSER_SCAN", raising=False)
    assert _browser_enabled(None) is False
    monkeypatch.setenv("TENANTCHECK_BROWSER_SCAN", "1")
    assert _browser_enabled(None) is True
    assert _browser_enabled(False) is False
