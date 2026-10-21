"""B3: network-traffic resource discovery - pure analysis + real browser capture."""

from __future__ import annotations

import logging

from harness import serve

from controls.webapp import make_webapp
from verifier.network import (
    NetworkCall,
    TrafficRecorder,
    candidates,
    capture,
    has_identifier_segment,
)
from verifier.ui import Credentials, provision_account

log = logging.getLogger(__name__)


def test_identifier_segments_are_recognised() -> None:
    assert has_identifier_segment("/api/v1/personas/19644be4-b38d-4c20-bcce-9ef9f0574f73/stats")
    assert has_identifier_segment("/api/v1/invoices/42")
    assert has_identifier_segment("/api/items/inv-abc123")
    assert not has_identifier_segment("/api/v1/personas")
    assert not has_identifier_segment("/api/v1/boards")


class _Request:
    def __init__(self, method: str, resource_type: str) -> None:
        self.method = method
        self.resource_type = resource_type


class _Response:
    def __init__(
        self,
        method: str,
        url: str,
        resource_type: str,
        *,
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self.request = _Request(method, resource_type)
        self.url = url
        self.status = status
        self.headers = {"content-type": content_type}


class _FakePage:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler) -> None:
        self.handlers[event].remove(handler)

    def emit(self, event: str, payload: object) -> None:
        for handler in list(self.handlers.get(event, [])):
            handler(payload)


def test_candidates_keep_data_calls_and_drop_noise() -> None:
    calls = [
        NetworkCall("GET", "http://app.test/assets/app.js", "script"),
        NetworkCall("GET", "http://app.test/api/invoices", "fetch"),
        NetworkCall("GET", "http://app.test/api/invoices/inv-abc12345", "fetch"),
        NetworkCall("GET", "https://www.google-analytics.com/collect?x=1", "fetch"),
        NetworkCall("GET", "http://app.test/dashboard", "document"),
        NetworkCall("OPTIONS", "http://app.test/api/invoices", "fetch"),
        NetworkCall("GET", "http://app.test/logo.png", "image"),
    ]
    found = candidates(calls)
    assert [c.path for c in found] == ["/api/invoices", "/api/invoices/inv-abc12345"]
    assert [c.kind for c in found] == ["collection", "record"]


def test_numeric_and_uuid_records_are_classified() -> None:
    uuid = "123e4567-e89b-12d3-a456-426614174000"
    calls = [
        NetworkCall("GET", "http://app.test/api/orders/42", "xhr"),
        NetworkCall("GET", f"http://app.test/api/people/{uuid}", "xhr"),
        NetworkCall("GET", "http://app.test/api/orders/summary", "xhr"),
    ]
    found = candidates(calls)
    assert [c.kind for c in found] == ["record", "record", "collection"]


def test_json_api_is_kept_even_without_an_xhr_type() -> None:
    calls = [NetworkCall("GET", "http://app.test/rest/v1/items?select=*", "")]
    found = candidates(calls)
    assert found and found[0].path == "/rest/v1/items?select=*"


def test_supabase_id_filter_marks_a_record() -> None:
    uuid = "123e4567-e89b-12d3-a456-426614174000"
    calls = [NetworkCall("GET", f"http://app.test/rest/v1/items?id=eq.{uuid}", "fetch")]
    found = candidates(calls)
    assert found[0].kind == "record"
    assert found[0].identifier == uuid


def test_duplicate_calls_are_deduped() -> None:
    calls = [
        NetworkCall("GET", "http://app.test/api/invoices", "fetch"),
        NetworkCall("GET", "http://app.test/api/invoices", "fetch"),
    ]
    assert len(candidates(calls)) == 1


def test_recorder_attaches_records_and_detaches() -> None:
    page = _FakePage()
    recorder = TrafficRecorder(origin="http://app.test")
    recorder.attach(page)
    page.emit("response", _Response("GET", "http://app.test/api/invoices", "fetch"))
    assert len(recorder.calls) == 1
    assert recorder.calls[0].method == "GET"
    assert recorder.calls[0].origin == "http://app.test"
    recorder.detach(page)
    page.emit("response", _Response("GET", "http://app.test/api/other", "fetch"))
    assert len(recorder.calls) == 1


async def test_capture_records_while_driving() -> None:
    page = _FakePage()

    async def drive() -> str:
        page.emit("response", _Response("GET", "http://app.test/api/invoices", "fetch"))
        return "done"

    calls = await capture(page, drive, origin="http://app.test")
    assert [call.url for call in calls] == ["http://app.test/api/invoices"]


async def test_dashboard_traffic_yields_the_invoices_api(browser_page: object) -> None:
    with serve(make_webapp()) as base:
        result = await provision_account(
            browser_page, base, Credentials("erin@example.com", "hunter2pw")
        )
        assert result.logged_in is True

        async def drive() -> None:
            await browser_page.goto(f"{base}/dashboard", wait_until="load")
            try:
                await browser_page.wait_for_function(
                    "() => document.querySelectorAll('#invoices p').length > 0", timeout=8000
                )
            except Exception:  # noqa: BLE001 - the traffic is recorded regardless
                log.debug("dashboard data did not render in time")

        calls = await capture(browser_page, drive, origin=base)

    found = candidates(calls)
    paths = [candidate.path for candidate in found]
    assert "/api/invoices" in paths
    assert any(candidate.kind == "record" and candidate.path.startswith("/api/invoices/") for candidate in found)
