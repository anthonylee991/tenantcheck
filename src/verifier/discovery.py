"""Discover resources owned by an account, to probe for cross-account access.

Stage 1 uses a per-app-shape strategy (create a record, read it back). Later
stages derive resources from observed network traffic generically; the interface
(`strategy(client, owner) -> list[ResourceRef]`) stays the same.
"""

from __future__ import annotations

import uuid

from .http import Client
from .models import ResourceRef, Session, WriteProbe


async def discover_invoices(
    client: Client,
    owner: Session,
    create_path: str = "/api/invoices",
) -> list[ResourceRef]:
    """Create a record as `owner` and capture the reference needed to probe it."""
    nonce = "vrfr-" + uuid.uuid4().hex
    created = await client.request(
        "POST",
        create_path,
        token=owner.token,
        json={"amount": 4242.42, "note": nonce},
    )
    created.raise_for_status()
    record = created.json()
    record_id = record["id"]
    path = f"{create_path}/{record_id}"

    # Read it back as the owner so the bench has the owner-side baseline.
    await client.request("GET", path, token=owner.token)

    # A benign mutation probe: write a second nonce and see if the owner sees it.
    write_nonce = "vrfr-w-" + uuid.uuid4().hex

    return [
        ResourceRef(
            method="GET",
            path=path,
            owner_id=owner.user_id,
            marker=nonce,
            sample=record,
            write=WriteProbe(
                method="PATCH", path=path, payload={"note": write_nonce}, marker=write_nonce
            ),
        )
    ]
