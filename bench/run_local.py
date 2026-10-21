"""Local-mode bench: scan an app on this machine with two supplied accounts.

The app is served on loopback, so there is nothing to publish to prove ownership —
`verify_ownership` treats this machine as inherently owned. The app is also login-only
from the scanner's point of view (accounts are pre-created), so the scan uses
bring-your-own-accounts: two credentials are supplied and the scanner logs in as each.

This is the path a DIY builder takes for an app they run locally.

Requires the optional browser deps:
    pip install "tenantcheck[browser]" && patchright install chromium

Run:  python bench/run_local.py
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

import httpx
import uvicorn

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from controls.webapp import make_webapp
from verifier.browser_door import scan_browser
from verifier.ui import Credentials
from verifier.url_scan import scan_url


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


async def _signup(base: str, email: str) -> None:
    async with httpx.AsyncClient(base_url=base) as client:
        response = await client.post(
            "/signup", data={"name": "T", "email": email, "password": "pw12345"}
        )
    if response.status_code != 303:
        raise RuntimeError(f"signup failed for {email}: {response.status_code}")


async def _scan_one(app) -> dict:
    port = _free_port()
    server = _serve(app, port)
    url = f"http://127.0.0.1:{port}"
    accounts = [
        Credentials("owner@x.test", "pw12345"),
        Credentials("other@x.test", "pw12345"),
    ]
    for account in accounts:
        await _signup(url, account.email)
    started = time.perf_counter()
    try:
        found = await scan_url(
            url,
            require_ownership=True,
            browser_scan=lambda target: scan_browser(target, accounts=accounts),
        )
    finally:
        server.should_exit = True
    if found is None:
        return {"ownership": None, "resources_probed": 0, "findings": [], "seconds": 0.0}
    return {
        "ownership": found.ownership.method if found.ownership else None,
        "resources_probed": found.result.resources_probed,
        "findings": [
            {"check": finding.check, "title": finding.title}
            for finding in found.result.findings
        ],
        "seconds": round(time.perf_counter() - started, 2),
    }


async def _run() -> dict:
    vulnerable = await _scan_one(make_webapp(vulnerable=True))
    secure = await _scan_one(make_webapp())
    return {"vulnerable": vulnerable, "secure": secure}


def main() -> int:
    outcome = asyncio.run(_run())

    owned = all(outcome[key]["ownership"] == "loopback" for key in ("vulnerable", "secure"))
    vuln_found = any(f["check"] == "isolation" for f in outcome["vulnerable"]["findings"])
    false_positives = len(outcome["secure"]["findings"])
    ok = owned and vuln_found and false_positives == 0

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "outcome": outcome,
        "expected": {
            "ownership": "loopback",
            "cross_account_reads": 1,
            "false_positives": 0,
        },
        "found": int(vuln_found),
        "false_positives": false_positives,
        "ok": ok,
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"local-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | Local loopback + BYOA (no token; two supplied accounts) | 1 | "
        f"{int(vuln_found)} | {1.0 if vuln_found else 0.0:.2f} | {false_positives} | "
        f"{out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
