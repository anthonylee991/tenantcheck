"""Ownership proof: the scan is refused unless the submitter controls the domain."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

from controls.frontend import make_frontend
from verifier.ownership import PREFIX, normalise_host, site_token, verify_ownership

SUPABASE_URL = "https://abcdefghijklmnopqrst.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.abc123_XYZ"

URL = "http://frontend"
_NO_DNS = lambda _name: []  # deliberately no TXT records


def test_token_is_derived_from_host_only() -> None:
    assert site_token("https://App.Example.com/some/path") == site_token("http://app.example.com")
    assert site_token("https://a.example.com") != site_token("https://b.example.com")
    assert normalise_host("https://App.Example.com:8443/x") == "app.example.com"


async def test_meta_tag_proves_ownership() -> None:
    app = make_frontend(SUPABASE_URL, ANON_KEY, verification_token=site_token(URL))
    result = await verify_ownership(
        URL, transport=httpx.ASGITransport(app=app), txt_resolver=_NO_DNS
    )
    assert result.verified is True
    assert result.method == "meta_tag"


async def test_well_known_file_proves_ownership() -> None:
    token = site_token(URL)
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return "<html><head></head><body>app</body></html>"

    @app.get("/.well-known/tenantcheck-verification.txt")
    def well_known() -> Response:
        return Response(token, media_type="text/plain")

    result = await verify_ownership(
        URL, transport=httpx.ASGITransport(app=app), txt_resolver=_NO_DNS
    )
    assert result.verified is True
    assert result.method == "well_known"


async def test_dns_txt_proves_ownership() -> None:
    app = make_frontend(SUPABASE_URL, ANON_KEY)
    result = await verify_ownership(
        URL,
        transport=httpx.ASGITransport(app=app),
        txt_resolver=lambda _name: [f"{PREFIX}={site_token(URL)}"],
    )
    assert result.verified is True
    assert result.method == "dns_txt"


async def test_no_proof_is_refused() -> None:
    app = make_frontend(SUPABASE_URL, ANON_KEY)
    result = await verify_ownership(
        URL, transport=httpx.ASGITransport(app=app), txt_resolver=_NO_DNS
    )
    assert result.verified is False
    assert result.method is None
    assert set(result.attempts) == {"meta_tag", "well_known", "dns_txt"}
    assert site_token(URL) in result.instructions()
