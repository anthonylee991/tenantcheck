"""B3: discover candidate resources from a driven browser's network traffic.

Stage 1 derives resources from a discoverable API (PostgREST introspection). This is
the browser equivalent: use the app as a logged-in person and watch the XHR/fetch
calls it makes, then keep the ones that look like data endpoints for a record the
current user owns. The output is a small, replayable list — the raw material a
browser-backed `Target` (B4) turns into isolation probes.

Split for testability: `candidates()` is pure (calls in, resources out) and needs no
browser; `TrafficRecorder`/`capture()` attach to a Playwright page and record.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger(__name__)

# Response/request resource types that are data calls (not documents or assets).
_DATA_TYPES = {"xhr", "fetch"}
_SKIP_METHODS = {"OPTIONS", "HEAD"}
_STATIC_EXT = {
    ".js", ".mjs", ".cjs", ".css", ".map", ".jsonld",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".avif", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".mp3", ".wav",
    ".wasm", ".pdf", ".zip",
}
# Hosts that are never the app's own data plane.
_TRACKER_HOSTS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net", "facebook.net",
    "facebook.com", "segment.io", "segment.com", "sentry.io", "sentry-cdn.com",
    "posthog.com", "hotjar.com", "fullstory.com", "mixpanel.com", "amplitude.com",
    "clarity.ms", "intercom.io", "hubspot.com", "criteo.com", "tiktok.com",
)
_UUID_LIKE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# Stripe-style / app-style ids: inv-abc123, usr_9f2, etc.
_PREFIXED_ID = re.compile(r"^[A-Za-z]{2,6}[-_][0-9A-Za-z]{4,}$")


@dataclass(frozen=True)
class NetworkCall:
    """One observed request/response. Pure data, so it is easy to synthesize in tests."""

    method: str
    url: str
    resource_type: str = ""
    status: int | None = None
    content_type: str = ""
    origin: str = ""
    post_data: str = ""


@dataclass(frozen=True)
class CandidateResource:
    """A data endpoint worth probing. `record` means it addresses one specific row."""

    method: str
    url: str
    path: str
    host: str
    kind: str = "collection"  # "collection" | "record"
    identifier: str | None = None


def _is_static(path: str) -> bool:
    lowered = path.lower()
    return any(lowered.endswith(ext) for ext in _STATIC_EXT)


def _is_tracker(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(tracker in host for tracker in _TRACKER_HOSTS)


def _looks_like_api(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in ("/api/", "/rest/", "/graphql", "/v1/", "/v2/"))


def _is_data_call(call: NetworkCall) -> bool:
    if call.method.upper() in _SKIP_METHODS:
        return False
    path = urlsplit(call.url).path
    if _is_static(path) or _is_tracker(call.url):
        return False
    return call.resource_type in _DATA_TYPES or _looks_like_api(path)


def _classify(path: str, query: str) -> tuple[str, str | None]:
    segments = [segment for segment in path.split("/") if segment]
    identifier: str | None = None
    if segments:
        last = segments[-1]
        if last.isdigit() or _is_uuid_like(last) or _PREFIXED_ID.match(last):
            identifier = last
    if identifier is None and query:
        for pair in query.split("&"):
            name, _, value = pair.partition("=")
            if name.lower() in ("id", "pk", "uuid", "row_id") and value:
                identifier = value.removeprefix("eq.")
                break
    kind = "record" if identifier is not None else "collection"
    return kind, identifier


def _is_uuid_like(text: str) -> bool:
    return bool(re.fullmatch(_UUID_LIKE, text))


def has_identifier_segment(path: str) -> bool:
    """True when the path already addresses a record somewhere in it.

    `/personas` is a collection; `/personas/<id>` and `/personas/<id>/stats` are not
    places to append another id.
    """
    return any(
        segment.isdigit() or _is_uuid_like(segment) or bool(_PREFIXED_ID.match(segment))
        for segment in path.split("/")
        if segment
    )


def _normalize(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def candidates(calls: list[NetworkCall]) -> list[CandidateResource]:
    """Keep the data calls, dedupe them, and classify each as a collection or a record."""
    seen: set[tuple[str, str]] = set()
    found: list[CandidateResource] = []
    for call in calls:
        if not _is_data_call(call):
            continue
        url = _normalize(call.url)
        method = call.method.upper()
        key = (method, url)
        if key in seen:
            continue
        seen.add(key)
        parts = urlsplit(url)
        kind, identifier = _classify(parts.path, parts.query)
        found.append(
            CandidateResource(
                method=method,
                url=url,
                path=parts.path + (f"?{parts.query}" if parts.query else ""),
                host=parts.netloc,
                kind=kind,
                identifier=identifier,
            )
        )
    return found


class TrafficRecorder:
    """Records response-level network events from a Playwright page.

    The handlers are synchronous (Playwright allows it), so the class is trivial to
    exercise with a fake page in tests - no browser required. Requests are recorded
    separately (`requests`) because a *sent* body is what tells a write probe how to
    reproduce a mutation the app itself made.
    """

    def __init__(self, origin: str | None = None) -> None:
        self.origin = origin or ""
        self.calls: list[NetworkCall] = []
        self.requests: list[NetworkCall] = []

    def _on_response(self, response: Any) -> None:
        try:
            request = response.request
            self.calls.append(
                NetworkCall(
                    method=request.method,
                    url=response.url,
                    resource_type=request.resource_type,
                    status=response.status,
                    content_type=(response.headers or {}).get("content-type", ""),
                    origin=self.origin,
                )
            )
        except Exception:  # noqa: BLE001 - a malformed event is simply not recorded
            return

    def _on_request(self, request: Any) -> None:
        try:
            self.requests.append(
                NetworkCall(
                    method=request.method,
                    url=request.url,
                    resource_type=getattr(request, "resource_type", ""),
                    origin=self.origin,
                    post_data=getattr(request, "post_data", "") or "",
                )
            )
        except Exception:  # noqa: BLE001 - a malformed event is simply not recorded
            return

    def attach(self, page: Any) -> None:
        page.on("response", self._on_response)
        page.on("request", self._on_request)

    def detach(self, page: Any) -> None:
        page.remove_listener("response", self._on_response)
        page.remove_listener("request", self._on_request)


async def capture(
    page: Any,
    drive: Callable[[], Awaitable[Any]],
    *,
    origin: str | None = None,
) -> list[NetworkCall]:
    """Run `drive()` while recording the page's responses; return the calls seen."""
    recorder = TrafficRecorder(origin=origin)
    recorder.attach(page)
    try:
        await drive()
    finally:
        recorder.detach(page)
    return recorder.calls
