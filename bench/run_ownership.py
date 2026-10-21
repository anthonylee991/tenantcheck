"""Ownership-gate bench: only a proven owner gets scanned.

Four fixture states, one exposure each. The gate must accept every real proof and
refuse the app that published none - and a stray token never counts.

Run:  python bench/run_ownership.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
import sys
import time

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from controls.frontend import make_frontend
from verifier.ownership import PREFIX, site_token, verify_ownership

URL = "http://frontend"
SUPABASE_URL = "https://abcdefghijklmnopqrst.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.abc123_XYZ"
TOKEN = site_token(URL)


def _well_known_only() -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return "<html><head></head><body>app</body></html>"

    @app.get("/.well-known/tenantcheck-verification.txt")
    def well_known() -> Response:
        return Response(TOKEN, media_type="text/plain")

    return app


async def main() -> int:
    cases = [
        ("meta tag", make_frontend(SUPABASE_URL, ANON_KEY, verification_token=TOKEN), lambda _n: [], True),
        ("well-known file", _well_known_only(), lambda _n: [], True),
        ("dns txt", make_frontend(SUPABASE_URL, ANON_KEY), lambda _n: [f"{PREFIX}={TOKEN}"], True),
        ("no proof", make_frontend(SUPABASE_URL, ANON_KEY), lambda _n: [], False),
        ("wrong token", make_frontend(SUPABASE_URL, ANON_KEY), lambda _n: [f"{PREFIX}=deadbeef"], False),
    ]

    results = []
    ok = True
    started = time.perf_counter()
    for name, app, resolver, expected in cases:
        result = await verify_ownership(
            URL, transport=httpx.ASGITransport(app=app), txt_resolver=resolver
        )
        results.append(
            {"case": name, "expected_verified": expected, "verified": result.verified, "method": result.method}
        )
        if result.verified != expected:
            ok = False
    elapsed = time.perf_counter() - started

    should_accept = [r for r in results if r["expected_verified"]]
    should_refuse = [r for r in results if not r["expected_verified"]]
    accepted = sum(1 for r in should_accept if r["verified"])
    false_accepts = sum(1 for r in should_refuse if r["verified"])

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "target": "fixture frontend",
        "cases": results,
        "proof_recall": accepted / len(should_accept) if should_accept else 1.0,
        "false_accepts": false_accepts,
        "wall_clock_seconds": round(elapsed, 2),
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"ownership-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | Ownership gate (3 real proofs + 2 non-proofs, fixture) | "
        f"{len(should_accept)} | {accepted} | {summary['proof_recall']:.2f} | "
        f"{false_accepts} | {out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
