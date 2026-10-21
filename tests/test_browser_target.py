"""B4: a browser-backed Target lets the unchanged prober find cross-account access."""

from __future__ import annotations

import json

from harness import serve

from controls.webapp import make_webapp
from verifier.browser_target import (
    BrowserClient,
    _id_values,
    _marker_from_body,
    _nonce_field,
    _record_with,
    browser_target,
)
from verifier.scan import scan


def test_the_body_field_holding_the_nonce_is_reused_for_writes() -> None:
    assert _nonce_field('{"note": "vrfr-abc123", "amount": 1}', "vrfr-abc123") == "note"
    assert _nonce_field("note=vrfr-abc123&other=x", "vrfr-abc123") == "note"
    assert _nonce_field("note=something-else", "vrfr-abc123") is None


def test_the_created_record_is_found_inside_a_listing() -> None:
    listing = json.dumps({"invoices": [{"id": "inv-1", "note": "a"}, {"id": "inv-2", "note": "vrfr-x"}]})
    assert _record_with(listing, "vrfr-x") == {"id": "inv-2", "note": "vrfr-x"}
    assert _record_with(listing, "vrfr-missing") is None


def test_marker_prefers_a_distinctive_value() -> None:
    body = json.dumps({"id": "inv-abc12345", "amount": 42.0, "note": "vrfr-9f3a2b7c1d"})
    assert _marker_from_body(body, "inv-abc12345") == "vrfr-9f3a2b7c1d"


def test_marker_falls_back_to_the_record_id() -> None:
    assert _marker_from_body(json.dumps({"id": "inv-abc12345"}), "inv-abc12345") == "inv-abc12345"
    assert _marker_from_body("not json", "inv-abc12345") == "inv-abc12345"


async def test_browser_target_recalls_a_cross_account_read(browser_session: object) -> None:
    with serve(make_webapp(vulnerable=True)) as base:
        browser = BrowserClient(browser_session, base)
        try:
            result = await scan(browser, browser_target(browser))
        finally:
            await browser.aclose()
    assert result.resources_probed >= 1
    checks = [finding.check for finding in result.findings]
    assert "isolation" in checks


def test_ids_are_found_in_collection_payloads() -> None:
    assert _id_values({"invoices": [{"id": "inv-abc123", "amount": 1}]}) == ["inv-abc123"]
    assert _id_values({"data": [{"persona_id": "abc", "user_id": "u"}]}) == ["abc", "u"]
    assert _id_values({"ok": True}) == []


async def test_records_are_derived_when_the_dashboard_lists_only(
    browser_session: object,
) -> None:
    with serve(make_webapp(vulnerable=True, list_only=True)) as base:
        browser = BrowserClient(browser_session, base)
        try:
            result = await scan(browser, browser_target(browser))
        finally:
            await browser.aclose()
    assert result.resources_probed >= 1
    assert any(finding.check == "isolation" for finding in result.findings)


async def test_browser_target_stays_silent_when_secure(browser_session: object) -> None:
    with serve(make_webapp()) as base:
        browser = BrowserClient(browser_session, base)
        try:
            result = await scan(browser, browser_target(browser))
        finally:
            await browser.aclose()
    assert result.resources_probed >= 1
    assert result.findings == []


async def test_cross_account_write_is_found_on_a_record_the_scan_created(
    browser_session: object,
) -> None:
    with serve(make_webapp(vulnerable=True)) as base:
        browser = BrowserClient(browser_session, base)
        try:
            result = await scan(browser, browser_target(browser))
        finally:
            await browser.aclose()
    assert any(finding.check == "cross_account_write" for finding in result.findings)
    assert any(
        "change another account's data" in finding.title for finding in result.findings
    )


async def test_no_create_affordance_means_a_read_only_scan(browser_session: object) -> None:
    with serve(make_webapp(vulnerable=True, create_ui=False)) as base:
        browser = BrowserClient(browser_session, base)
        try:
            result = await scan(browser, browser_target(browser))
        finally:
            await browser.aclose()
    checks = [finding.check for finding in result.findings]
    assert "isolation" in checks
    assert "cross_account_write" not in checks
    assert "unauthenticated_write" not in checks
