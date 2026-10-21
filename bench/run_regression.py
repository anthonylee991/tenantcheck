"""Stage 2 bench: detect a silent regression between two app states.

Baseline is captured on the known-good control app; current is captured on a
twin that differs by one behaviour. The gate must flag the regression and stay
silent when nothing changed.

Run:  python bench/run_regression.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
import sys
import time
import uuid

import httpx

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from controls.app import make_app
from verifier.behavior import Flow, capture, compare
from verifier.http import Client

# (case name, current-app mode, expected number of regressions)
CASES = [
    ("no change", "good", 0),
    ("field silently dropped", "regressed_field", 1),
    ("endpoint starts failing", "regressed_error", 1),
]


async def _capture(mode: str) -> dict[str, dict]:
    client = Client("http://control", transport=httpx.ASGITransport(app=make_app(mode)))
    try:
        signup = await client.request(
            "POST", "/signup", json={"email": f"vrfr_{uuid.uuid4().hex[:8]}@x.test", "password": "pw"}
        )
        token = signup.json()["token"]
        created = await client.request(
            "POST", "/api/invoices", token=token, json={"amount": 10.0, "note": "hello"}
        )
        invoice_id = created.json()["id"]
        flows = [
            Flow("GET", "/api/invoices", "list invoices"),
            Flow("GET", f"/api/invoices/{invoice_id}", "read one invoice"),
        ]
        return await capture(client, flows, token=token)
    finally:
        await client.aclose()


async def main() -> int:
    baseline = await _capture("good")

    results = []
    ok = True
    started = time.perf_counter()
    for name, mode, expected in CASES:
        current = await _capture(mode)
        found = compare(baseline, current)
        results.append({"case": name, "expected": expected, "found": len(found), "findings": [f.title for f in found]})
        if len(found) != expected:
            ok = False
    elapsed = time.perf_counter() - started

    true_positives = sum(r["found"] for r in results if r["expected"] > 0)
    expected_positives = sum(r["expected"] for r in results if r["expected"] > 0)
    false_positives = sum(r["found"] for r in results if r["expected"] == 0)
    recall = true_positives / expected_positives if expected_positives else 1.0

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "baseline": "control:good",
        "cases": results,
        "regression_recall": recall,
        "false_positive_on_no_change": false_positives,
        "wall_clock_seconds": round(elapsed, 2),
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"regression-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | Stage 2 regression (control good vs {len(CASES) - 1} regressed twins) | "
        f"{expected_positives} | {true_positives} | {recall:.2f} | {false_positives} | {out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
