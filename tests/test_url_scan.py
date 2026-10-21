"""URL-in flow: find the Supabase config a deployed frontend ships."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

from controls.frontend import make_frontend
from verifier.frontend import find_supabase
from verifier.http import Client

SUPABASE_URL = "https://abcdefghijklmnopqrst.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.abc123_XYZ"


async def test_finds_config_in_js_bundle() -> None:
    app = make_frontend(SUPABASE_URL, ANON_KEY)
    client = Client("http://frontend", transport=httpx.ASGITransport(app=app))
    try:
        config = await find_supabase(client)
    finally:
        await client.aclose()
    assert config is not None
    assert config.url == SUPABASE_URL
    assert config.anon_key == ANON_KEY


async def test_returns_none_when_page_has_no_config() -> None:
    app = make_frontend("https://not-supabase.example", "not-a-key")
    client = Client("http://frontend", transport=httpx.ASGITransport(app=app))
    try:
        config = await find_supabase(client)
    finally:
        await client.aclose()
    assert config is None


async def test_ignores_cross_origin_scripts() -> None:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return '<script src="https://evil.example/bundle.js"></script>'

    @app.get("/bundle.js")
    def local() -> Response:  # pragma: no cover - must never be requested cross-origin
        return Response("const supabaseUrl='https://abcdefghijklmnopqrst.supabase.co';"
                        "const supabaseKey='" + ANON_KEY + "';")

    client = Client("http://frontend", transport=httpx.ASGITransport(app=app))
    try:
        config = await find_supabase(client)
    finally:
        await client.aclose()
    assert config is None


async def test_finds_self_hosted_url_from_assignment() -> None:
    app = make_frontend("http://127.0.0.1:54421", ANON_KEY)
    client = Client("http://frontend", transport=httpx.ASGITransport(app=app))
    try:
        config = await find_supabase(client)
    finally:
        await client.aclose()
    assert config is not None
    assert config.url == "http://127.0.0.1:54421"
