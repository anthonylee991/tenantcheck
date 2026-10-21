"""Provision two independent accounts ("A" and "B").

For the reference app (and any app with self-serve signup) we create them
automatically. When signup isn't automatable, the caller supplies two accounts
instead — the isolation check only needs *two strangers*, however they are made.
"""

from __future__ import annotations

import uuid

from .http import Client
from .models import Session


async def provision_two(
    client: Client,
    signup_path: str = "/signup",
    email_prefix: str = "verifier",
) -> tuple[Session, Session]:
    a = await _signup(client, signup_path, f"{email_prefix}+a{uuid.uuid4().hex[:6]}@example.test")
    b = await _signup(client, signup_path, f"{email_prefix}+b{uuid.uuid4().hex[:6]}@example.test")
    return a, b


async def _signup(client: Client, signup_path: str, email: str) -> Session:
    resp = await client.request(
        "POST",
        signup_path,
        json={"email": email, "password": "pw-" + uuid.uuid4().hex},
    )
    resp.raise_for_status()
    data = resp.json()
    return Session(label=email, user_id=data["user_id"], token=data["token"])
