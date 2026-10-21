"""The isolation check: can one account reach another account's records?

Four probes over every record account A owns:

- **cross-account read**: replay the fetch *as B*. If B receives it, A's data is
  exposed to another signed-in user.
- **unauthenticated read**: replay the fetch with *no* credentials. If it still
  returns, the route is missing its auth check entirely.
- **cross-account write**: replay a benign mutation *as B*, then read back as A.
- **unauthenticated write**: same, with no credentials.

Reads assert on a marker unique to A's record rather than on "2xx" alone: an app
that returns an empty shell or a login wall with a 200 must not be reported as a
leak. Writes assert on the owner seeing our change afterwards, so an endpoint that
2xx's but ignores the body is not reported either.
"""

from __future__ import annotations

from .http import Client
from .models import Finding, ResourceRef, Session


def _leaks(response_status: int, body: str, resource: ResourceRef) -> bool:
    if not (200 <= response_status < 300):
        return False
    return bool(resource.marker) and resource.marker in body


async def probe_isolation(
    client: Client,
    attacker: Session,
    resources: list[ResourceRef],
    owner: Session | None = None,
) -> list[Finding]:
    findings: list[Finding] = []

    for resource in resources:
        owner_desc = owner.label if owner else "another account"

        # 1. Cross-account: a *different* signed-in user should not reach it.
        resp = await client.request(resource.method, resource.path, token=attacker.token)
        if _leaks(resp.status_code, resp.text, resource):
            findings.append(
                Finding(
                    check="isolation",
                    severity="high",
                    title="One account can read another account's data",
                    detail=(
                        f"Acting as {attacker.label}, {resource.method} {resource.path} "
                        f"returned data owned by {owner_desc}. The response contained a value "
                        f"unique to the other account's record."
                    ),
                    evidence={
                        "method": resource.method,
                        "path": resource.path,
                        "status": resp.status_code,
                        "as": attacker.label,
                        "owner": resource.owner_id,
                        "body": resp.text[:500],
                    },
                )
            )

        # 2. Unauthenticated: no credentials at all should not reach it.
        anon = await client.request(resource.method, resource.path)
        if _leaks(anon.status_code, anon.text, resource):
            findings.append(
                Finding(
                    check="unauthenticated",
                    severity="high",
                    title="A record is readable without signing in",
                    detail=(
                        f"{resource.method} {resource.path} returned {owner_desc}'s data "
                        f"to a request with no credentials."
                    ),
                    evidence={
                        "method": resource.method,
                        "path": resource.path,
                        "status": anon.status_code,
                        "as": "<anonymous>",
                        "owner": resource.owner_id,
                        "body": anon.text[:500],
                    },
                )
            )

        # 3 & 4. Writes: a mutation should be refused just like a read.
        if resource.write is not None and owner is not None:
            await _probe_write(client, resource, owner, findings, actor=attacker)
            await _probe_write(client, resource, owner, findings, actor=None)

    return findings


async def _probe_write(
    client: Client,
    resource: ResourceRef,
    owner: Session,
    findings: list[Finding],
    *,
    actor: Session | None,
) -> None:
    probe = resource.write
    if probe is None:
        return

    anonymous = actor is None
    actor_desc = "<anonymous>" if anonymous else actor.label
    resp = await client.request(
        probe.method, probe.path, token=None if anonymous else actor.token, json=probe.payload
    )
    if not (200 <= resp.status_code < 300):
        return

    # Confirm the change landed: the owner must now see our marker on read-back.
    readback = await client.request(resource.method, resource.path, token=owner.token)
    landed = 200 <= readback.status_code < 300 and probe.marker in readback.text
    if not landed and probe.marker not in resp.text:
        return

    if anonymous:
        findings.append(
            Finding(
                check="unauthenticated_write",
                severity="high",
                title="A record can be changed without signing in",
                detail=(
                    f"{probe.method} {probe.path} let a request with no credentials modify "
                    f"{owner.label}'s record."
                ),
                evidence={
                    "method": probe.method,
                    "path": probe.path,
                    "status": resp.status_code,
                    "as": "<anonymous>",
                    "owner": resource.owner_id,
                    "body": readback.text[:500] or resp.text[:500],
                },
            )
        )
    else:
        findings.append(
            Finding(
                check="cross_account_write",
                severity="high",
                title="One account can change another account's data",
                detail=(
                    f"Acting as {actor_desc}, {probe.method} {probe.path} modified a record "
                    f"owned by {owner.label}. The change was visible to the owner afterwards."
                ),
                evidence={
                    "method": probe.method,
                    "path": probe.path,
                    "status": resp.status_code,
                    "as": actor_desc,
                    "owner": resource.owner_id,
                    "body": readback.text[:500] or resp.text[:500],
                },
            )
        )
