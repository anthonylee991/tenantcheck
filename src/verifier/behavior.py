"""Stage 2: behavioural baseline and regression detection.

The research is blunt about what actually keeps AI-built apps working: a
*verification gate* that compares the new state against the last known-good state
and flags what changed. This is that gate, black-box.

A **flow** is something the app does (a request a user's browser would make). A
**baseline** is a behavioural fingerprint of each flow on a known-good state: did
it work, and what shape did the response have. A **regression** is a previously-
working flow that stopped working or whose response changed underneath the user.

Fingerprints compare *shape*, not values, so they are stable across runs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .http import Client
from .models import Finding

Baseline = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Flow:
    method: str
    path: str
    name: str


def _signature(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _signature(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_signature(value[0])] if value else []
    return type(value).__name__


def _flatten(sig: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(sig, dict):
        for key, value in sig.items():
            out.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(sig, list):
        if sig:
            out.update(_flatten(sig[0], f"{prefix}[]"))
    else:
        out[prefix] = str(sig)
    return out


def _shape(response: Any) -> dict[str, str]:
    try:
        return _flatten(_signature(response.json()))
    except ValueError:
        return {"<body>": "non-json"}


async def capture(client: Client, flows: list[Flow], token: str | None = None) -> Baseline:
    """Fingerprint each flow on the app's current state."""
    baseline: Baseline = {}
    for flow in flows:
        resp = await client.request(flow.method, flow.path, token=token)
        baseline[flow.name] = {
            "status": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
            "fields": _shape(resp),
        }
    return baseline


def _regression(name: str, detail: str, severity: str, evidence: dict[str, Any]) -> Finding:
    return Finding(
        check="regression",
        severity=severity,  # type: ignore[arg-type]
        title=f"{name} changed",
        detail=detail,
        evidence=evidence,
    )


def compare(baseline: Baseline, current: Baseline) -> list[Finding]:
    """Return findings for flows whose behaviour regressed from baseline to current."""
    findings: list[Finding] = []
    for name, before in baseline.items():
        after = current.get(name)
        if after is None:
            findings.append(
                _regression(
                    name,
                    f"{name} is no longer reachable.",
                    "high",
                    {"flow": name, "before_status": before["status"], "status": None},
                )
            )
            continue

        if before["ok"] and not after["ok"]:
            findings.append(
                _regression(
                    name,
                    f"{name} used to work (HTTP {before['status']}) and now fails "
                    f"(HTTP {after['status']}).",
                    "high",
                    {"flow": name, "before_status": before["status"], "status": after["status"]},
                )
            )
            continue

        if not (before["ok"] and after["ok"]):
            continue

        removed = sorted(set(before["fields"]) - set(after["fields"]))
        changed = sorted(
            key
            for key in set(before["fields"]) & set(after["fields"])
            if before["fields"][key] != after["fields"][key]
        )
        if removed:
            findings.append(
                _regression(
                    name,
                    f"{name} no longer returns: {', '.join(removed)}. "
                    "The page that shows these may now be blank or wrong.",
                    "high",
                    {"flow": name, "before_status": 200, "status": 200, "removed_fields": removed},
                )
            )
        if changed:
            findings.append(
                _regression(
                    name,
                    f"{name} changed the type of: {', '.join(changed)}.",
                    "medium",
                    {"flow": name, "before_status": 200, "status": 200, "type_changes": changed},
                )
            )
    return findings


# --- the memory store: a per-app known-good baseline -------------------------


def app_key(url: str) -> str:
    """A stable, filesystem-safe key for an app, derived from its URL."""
    parsed = urlparse(url if "://" in url else f"//{url}")
    raw = (parsed.netloc + parsed.path).strip("/") or "app"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw)


def default_root() -> Path:
    return Path(os.environ.get("TENANTCHECK_STATE_DIR") or (Path.home() / ".tenantcheck" / "baselines"))


@dataclass(frozen=True)
class StoredBaseline:
    app: str
    created_at: str
    label: str
    baseline: Baseline


@dataclass(frozen=True)
class BehaviorOutcome:
    """What happened when a set of flows was checked against the stored baseline."""

    established: bool
    advanced: bool
    findings: list[Finding]
    comparison_to: str | None = None

    def status(self) -> str:
        if self.established:
            return "baseline established (first run)"
        if self.findings:
            return f"checked against {self.comparison_to}: {len(self.findings)} regression(s)"
        return f"checked against {self.comparison_to}: still green"


class BaselineStore:
    """Persists behavioural baselines per app, as versioned JSON on disk.

    Deliberately dumb and portable (no DB): one file per observation, newest wins.
    Swap for a real store later without changing callers.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(root) if root is not None else default_root()

    def _dir(self, app: str) -> Path:
        return self.root / app

    def save(self, app: str, baseline: Baseline, label: str = "") -> Path:
        directory = self._dir(app)
        directory.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        # The system clock can repeat within a save pair (coarse Windows timer), so
        # add a monotonic ordinal: names stay unique *and* sort in write order.
        ordinal = len(list(directory.glob("*.json"))) + 1
        path = directory / f"{created_at}-{ordinal:06d}.json"
        path.write_text(
            json.dumps(
                {"app": app, "created_at": created_at, "label": label, "baseline": baseline},
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def history(self, app: str) -> list[StoredBaseline]:
        directory = self._dir(app)
        if not directory.is_dir():
            return []
        records: list[StoredBaseline] = []
        for path in sorted(directory.glob("*.json")):
            records.append(StoredBaseline(**json.loads(path.read_text(encoding="utf-8"))))
        return records

    def latest(self, app: str) -> StoredBaseline | None:
        history = self.history(app)
        return history[-1] if history else None


async def verify_flows(
    client: Client,
    app: str,
    flows: list[Flow],
    store: BaselineStore,
    token: str | None = None,
) -> BehaviorOutcome:
    """Capture the flows, compare to the last known-good baseline, and advance it
    only when nothing regressed (the verification gate: keep the last green)."""
    current = await capture(client, flows, token=token)
    previous = store.latest(app)

    if previous is None:
        store.save(app, current, label="baseline")
        return BehaviorOutcome(established=True, advanced=True, findings=[])

    findings = compare(previous.baseline, current)
    if findings:
        return BehaviorOutcome(
            established=False, advanced=False, findings=findings, comparison_to=previous.created_at
        )

    store.save(app, current, label="green")
    return BehaviorOutcome(
        established=False, advanced=True, findings=[], comparison_to=previous.created_at
    )
