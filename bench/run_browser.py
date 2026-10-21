"""Browser-driver bench (B1): prove the driver really drives a page.

Serves the fixture frontend on a real port, launches the browser through
`BrowserSession`, navigates to it, and reads the DOM back. This measures the
lifecycle (driver -> browser -> context -> page), not a check.

Requires the optional browser deps:
    pip install "tenantcheck[browser]" && patchright install chromium

Run:  python bench/run_browser.py
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

from controls.frontend import make_frontend
from verifier.browser import BrowserConfig, BrowserSession

SUPABASE_URL = "https://abcdefghijklmnopqrst.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.abc123_XYZ"


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


async def _drive(url: str) -> dict:
    config = BrowserConfig.from_env()
    started = time.perf_counter()
    async with BrowserSession(config) as session:
        launched = time.perf_counter()
        page = await session.new_page()
        await page.goto(url, wait_until="load")
        loaded = time.perf_counter()
        title = await page.title()
        preload = await page.get_attribute("link[rel=modulepreload]", "href")
        closed = time.perf_counter()
    return {
        "engine": config.engine,
        "headless": config.headless,
        "title": title,
        "modulepreload_href": preload,
        "launch_seconds": round(launched - started, 2),
        "navigate_seconds": round(loaded - launched, 2),
        "read_seconds": round(closed - loaded, 2),
    }


def main() -> int:
    port = _free_port()
    server = _serve(make_frontend(SUPABASE_URL, ANON_KEY), port)
    url = f"http://127.0.0.1:{port}/"
    try:
        outcome = asyncio.run(_drive(url))
    finally:
        server.should_exit = True

    ok = outcome["title"] == "Invoice App" and outcome["modulepreload_href"] == "/assets/index-abc123.js"

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "target": url,
        "outcome": outcome,
        "expected_title": "Invoice App",
        "ok": ok,
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"browser-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | Browser driver B1 (launch + navigate + DOM read, fixture) | 1 | "
        f"{1 if ok else 0} | n/a | {0 if ok else 1} | {out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
