"""Supabase / RLS target shape — the first real-stack door.

Lovable, Bolt, Base44 and friends overwhelmingly run on Supabase, and the dominant
bug for their users is **missing Row Level Security**: a table with RLS off (or a
too-broad policy) is readable and writable by any signed-in user — or anyone at
all. That is exactly a cross-account access flaw, expressed through PostgREST.

Two pieces live here:

- `extract_config` — pull the Supabase project URL and public (anon) key out of a
  deployed frontend. Both are necessarily public in a Supabase web app, so this is
  pure client-side observation, no source access.
- `supabase_target` — a `Target` (same seam as the control twin) that signs up two
  users through GoTrue and seeds one owned row per table through PostgREST, so the
  generic isolation prober can run unchanged.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from .http import Client
from .models import ResourceRef, Session, WriteProbe
from .target import Target

_CLOUD_URL_RE = re.compile(r"https://[a-z0-9]{20}\.supabase\.(?:co|in)")
_URL_ASSIGN_RE = re.compile(
    r"""(?:supabaseUrl|supabase_url|SUPABASE_URL|VITE_SUPABASE_URL|NEXT_PUBLIC_SUPABASE_URL)"""
    r"""\s*[:=]\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_KEY_RE = re.compile(
    r"(?:eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+|sb_publishable_[A-Za-z0-9_\-]+)"
)

OWNER_COLS = (
    "user_id",
    "owner_id",
    "author_id",
    "creator_id",
    "profile_id",
    "member_id",
    "customer_id",
    "seller_id",
    "account_id",
    "created_by",
    "owner",
    "uid",
)
# Any column shaped like <role>_id / <role> also counts, so seller_id, creator_id, etc.
_OWNER_SHAPE = re.compile(
    r"^(user|owner|author|creator|profile|member|customer|seller)(_id)?$", re.IGNORECASE
)

TEXT_COLS = ("title", "name", "label", "note", "description", "content", "text", "body", "message")

# Formats that are strings in Postgres but not usable as a free-text marker.
_NON_TEXT_FORMATS = {
    "uuid",
    "date",
    "date-time",
    "timestamp",
    "timestamp with time zone",
    "timestamp without time zone",
    "time",
    "bytea",
    "json",
    "jsonb",
}


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    anon_key: str


def extract_config(text: str) -> SupabaseConfig | None:
    """Find the Supabase URL + public key in a page/bundle's text, if present."""
    # Prefer an explicit assignment (covers cloud and self-hosted/local URLs);
    # fall back to the cloud host pattern for minified bundles that inline it.
    assigned = _URL_ASSIGN_RE.search(text)
    url = assigned.group(1) if assigned else None
    if url is None:
        cloud = _CLOUD_URL_RE.search(text)
        url = cloud.group(0) if cloud else None
    key = _KEY_RE.search(text)
    if not url or not key:
        return None
    return SupabaseConfig(url=url.rstrip("/"), anon_key=key.group(0))


def supabase_target(anon_key: str, name: str = "Supabase (RLS)") -> Target:
    async def provision(client: Client) -> tuple[Session, Session]:
        a = await _signup(client)
        b = await _signup(client)
        return a, b

    async def discover(client: Client, owner: Session) -> list[ResourceRef]:
        return await _discover_tables(client, owner)

    return Target(name=name, provision=provision, discover=discover)


async def _signup(client: Client) -> Session:
    email = f"vrfr_{uuid.uuid4().hex[:10]}@example.test"
    resp = await client.request(
        "POST",
        "/auth/v1/signup",
        json={"email": email, "password": "Passw0rd!"},
    )
    resp.raise_for_status()
    body = resp.json()
    return Session(label=email, user_id=body["user"]["id"], token=body["access_token"])


def _is_textual(spec: dict) -> bool:
    return spec.get("type") == "string" and spec.get("format") not in _NON_TEXT_FORMATS


def _pick_owner(props: dict) -> str | None:
    for name in OWNER_COLS:
        if name in props:
            return name
    for name in props:
        if _OWNER_SHAPE.match(name):
            return name
    return None


def _pick_text(props: dict, exclude: tuple[str, ...] = ()) -> str | None:
    for name in TEXT_COLS:
        if name in props and name not in exclude and _is_textual(props[name]):
            return name
    for name, spec in props.items():
        if name == "id" or name in exclude:
            continue
        if _is_textual(spec):
            return name
    return None


def _default_for(spec: dict, nonce: str) -> object:
    """A plausible value for a required column, by type — so inserts succeed on
    real schemas that have NOT NULL columns without defaults."""
    enum = spec.get("enum")
    if enum:
        return enum[0]
    fmt, kind = spec.get("format"), spec.get("type")
    if fmt == "uuid":
        return str(uuid.uuid4())
    if fmt == "date":
        return "2020-01-01"
    if fmt in ("date-time", "timestamp", "timestamp with time zone", "timestamp without time zone"):
        return "2020-01-01T00:00:00Z"
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return "vrfr"


def _insert_payload(
    props: dict,
    required: list[str],
    owner_col: str,
    text_col: str,
    owner_uid: str,
    nonce: str,
) -> dict:
    payload: dict[str, object] = {}
    for col in required:
        spec = props.get(col, {})
        if col in (owner_col, text_col, "id") or "default" in spec:
            continue
        payload[col] = _default_for(spec, nonce)
    payload[owner_col] = owner_uid
    payload[text_col] = nonce
    return payload


async def _discover_tables(client: Client, owner: Session) -> list[ResourceRef]:
    root = await client.request("GET", "/rest/v1/")
    if root.status_code >= 300:
        return []
    definitions = root.json().get("definitions", {})

    resources: list[ResourceRef] = []
    for table, spec in definitions.items():
        props = spec.get("properties", {})
        owner_col = _pick_owner(props)
        if not owner_col:
            continue  # no owner we can attribute a row to; skip rather than guess
        text_col = _pick_text(props, exclude=(owner_col,))
        if not text_col:
            continue  # nothing to carry a marker

        nonce = "vrfr-" + uuid.uuid4().hex
        payload = _insert_payload(
            props, spec.get("required", []), owner_col, text_col, owner.user_id, nonce
        )
        inserted = await client.request(
            "POST",
            f"/rest/v1/{table}",
            token=owner.token,
            json=payload,
            headers={"Prefer": "return=representation"},
        )
        if inserted.status_code >= 300:
            continue  # not insertable (view, denied policy, bad FK); nothing to own
        try:
            row = inserted.json()[0]
        except (ValueError, IndexError, KeyError):
            continue

        write = None
        row_id = row.get("id")
        if row_id is not None:
            write_nonce = "vrfr-w-" + uuid.uuid4().hex
            write = WriteProbe(
                method="PATCH",
                path=f"/rest/v1/{table}?id=eq.{row_id}",
                payload={text_col: write_nonce},
                marker=write_nonce,
            )

        resources.append(
            ResourceRef(
                method="GET",
                path=f"/rest/v1/{table}?select=*",
                owner_id=owner.user_id,
                marker=nonce,
                sample=row,
                write=write,
            )
        )
    return resources
