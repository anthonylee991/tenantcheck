"""Orchestrate a scan: provision, discover, probe isolation, check behaviour.

Stage 1 finds live vulnerabilities. Stage 2 (when a baseline store is supplied)
remembers how the app behaved last time and flags anything that silently changed.
"""

from __future__ import annotations

from .behavior import BaselineStore, Flow, verify_flows
from .http import Client
from .isolation import probe_isolation
from .models import ScanResult
from .target import Target


async def scan(
    client: Client,
    target: Target,
    *,
    store: BaselineStore | None = None,
    app_key: str | None = None,
) -> ScanResult:
    account_a, account_b = await target.provision(client)
    resources = await target.discover(client, account_a)
    findings = await probe_isolation(client, account_b, resources, owner=account_a)

    baseline_status: str | None = None
    if store is not None and app_key is not None and resources:
        flows = [Flow(r.method, r.path, r.path) for r in resources]
        outcome = await verify_flows(client, app_key, flows, store, token=account_a.token)
        findings = findings + outcome.findings
        baseline_status = outcome.status()

    return ScanResult(
        target=target.name,
        resources_probed=len(resources),
        findings=findings,
        baseline_status=baseline_status,
    )
