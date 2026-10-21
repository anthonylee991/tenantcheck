"""UI-provisioning bench (B2): create accounts on a real HTML app.

Serves the fixture web app on a real port and drives it through the browser the way
a person would. Three scenarios:

1. normal form  -> heuristics find the fields and create the account (no fallback);
2. obfuscated form + a scripted fallback -> fallback is consulted and succeeds;
3. obfuscated form + the *default* fallback -> nothing is spent, no session.

Requires the optional browser deps:
    pip install "tenantcheck[browser]" && patchright install chromium

Run:  python bench/run_ui.py
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
from verifier.ui import Credentials, provision_account
from verifier.vision import Action, ScriptedFallback


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


async def _scenario(page, make_app, credentials, fallback, expected_fallback: bool) -> dict:
    port = _free_port()
    server = _serve(make_app(), port)
    url = f"http://127.0.0.1:{port}/"
    started = time.perf_counter()
    try:
        result = await provision_account(page, url, credentials, fallback=fallback)
    finally:
        server.should_exit = True
        await page.context.clear_cookies()
    return {
        "url": url,
        "logged_in": result.logged_in,
        "used_fallback": result.used_fallback,
        "detail": result.detail,
        "expected_fallback": expected_fallback,
        "seconds": round(time.perf_counter() - started, 2),
    }


async def _run() -> list[dict]:
    async with BrowserSession(BrowserConfig.from_env()) as session:
        page = await session.new_page()
        outcomes = [
            await _scenario(
                page,
                make_webapp,
                Credentials("alice@example.com", "hunter2pw"),
                None,
                expected_fallback=False,
            ),
            await _scenario(
                page,
                lambda: make_webapp(obfuscated=True),
                Credentials("bob@example.com", "hunter2pw"),
                ScriptedFallback(
                    [
                        Action("type", target="example.com", text="bob@example.com"),
                        Action("type", target="Password", text="hunter2pw"),
                        Action("click", target="Create account"),
                        Action("done"),
                    ]
                ),
                expected_fallback=True,
            ),
            await _scenario(
                page,
                lambda: make_webapp(obfuscated=True),
                Credentials("carol@example.com", "hunter2pw"),
                None,
                expected_fallback=False,
            ),
        ]
    return outcomes


def main() -> int:
    outcomes = asyncio.run(_run())

    expectations = [True, True, False]  # logged_in expected per scenario
    correct = sum(1 for outcome, want in zip(outcomes, expectations, strict=True) if outcome["logged_in"] is want)
    fallback_used = [outcome["used_fallback"] for outcome in outcomes]

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "outcomes": outcomes,
        "expected_logged_in": expectations,
        "correct": correct,
        "total": len(outcomes),
        "fallback_used": fallback_used,
        "ok": correct == len(outcomes) and fallback_used == [False, True, False],
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"ui-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | UI provisioning B2 (heuristic + fallback, fixture) | 3 | "
        f"{correct} | {correct / len(outcomes):.2f} | 0 | {out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
