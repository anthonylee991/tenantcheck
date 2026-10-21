"""Network-traffic resource-discovery bench (B3): learn the API from the browser.

Drives the fixture web app with the B2 provisioning step, then captures the XHR/fetch
traffic its dashboard makes while the page loads. From that raw traffic it must derive
both the collection endpoint (`/api/invoices`) and a record endpoint
(`/api/invoices/<id>`) - the browser equivalent of PostgREST introspection.

Requires the optional browser deps:
    pip install "tenantcheck[browser]" && patchright install chromium

Run:  python bench/run_network.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import pathlib
import socket
import sys
import threading
import time

import uvicorn

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from controls.webapp import make_webapp
from verifier.browser import BrowserConfig, BrowserSession
from verifier.network import candidates, capture
from verifier.ui import Credentials, provision_account

log = logging.getLogger(__name__)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    return server


async def _run(url: str) -> dict:
    config = BrowserConfig.from_env()
    started = time.perf_counter()
    async with BrowserSession(config) as session:
        page = await session.new_page()
        credentials = Credentials("bench@example.com", "hunter2pw")
        provisioned = await provision_account(page, url, credentials)
        signed_in = time.perf_counter()
        if not provisioned.logged_in:
            return {"provisioned": False, "calls": 0, "candidates": []}

        async def drive() -> None:
            await page.goto(f"{url}/dashboard", wait_until="load")
            try:
                await page.wait_for_function(
                    "() => document.querySelectorAll('#invoices p').length > 0", timeout=8000
                )
            except Exception:  # noqa: BLE001 - traffic is captured even if render is slow
                log.warning("dashboard data did not render in time")

        calls = await capture(page, drive, origin=url)
        finished = time.perf_counter()

    found = candidates(calls)
    return {
        "engine": config.engine,
        "provisioned": provisioned.logged_in,
        "calls": len(calls),
        "candidates": [
            {"method": c.method, "path": c.path, "kind": c.kind, "identifier": c.identifier}
            for c in found
        ],
        "provision_seconds": round(signed_in - started, 2),
        "capture_seconds": round(finished - signed_in, 2),
    }


def main() -> int:
    port = _free_port()
    server = _serve(make_webapp(), port)
    url = f"http://127.0.0.1:{port}"
    try:
        outcome = asyncio.run(_run(url))
    finally:
        server.should_exit = True

    paths = [candidate["path"] for candidate in outcome["candidates"]]
    found_collection = "/api/invoices" in paths
    found_record = any(
        candidate["kind"] == "record" and candidate["path"].startswith("/api/invoices/")
        for candidate in outcome["candidates"]
    )
    found = int(found_collection) + int(found_record)
    ok = found == 2

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "target": url,
        "outcome": outcome,
        "expected": {"collection": "/api/invoices", "record": "/api/invoices/<id>"},
        "found_collection": found_collection,
        "found_record": found_record,
        "ok": ok,
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"network-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | Traffic discovery B3 (XHR/fetch → collection + record, fixture) | 2 | "
        f"{found} | {found / 2:.2f} | 0 | {out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
