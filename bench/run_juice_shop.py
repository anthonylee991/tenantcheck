"""External control: run the generic isolation check against OWASP Juice Shop.

Requires a running Juice Shop. Default URL http://localhost:3011; override with
JUICE_SHOP_URL. Bring one up with:

    docker run -d --rm -p 3011:3000 bkimminich/juice-shop

Run:  python bench/run_juice_shop.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from verifier.http import Client
from verifier.juice_shop import juice_shop_target
from verifier.scan import scan

URL = os.environ.get("JUICE_SHOP_URL", "http://localhost:3011")
EXPECTED = 1  # the basket IDOR


async def main() -> int:
    client = Client(URL)
    try:
        result = await scan(client, juice_shop_target())
    finally:
        await client.aclose()

    found = len(result.findings)
    recall = min(1.0, found / EXPECTED)
    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "target": "OWASP Juice Shop (external, not authored by us)",
        "url": URL,
        "expected_bugs": EXPECTED,
        "found": found,
        "recall": recall,
        "checks": sorted({f.check for f in result.findings}),
        "findings": [{"check": f.check, "title": f.title, "evidence": f.evidence} for f in result.findings],
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"juice-shop-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | OWASP Juice Shop (external) | {EXPECTED} | {found} | "
        f"{recall:.2f} | n/a | {out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if found >= EXPECTED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
