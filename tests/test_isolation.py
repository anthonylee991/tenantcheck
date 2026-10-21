"""Isolation check against the control apps (in-process, no server)."""

from __future__ import annotations

import httpx

from controls.app import make_app
from verifier.http import Client
from verifier.report import render
from verifier.scan import scan
from verifier.target import control_target


def _client(mode: str) -> Client:
    return Client("http://control", transport=httpx.ASGITransport(app=make_app(mode)))


async def test_vuln_app_reports_cross_account_read() -> None:
    client = _client("vuln")
    try:
        result = await scan(client, control_target(name="control:vuln"))
    finally:
        await client.aclose()
    isolation = [f for f in result.findings if f.check == "isolation"]
    assert len(isolation) >= 1
    assert isolation[0].evidence["status"] == 200


async def test_vuln_app_does_not_flag_unauthenticated() -> None:
    # The control app requires a token on every route, so the anon probe must be clean.
    client = _client("vuln")
    try:
        result = await scan(client, control_target(name="control:vuln"))
    finally:
        await client.aclose()
    assert [f for f in result.findings if f.check == "unauthenticated"] == []


async def test_vuln_app_reports_cross_account_write() -> None:
    client = _client("vuln")
    try:
        result = await scan(client, control_target(name="control:vuln"))
    finally:
        await client.aclose()
    writes = [f for f in result.findings if f.check == "cross_account_write"]
    assert len(writes) >= 1
    assert writes[0].evidence["status"] == 200


async def test_secure_app_reports_nothing() -> None:
    client = _client("secure")
    try:
        result = await scan(client, control_target(name="control:secure"))
    finally:
        await client.aclose()
    assert result.findings == []


async def test_report_includes_a_fix_prompt() -> None:
    client = _client("vuln")
    try:
        result = await scan(client, control_target(name="control:vuln"))
    finally:
        await client.aclose()
    text = render(result)
    assert "Fix prompt" in text
    assert "another user's data" in text


async def test_secure_report_states_no_exposure() -> None:
    client = _client("secure")
    try:
        result = await scan(client, control_target(name="control:secure"))
    finally:
        await client.aclose()
        assert "No problems found" in render(result)


def test_report_says_nothing_to_test_when_no_records_were_reachable() -> None:
    from verifier.models import ScanResult

    text = render(ScanResult(target="t", resources_probed=0))
    assert "Nothing to test" in text
    assert "Fix prompt" not in text


def test_report_is_ascii_for_windows_consoles() -> None:
    # Default Windows consoles are cp1252; the report must not carry non-ASCII.
    from verifier.models import Finding, ScanResult

    result = ScanResult(
        target="t",
        resources_probed=1,
        findings=[
            Finding(
                check="isolation",
                title="x",
                detail="y",
                evidence={"method": "GET", "path": "/p", "status": 200, "as": "a", "body": "b"},
            )
        ],
    )
    assert render(result).isascii()
