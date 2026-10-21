"""Browser-Target bench (B4): the unchanged prober, driven entirely through the UI.

Two fixture apps shaped like a real account app:
  - `vulnerable` - the record route has broken object-level authorization (any signed-in
    user can fetch or change any invoice by id), so the scan must find a cross-account
    read *and* a cross-account write;
  - secure       - the same app with the ownership check, so the scan must stay silent.

Neither app exposes a documented API to the verifier: it signs two accounts up through
the UI (B2), learns the record endpoint from traffic (B3), creates one record of its
own through the create form (so no user data is touched), and replays the prober
(unchanged) as the other account and anonymously. This is the payoff of the `Target`
seam.

Requires the optional browser deps:
    pip install "tenantcheck[browser]" && patchright install chromium

Run:  python bench/run_browser_target.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
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
from verifier.browser_target import BrowserClient, browser_target
from verifier.scan import scan


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


async def _scan_one(session: BrowserSession, app, engine: str) -> dict:
    port = _free_port()
    server = _serve(app, port)
    base = f"http://127.0.0.1:{port}"
    started = time.perf_counter()
    browser = BrowserClient(session, base)
    try:
        result = await scan(browser, browser_target(browser))
    finally:
        await browser.aclose()
        server.should_exit = True
    return {
        "engine": engine,
        "resources_probed": result.resources_probed,
        "findings": [
            {"check": finding.check, "title": finding.title} for finding in result.findings
        ],
        "seconds": round(time.perf_counter() - started, 2),
    }


async def _run() -> dict:
    config = BrowserConfig.from_env()
    async with BrowserSession(config) as session:
        vulnerable = await _scan_one(session, make_webapp(vulnerable=True), config.engine)
        secure = await _scan_one(session, make_webapp(), config.engine)
    return {"vulnerable": vulnerable, "secure": secure}


def main() -> int:
    outcome = asyncio.run(_run())

    vuln_found = any(f["check"] == "isolation" for f in outcome["vulnerable"]["findings"])
    write_found = any(
        f["check"] == "cross_account_write" for f in outcome["vulnerable"]["findings"]
    )
    false_positives = len(outcome["secure"]["findings"])
    ok = vuln_found and write_found and false_positives == 0

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "outcome": outcome,
        "expected": {
            "cross_account_reads": 1,
            "cross_account_writes": 1,
            "false_positives": 0,
        },
        "found": int(vuln_found),
        "writes_found": int(write_found),
        "false_positives": false_positives,
        "ok": ok,
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"browser-target-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | Browser target B4 (unchanged prober over the UI; read+write on "
        f"a scan-created record) | 1 | "
        f"{int(vuln_found)} | {1.0 if vuln_found else 0.0:.2f} | {false_positives} | "
        f"{out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
