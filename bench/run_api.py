"""Submission API bench: the two-step flow a non-developer actually goes through.

Drives the real FastAPI app with a TestClient. Ownership is checked for real
(against the in-process fixture frontend over ASGI); only the scan itself is
stubbed, because this bench measures the *API contract*, not the scan.

Timeline:
  1. submit an app that has published no proof  -> ownership_required + token
  2. publish the token, then recheck the same id -> complete + report
  3. submit a freshly-proven app                 -> complete straight away

Run:  python bench/run_api.py
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys
import time

import httpx
from fastapi.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from controls.frontend import make_frontend
from verifier.api import STATUS_COMPLETE, STATUS_OWNERSHIP_REQUIRED, SubmissionService, create_app
from verifier.models import Finding, ScanResult
from verifier.ownership import site_token, verify_ownership
from verifier.store import SubmissionStore
from verifier.supabase import SupabaseConfig
from verifier.url_scan import UrlScan

FRONTEND_URL = "http://frontend"
SUPABASE_URL = "https://abcdefghijklmnopqrst.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.abc123_XYZ"
TOKEN = site_token(FRONTEND_URL)


async def _stub_scan(url: str) -> UrlScan:
    result = ScanResult(
        target=f"{url} (Supabase)",
        resources_probed=2,
        findings=[Finding(title="items readable by anyone", detail="proof", evidence={})],
        baseline_status="baseline established (first run)",
    )
    return UrlScan(result=result, config=SupabaseConfig(url=SUPABASE_URL, anon_key=ANON_KEY))


def main() -> int:
    holder: dict[str, object] = {"frontend": make_frontend(SUPABASE_URL, ANON_KEY)}

    async def ownership(url: str):
        return await verify_ownership(
            url,
            transport=httpx.ASGITransport(app=holder["frontend"]),
            txt_resolver=lambda _name: [],
        )

    service = SubmissionService(
        SubmissionStore(":memory:"), ownership=ownership, scan=_stub_scan
    )
    app = create_app(service)

    steps = []
    started = time.perf_counter()
    with TestClient(app) as client:
        created = client.post("/api/submissions", json={"url": FRONTEND_URL}).json()
        steps.append({"step": "submit (no proof)", "status": created["status"]})

        holder["frontend"] = make_frontend(SUPABASE_URL, ANON_KEY, verification_token=TOKEN)
        rechecked = client.post(f"/api/submissions/{created['id']}/recheck").json()
        steps.append(
            {"step": "publish proof, recheck", "status": rechecked["status"],
             "findings": rechecked["findings_count"]}
        )

        fresh = client.post("/api/submissions", json={"url": FRONTEND_URL}).json()
        steps.append({"step": "submit (proven)", "status": fresh["status"]})
    elapsed = time.perf_counter() - started

    expected = [STATUS_OWNERSHIP_REQUIRED, STATUS_COMPLETE, STATUS_COMPLETE]
    observed = [s["status"] for s in steps]
    ok = observed == expected

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "target": FRONTEND_URL,
        "note": "ownership real (fixture over ASGI); scan stubbed",
        "steps": steps,
        "expected_statuses": expected,
        "observed_statuses": observed,
        "ownership_enforced": observed[0] == STATUS_OWNERSHIP_REQUIRED,
        "wall_clock_seconds": round(elapsed, 2),
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"api-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | Submission API (two-step flow, ownership real) | 3 | "
        f"{sum(1 for o, e in zip(observed, expected) if o == e)} | n/a | "
        f"{0 if summary['ownership_enforced'] else 1} | {out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
