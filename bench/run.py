"""Stage 1 bench: measure access-control detection on the control apps.

Runs the isolation check against the `vuln` and `secure` twins in-process (no
server, no ports) and writes a timestamped result plus a RESULTS.md line.

Run:  python bench/run.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
import sys

import httpx

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from controls.app import make_app
from verifier.http import Client
from verifier.scan import scan
from verifier.target import control_target

# Ground truth: how many access-control bugs each control app actually has.
# vuln: cross-account read + cross-account write (2).  secure: none (0).
CASES = {"vuln": 2, "secure": 0}


async def main() -> int:
    results: dict[str, dict[str, object]] = {}
    for mode, expected in CASES.items():
        client = Client("http://control", transport=httpx.ASGITransport(app=make_app(mode)))
        try:
            result = await scan(client, control_target(name=f"control:{mode}"))
        finally:
            await client.aclose()
        results[mode] = {
            "expected_bugs": expected,
            "found": len(result.findings),
            "resources_probed": result.resources_probed,
            "checks": sorted({f.check for f in result.findings}),
        }

    vuln_found = int(results["vuln"]["found"])  # type: ignore[arg-type]
    secure_found = int(results["secure"]["found"])  # type: ignore[arg-type]
    recall = min(1.0, vuln_found / CASES["vuln"]) if CASES["vuln"] else 1.0
    false_positive_rate = float(secure_found > 0)

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "cases": results,
        "isolation_recall": recall,
        "false_positive_on_clean_app": false_positive_rate,
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"isolation-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | internal control (vuln+secure) | {CASES['vuln']} | {vuln_found} | "
        f"{recall:.2f} | {int(false_positive_rate)} | {out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if recall == 1.0 and false_positive_rate == 0.0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
