"""B4: a browser-backed `Target`, so the unchanged isolation prober runs over the UI.

Stage 1's prober only knows how to (a) make two accounts and (b) list records one
owns, talking HTTP with a bearer token. This adapter supplies both from the browser:

- **accounts** are independent browser contexts (their own cookies), each signed up
  through the real UI (B2);
- **discovery** watches the owner's dashboard traffic (B3), keeps record endpoints,
  fetches one as the owner, and takes a distinctive value from the response as the
  proof marker.

`BrowserClient` is `Client`-shaped (`request(...) -> .status_code/.text/.json()`), so
`probe_isolation` is reused verbatim — the seam the whole design rests on.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

from .models import ResourceRef, Session, WriteProbe
from .network import (
    CandidateResource,
    TrafficRecorder,
    candidates,
    capture,
    has_identifier_segment,
)
from .target import Target
from .ui import (
    Credentials,
    ProvisioningError,
    add_record,
    new_account_credentials,
    provision_account,
    read_stored_token,
)
from .vision import Fallback, NullFallback

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserResponse:
    """The slice of an HTTP response the prober consumes."""

    status_code: int
    text: str
    url: str = ""

    def json(self) -> Any:
        return jsonlib.loads(self.text)


class BrowserClient:
    """A `Client`-shaped adapter over isolated browser contexts.

    `token` names an account context; `token=None` is the anonymous context. Requests
    go through Playwright's context-bound API client, so they carry exactly the
    cookies a real browser would for that account.
    """

    def __init__(
        self,
        session: Any,
        base_url: str,
        *,
        fallback: Fallback | None = None,
        accounts: list[Credentials] | None = None,
    ) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.fallback = fallback or NullFallback()
        self.accounts = list(accounts) if accounts is not None else None
        if self.accounts is not None and len(self.accounts) < 2:
            raise ValueError("bring-your-own-accounts needs two accounts")
        self._contexts: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}
        self._bearers: dict[str, str] = {}
        self._anonymous: Any | None = None

    async def open_account(
        self,
        credentials: Credentials | None = None,
        *,
        create: bool | None = None,
    ) -> Session:
        supplied = credentials is not None
        if credentials is None and self.accounts:
            credentials = self.accounts.pop(0)
            supplied = True
        creds = credentials or new_account_credentials()
        use_create = (not supplied) if create is None else create
        context = await self.session.new_context()
        page = await context.new_page()
        try:
            result = await provision_account(
                page, self.base_url, creds, fallback=self.fallback, create=use_create
            )
        except ProvisioningError:
            await _close_quietly(context)
            raise
        if not result.logged_in:
            await _close_quietly(context)
            hint = "" if supplied else " (this app is login-only; supply two accounts)"
            raise ProvisioningError(
                f"could not provision an account at {self.base_url} ({result.detail}){hint}"
            )
        token = f"acct-{len(self._contexts) + 1}"
        self._contexts[token] = context
        self._pages[token] = page
        bearer = await read_stored_token(page)
        if bearer:
            self._bearers[token] = bearer
        return Session(label=creds.email, user_id=creds.email, token=token)

    def page_for(self, session: Session) -> Any:
        return self._pages[session.token]

    async def request(
        self,
        method: str,
        path: str,
        token: str | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> BrowserResponse:
        context = await self._context(token)
        merged = dict(headers or {})
        bearer = self._bearers.get(token) if token is not None else None
        if bearer:
            merged.setdefault("authorization", f"Bearer {bearer}")
        data: bytes | None = None
        if json is not None:
            data = jsonlib.dumps(json).encode("utf-8")
            merged.setdefault("content-type", "application/json")
            merged.setdefault("accept", "application/json")
        response = await context.request.fetch(
            self._url(path),
            method=method.upper(),
            headers=merged,
            data=data,
            fail_on_status_code=False,
        )
        return BrowserResponse(response.status, await response.text(), response.url)

    async def _context(self, token: str | None) -> Any:
        if token is None:
            if self._anonymous is None:
                self._anonymous = await self.session.new_context()
            return self._anonymous
        return self._contexts[token]

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return self.base_url + (path if path.startswith("/") else f"/{path}")

    async def aclose(self) -> None:
        for context in [*self._contexts.values(), self._anonymous]:
            if context is not None:
                await _close_quietly(context)
        self._contexts.clear()
        self._pages.clear()
        self._bearers.clear()
        self._anonymous = None


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _marker_from_body(text: str, record_id: str) -> str:
    """Pick a distinctive value from the owner's record to use as the leak marker.

    Prefers a longer string carrying a digit or separator (e.g. a nonce); any long
    string is a fallback; the record id is the last resort.
    """
    try:
        body = jsonlib.loads(text)
    except ValueError:
        return record_id
    distinctive = ""
    longest = ""
    for value in _strings(body):
        if value == record_id or len(value) < 6:
            continue
        if len(value) > len(longest):
            longest = value
        separator = any(char.isdigit() for char in value) or "-" in value or "_" in value
        if separator and len(value) > len(distinctive):
            distinctive = value
    return distinctive or longest or record_id


async def _traffic(browser: BrowserClient, owner: Session) -> list[CandidateResource]:
    page = browser.page_for(owner)

    async def drive() -> None:
        await page.reload(wait_until="load")
        try:
            await page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:  # noqa: BLE001 - a slow app still yields what it sent
            await asyncio.sleep(1)

    calls = await capture(page, drive, origin=browser.base_url)
    return candidates(calls)


def _id_values(value: Any) -> list[str]:
    """Ids anywhere in a JSON payload, so a collection yields its record paths."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if isinstance(item, (str, int)) and not isinstance(item, bool):
                    name = key.lower()
                    if name in ("id", "uuid", "pk") or name.endswith("_id"):
                        text = str(item)
                        if text and text not in found:
                            found.append(text)
                else:
                    walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


def _child_path(collection_path: str, record_id: str) -> str:
    path = urlsplit(collection_path).path.rstrip("/")
    return f"{path}/{quote(record_id, safe='')}"


def _same_origin(url: str, base_url: str) -> bool:
    return urlsplit(url).netloc == urlsplit(base_url).netloc


def _nonce_field(post_data: str, nonce: str) -> str | None:
    """The body field the app filled with our nonce (so the write probe can reuse it)."""
    try:
        body: Any = jsonlib.loads(post_data)
    except ValueError:
        body = dict(parse_qsl(post_data))
    if isinstance(body, dict):
        for key, value in body.items():
            if isinstance(value, str) and nonce in value:
                return key
    return None


def _record_with(text: str, nonce: str) -> dict[str, Any] | None:
    """The one record in a listing that carries our nonce (not the listing wrapper)."""
    try:
        body = jsonlib.loads(text)
    except ValueError:
        return None

    def walk(node: Any) -> dict[str, Any] | None:
        if isinstance(node, dict):
            if any(isinstance(value, str) and nonce in value for value in node.values()):
                return node
            for item in node.values():
                hit = walk(item)
                if hit is not None:
                    return hit
        elif isinstance(node, list):
            for item in node:
                hit = walk(item)
                if hit is not None:
                    return hit
        return None

    hit = walk(body)
    return hit if isinstance(hit, dict) else None


async def _created_record(
    browser: BrowserClient, owner: Session, collections: list[str]
) -> ResourceRef | None:
    """Create a record of our own through the UI, then arm a write probe for it.

    Only records the scanner created are ever mutated: the user's existing data is
    left alone. If the app has no create affordance, returns None (read-only scan).
    """
    page = browser.page_for(owner)
    nonce = "vrfr-" + uuid.uuid4().hex
    recorder = TrafficRecorder(origin=browser.base_url)
    recorder.attach(page)
    try:
        made = await add_record(page, nonce)
    finally:
        recorder.detach(page)
    if not made:
        return None

    post = next(
        (
            call
            for call in recorder.requests
            if call.method.upper() == "POST"
            and nonce in call.post_data
            and _same_origin(call.url, browser.base_url)
        ),
        None,
    )
    if post is None:
        return None
    field = _nonce_field(post.post_data, nonce)
    if field is None:
        return None

    paths = [urlsplit(post.url).path, *collections]
    for path in dict.fromkeys(paths):
        if has_identifier_segment(path):
            continue
        listing = await browser.request("GET", path, token=owner.token)
        if not (200 <= listing.status_code < 300):
            continue
        sample = _record_with(listing.text, nonce)
        if sample is None:
            continue
        record_id = next(iter(_id_values(sample)), None)
        if not record_id:
            continue
        record_path = _child_path(path, record_id)
        response = await browser.request("GET", record_path, token=owner.token)
        resource = _resource(owner, record_path, response, record_id)
        if resource is None:
            continue
        write_nonce = "vrfr-w-" + uuid.uuid4().hex
        return resource.model_copy(
            update={
                "marker": nonce,
                "write": WriteProbe(
                    method="PATCH",
                    path=record_path,
                    payload={field: write_nonce},
                    marker=write_nonce,
                ),
            }
        )
    return None


def _resource(
    owner: Session, path: str, response: BrowserResponse, record_id: str
) -> ResourceRef | None:
    if not (200 <= response.status_code < 300):
        return None
    try:
        sample = response.json()
    except ValueError:
        return None
    if not isinstance(sample, dict):
        return None
    marker = _marker_from_body(response.text, record_id)
    if not marker or marker == record_id:
        return None
    return ResourceRef(
        method="GET",
        path=path,
        owner_id=owner.user_id,
        marker=marker,
        sample=sample,
        write=None,
    )


async def discover_via_traffic(
    browser: BrowserClient,
    owner: Session,
    *,
    max_derived: int = 5,
    write: bool = True,
) -> list[ResourceRef]:
    """Records the owner's dashboard fetched, plus records derived from collections.

    Dashboards often call only the collection (`/api/v1/personas`) and open a record
    on a detail page we never visit. Taking an id from the owner's own collection and
    fetching the child path gives the prober a real record without clicking.

    With `write`, the scan also creates one record of its own through the UI and arms
    a mutation probe for it, so cross-account writes can be tested without touching
    the user's existing data.
    """
    resources: list[ResourceRef] = []
    seen: set[str] = set()

    traffic = await _traffic(browser, owner)
    for candidate in (c for c in traffic if c.method == "GET" and c.kind == "record"):
        if candidate.path in seen:
            continue
        seen.add(candidate.path)
        response = await browser.request("GET", candidate.path, token=owner.token)
        resource = _resource(owner, candidate.path, response, candidate.identifier or "")
        if resource is not None:
            resources.append(resource)

    derived = 0
    for candidate in (c for c in traffic if c.method == "GET" and c.kind == "collection"):
        if derived >= max_derived or has_identifier_segment(candidate.path):
            continue
        listing = await browser.request("GET", candidate.path, token=owner.token)
        if not (200 <= listing.status_code < 300):
            continue
        try:
            payload = listing.json()
        except ValueError:
            continue
        for record_id in _id_values(payload):
            if derived >= max_derived or record_id == owner.user_id:
                continue
            path = _child_path(candidate.path, record_id)
            if path in seen:
                continue
            seen.add(path)
            derived += 1
            response = await browser.request("GET", path, token=owner.token)
            resource = _resource(owner, path, response, record_id)
            if resource is not None:
                resources.append(resource)

    if write:
        collections = [
            candidate.path
            for candidate in traffic
            if candidate.method == "GET"
            and candidate.kind == "collection"
            and not has_identifier_segment(candidate.path)
        ]
        created = await _created_record(browser, owner, collections)
        if created is not None:
            existing = next((item for item in resources if item.path == created.path), None)
            if existing is None:
                resources.insert(0, created)
            else:
                resources[resources.index(existing)] = existing.model_copy(
                    update={"write": created.write}
                )
    return resources


def browser_target(
    browser: BrowserClient, *, name: str = "browser (UI)", write: bool = True
) -> Target:
    """A `Target` that provisions and discovers entirely through the browser."""

    async def provision(_client: Any) -> tuple[Session, Session]:
        first = await browser.open_account()
        second = await browser.open_account()
        return first, second

    async def discover(_client: Any, owner: Session) -> list[ResourceRef]:
        return await discover_via_traffic(browser, owner, write=write)

    return Target(name=name, provision=provision, discover=discover)


async def _close_quietly(context: Any) -> None:
    try:
        await context.close()
    except Exception:
        log.debug("browser context close failed", exc_info=True)


__all__ = [
    "BrowserClient",
    "BrowserResponse",
    "browser_target",
    "discover_via_traffic",
]
