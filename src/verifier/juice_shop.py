"""OWASP Juice Shop target shape — the external positive control.

Juice Shop is a deliberately vulnerable app *we did not write*. Its basket
endpoint (`GET /rest/basket/{id}`) has the classic broken-access-control flaw: any
signed-in user can read any basket by id.

Detecting it with the *generic* isolation prober — the same code path that runs
against our control twin — is the point: the check is not welded to the app it
was first written against.
"""

from __future__ import annotations

import uuid

from .http import Client
from .models import ResourceRef, Session
from .target import Target


def juice_shop_target(name: str = "OWASP Juice Shop") -> Target:
    return Target(name=name, provision=_provision, discover=_discover)


async def _provision(client: Client) -> tuple[Session, Session]:
    a = await _register_and_login(client, "vrfr_a")
    b = await _register_and_login(client, "vrfr_b")
    return a, b


async def _register_and_login(client: Client, prefix: str) -> Session:
    email = f"{prefix}+{uuid.uuid4().hex[:6]}@example.test"
    password = "Passw0rd!" + uuid.uuid4().hex[:4]
    reg = await client.request(
        "POST",
        "/api/Users",
        json={"email": email, "password": password, "passwordRepeat": password},
    )
    reg.raise_for_status()
    login = await client.request(
        "POST", "/rest/user/login", json={"email": email, "password": password}
    )
    login.raise_for_status()
    auth = login.json()["authentication"]
    # Juice Shop hands back the account's basket id ("bid"); we probe that basket.
    return Session(label=email, user_id=str(auth["bid"]), token=auth["token"])


async def _discover(client: Client, owner: Session) -> list[ResourceRef]:
    path = f"/rest/basket/{owner.user_id}"
    resp = await client.request("GET", path, token=owner.token)
    resp.raise_for_status()
    data = resp.json().get("data", {})
    # The basket body carries no nonce we can set, so mark on the owner's user id,
    # which appears in the record's JSON ("UserId":<n>) and nowhere B's should.
    user_id_in_body = data.get("UserId")
    marker = f'"UserId":{user_id_in_body}' if user_id_in_body is not None else f'"id":{owner.user_id}'
    return [
        ResourceRef(method="GET", path=path, owner_id=owner.user_id, marker=marker, sample=data)
    ]
