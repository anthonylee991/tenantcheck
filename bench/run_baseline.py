"""Stage 2 store bench: the app's behaviour is remembered across runs.

A four-step timeline against the control twins:
  1. good            -> baseline established
  2. good            -> still green, baseline advances
  3. regressed_field -> regression flagged, baseline NOT advanced (last green kept)
  4. good (fixed)    -> still green, baseline advances

The point: the same check that stays quiet on a healthy app catches the one change
that broke it, and never forgets the last known-good version.

Run:  python bench/run_baseline.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
import sys
import tempfile
import time
import uuid

import httpx

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from controls.app import make_app
from verifier.behavior import BaselineStore, Flow, verify_flows
from verifier.http import Client

APP = "control-demo"

# (step, app state, expected findings, expected advance)
TIMELINE = [
    ("baseline", "good", 0, True),
    ("still green", "good", 0, True),
    ("silent regression", "regressed_field", 1, False),
    ("fixed", "good", 0, True),
]


async def _verify(mode: str, store: BaselineStore) -> dict:
    client = Client("http://control", transport=httpx.ASGITransport(app=make_app(mode)))
    try:
        signup = await client.request(
            "POST", "/signup", json={"email": f"vbase_{uuid.uuid4().hex[:8]}@x.test", "password": "pw"}
        )
        token = signup.json()["token"]
        created = await client.request(
            "POST", "/api/invoices", token=token, json={"amount": 5.0, "note": "hi"}
        )
        invoice_id = created.json()["id"]
        flows = [
            Flow("GET", "/api/invoices", "list invoices"),
            Flow("GET", f"/api/invoices/{invoice_id}", "read one invoice"),
        ]
        outcome = await verify_flows(client, APP, flows, store, token=token)
        return {
            "established": outcome.established,
            "advanced": outcome.advanced,
            "findings": len(outcome.findings),
        }
    finally:
        await client.aclose()


async def main() -> int:
    with tempfile.TemporaryDirectory() as state_dir:
        store = BaselineStore(state_dir)
        results = []
        ok = True
        started = time.perf_counter()
        for step, mode, expected_findings, expected_advance in TIMELINE:
            outcome = await _verify(mode, store)
            versions = len(store.history(APP))
            results.append({"step": step, "app_state": mode, "versions": versions, **outcome})
            if outcome["findings"] != expected_findings or outcome["advanced"] != expected_advance:
                ok = False
        elapsed = time.perf_counter() - started
        versions = len(store.history(APP))

    regressions = [r for r in results if r["app_state"] == "regressed_field"]
    detected = sum(r["findings"] for r in regressions)
    false_positives = sum(r["findings"] for r in results if r["app_state"] == "good")

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "app": APP,
        "timeline": results,
        "baseline_versions": versions,
        "regression_recall": detected / len(regressions) if regressions else 1.0,
        "false_positives_on_good": false_positives,
        "wall_clock_seconds": round(elapsed, 2),
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"baseline-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | Stage 2 store (4-run timeline, baseline advances only when green) | "
        f"{len(regressions)} | {detected} | {summary['regression_recall']:.2f} | "
        f"{false_positives} | {out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
