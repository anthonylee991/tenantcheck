"""The baseline store: remember how the app behaved, advance only when green."""

from __future__ import annotations

import uuid

import httpx

from controls.app import make_app
from verifier.behavior import BaselineStore, app_key, verify_flows
from verifier.http import Client


async def _observe(mode: str) -> tuple[Client, str, list]:
    client = Client("http://control", transport=httpx.ASGITransport(app=make_app(mode)))
    signup = await client.request(
        "POST", "/signup", json={"email": f"{uuid.uuid4().hex[:8]}@x.test", "password": "pw"}
    )
    token = signup.json()["token"]
    created = await client.request(
        "POST", "/api/invoices", token=token, json={"amount": 1.0, "note": "n"}
    )
    invoice_id = created.json()["id"]
    from verifier.behavior import Flow

    flows = [Flow("GET", "/api/invoices", "list"), Flow("GET", f"/api/invoices/{invoice_id}", "one")]
    return client, token, flows


def test_app_key_is_stable_and_filesystem_safe() -> None:
    assert app_key("https://my-app.lovable.app/") == app_key("https://my-app.lovable.app")
    assert "/" not in app_key("https://a.b/c/d")


def test_store_round_trips(tmp_path) -> None:
    store = BaselineStore(tmp_path)
    assert store.latest("demo") is None
    first = store.save("demo", {"read": {"status": 200, "ok": True, "fields": {"id": "str"}}})
    second = store.save("demo", {"read": {"status": 200, "ok": True, "fields": {"id": "str"}}})
    assert first != second
    assert len(store.history("demo")) == 2
    latest = store.latest("demo")
    assert latest is not None
    assert latest.baseline["read"]["fields"] == {"id": "str"}


async def test_first_run_establishes_then_green_advances(tmp_path) -> None:
    store = BaselineStore(tmp_path)
    outcome = None
    for _ in range(2):
        client, token, flows = await _observe("good")
        try:
            outcome = await verify_flows(client, "demo", flows, store, token=token)
        finally:
            await client.aclose()
    assert outcome is not None
    assert outcome.established is False
    assert outcome.advanced is True
    assert outcome.findings == []
    assert len(store.history("demo")) == 2


async def test_regression_is_flagged_and_baseline_is_not_advanced(tmp_path) -> None:
    store = BaselineStore(tmp_path)

    client, token, flows = await _observe("good")
    try:
        established = await verify_flows(client, "demo", flows, store, token=token)
    finally:
        await client.aclose()
    assert established.established is True
    before = len(store.history("demo"))

    client, token, flows = await _observe("regressed_field")
    try:
        outcome = await verify_flows(client, "demo", flows, store, token=token)
    finally:
        await client.aclose()
    assert len(outcome.findings) == 1
    assert outcome.advanced is False
    assert len(store.history("demo")) == before
