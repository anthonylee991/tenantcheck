"""External control: run the generic isolation check against a real Supabase.

Requires a running Supabase (throwaway) with the ground-truth schema from
`seed_bench.sql`: `items` (RLS disabled) is the bug; `items_secure` (RLS enabled,
owner-only) is the clean twin. Defaults point at the throwaway project on 54421;
override with SUPABASE_URL / SUPABASE_ANON_KEY.

Run:  python bench/run_supabase.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
import re
import sys
import time

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from verifier.http import Client
from verifier.scan import scan
from verifier.supabase import supabase_target

URL = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54421")
ANON = os.environ.get(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0",
)

# Ground truth for the throwaway project.
BUGGY_TABLES = {"items", "posts"}
CLEAN_TABLES = {"items_secure", "posts_secure"}
IGNORED = {"post_titles"}  # a view: must be skipped, never flagged

_TABLE_RE = re.compile(r"/rest/v1/([^?]+)")


def _table_of(finding) -> str | None:
    match = _TABLE_RE.search(str(finding.evidence.get("path", "")))
    return match.group(1) if match else None


async def main() -> int:
    client = Client(URL, default_headers={"apikey": ANON})
    started = time.perf_counter()
    try:
        result = await scan(client, supabase_target(ANON, name="Supabase (RLS, throwaway)"))
    finally:
        await client.aclose()
    elapsed = time.perf_counter() - started

    flagged = {t for f in result.findings if (t := _table_of(f))}
    buggy_hit = len(BUGGY_TABLES & flagged)
    clean_hit = len(CLEAN_TABLES & flagged)
    ignored_hit = len(IGNORED & flagged)
    recall = buggy_hit / len(BUGGY_TABLES)
    false_positive_rate = clean_hit / len(CLEAN_TABLES)

    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "target": result.target,
        "url": URL,
        "tables_probed": result.resources_probed,
        "expected_buggy_tables": sorted(BUGGY_TABLES),
        "expected_clean_tables": sorted(CLEAN_TABLES),
        "ignored_tables": sorted(IGNORED),
        "flagged_tables": sorted(flagged),
        "isolation_recall": recall,
        "false_positive_on_clean_table": false_positive_rate,
        "false_positive_on_ignored": ignored_hit / len(IGNORED) if IGNORED else 0.0,
        "wall_clock_seconds": round(elapsed, 2),
        "findings": [
            {"check": f.check, "title": f.title, "path": f.evidence.get("path")}
            for f in result.findings
        ],
    }

    out_dir = _ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_file = out_dir / f"supabase-{stamp}.json"
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out_file}")

    results_md = _ROOT / "bench" / "RESULTS.md"
    existing = results_md.read_text(encoding="utf-8").rstrip()
    results_md.write_text(
        existing
        + f"\n| {stamp} | Supabase throwaway (RLS) | {len(BUGGY_TABLES)} | {buggy_hit} | "
        f"{recall:.2f} | {false_positive_rate:.2f} | {out_file.name} |\n",
        encoding="utf-8",
    )
    return 0 if recall == 1.0 and false_positive_rate == 0.0 and ignored_hit == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
