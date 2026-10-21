"""Product-door bench (B4 wired in): an unsupported stack, scanned by URL.

The app here is **not Supabase-backed**, so `scan_url` cannot read a config and would
normally return "unsupported". With the browser fallback enabled it scans the app the
way a person would: two accounts through the UI, endpoints from traffic, then the
unchanged prober. Two twins: `vulnerable` (cross-account read -> must be found) and
secure (must stay silent).

Requires the optional browser deps:
    pip install "tenantcheck[browser]" && patchright install chromium

Run:  python bench/run_url_browser.py
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
from verifier.browser_door import scan_browser
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


async def _scan_url(app) -> dict:
    port = _free_port()
    server = _serve(app, port)
    url = f"http://127.0.0.1:{port}"
    started = time.perf_counter()
    try:
        found = await scan_url(url, browser_scan=scan_browser)
    finally:
        server.should_exit = True
    if found is None:
        return {"stack": None, "resources_probed": 0, "findings": [], "seconds": 0.0}
    return {
        "stack": found.stack,
        "config": found.config is not None,
        "resources_probed": found.result.resources_probed,
        "findings": [
            {"check": finding.check, "title": finding.title}
            for finding in found.result.findings
        ],
        "seconds": round(time.perf_counter() - started, 2),
    }


async def _run() -> dict:
    vulnerable = await _scan_url(make_webapp(vulnerable=True))
    secure = await _scan_url(make_webapp())
    return {"vulnerable": vulnerable, "secure": secure}


def main() -> int:
    outcome = asyncio.run(_run())

    used_browser = all(
        outcome[key]["stack"] == "browser" for key in ("vulnerable", "secure")
    )
    vuln_found = any(f["check"] == "isolation" for f in outcome["vulnerable"]["findings"])
    false_positives = len(outcome["secure"]["findings"])
    ok = used_browser and vuln_found and false_positives == 0

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "outcome": outcome,
        "expected": {"stack": "browser", "cross_account_reads": 1, "false_positives": 0},
        "found": int(vuln_found),
        "false_positives": false_positives,
        "ok": ok,
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"url-browser-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | URL-in → browser fallback (non-Supabase app; vuln+secure) | 1 | "
        f"{int(vuln_found)} | {1.0 if vuln_found else 0.0:.2f} | {false_positives} | "
        f"{out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
