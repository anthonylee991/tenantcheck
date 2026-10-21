"""URL-in bench: point the product door at a fixture frontend backed by a real
Supabase (throwaway), and measure what comes out.

Requires the throwaway Supabase from bench/run_supabase.py, with
bench/seed_supabase.sql applied. The frontend is served in-process (ASGI), so no
ports or internet are involved.

Run:  python bench/run_url.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
import sys
import time

import httpx

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from controls.frontend import make_frontend
from verifier.url_scan import scan_url

SUPABASE_URL = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54421")
ANON = os.environ.get(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0",
)
FRONTEND_URL = "https://fixture-app.example"


async def main() -> int:
    frontend = make_frontend(SUPABASE_URL, ANON)
    started = time.perf_counter()
    scan = await scan_url(
        FRONTEND_URL,
        frontend_transport=httpx.ASGITransport(app=frontend),
    )
    elapsed = time.perf_counter() - started

    if scan is None:
        print("no supported stack found on the frontend")
        return 1

    flagged = sorted(
        {
            str(f.evidence.get("path", "")).split("/rest/v1/")[-1].split("?")[0]
            for f in scan.result.findings
        }
    )
    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "frontend": FRONTEND_URL,
        "discovered_stack": scan.stack,
        "discovered_supabase_url": scan.config.url,
        "discovered_key_prefix": scan.config.anon_key[:12] + "...",
        "tables_probed": scan.result.resources_probed,
        "flagged_tables": flagged,
        "expected_buggy": ["items"],
        "expected_clean": ["items_secure"],
        "false_positive_on_clean_table": float("items_secure" in flagged),
        "findings": [f.title for f in scan.result.findings],
        "wall_clock_seconds": round(elapsed, 2),
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"url-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    ok = flagged == ["items"] and "items_secure" not in flagged
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
