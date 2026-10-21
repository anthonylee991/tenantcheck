"""Bring-your-own-accounts: scan a login-only app with two existing accounts."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from harness import serve

from controls.webapp import make_webapp
from verifier.browser_target import BrowserClient, browser_target
from verifier.scan import scan
from verifier.ui import (
    Credentials,
    LoginOnlyError,
    new_account_credentials,
    provision_account,
    read_stored_token,
)

_LOGIN_ONLY = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Private</title></head>
<body><main>
<h1>Sign in</h1>
<p>Private instance - authorized accounts only.</p>
<form method="post" action="/login">
  <label>Username</label>
  <input type="text" name="login_id" autocomplete="username" placeholder="Enter username..." />
  <label>Password</label>
  <input type="password" name="password" autocomplete="current-password" placeholder="Enter password..." />
  <button type="submit">Sign In</button>
</form>
</main></body></html>"""


def make_login_only_app(*, user: str = "alice", secret: str = "pw12345") -> FastAPI:
    app = FastAPI(title="login-only")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _LOGIN_ONLY

    @app.post("/login")
    def login(login_id: str = Form(""), password: str = Form("")):
        if login_id != user or password != secret:
            return HTMLResponse("<h1>Wrong username or password</h1>")
        response = RedirectResponse("/welcome", status_code=303)
        response.set_cookie("session", user, httponly=True)
        return response

    @app.get("/welcome", response_class=HTMLResponse)
    def welcome() -> str:
        return f"<h1>Welcome, {user}</h1>"

    return app


def make_login_with_signup_link_app(*, user: str = "alice", secret: str = "pw12345678") -> FastAPI:
    """A login page whose header also has a "Sign up" link - the submit-step trap."""
    app = FastAPI(title="login-with-nav")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return """<html><body>
        <nav><a href="/signup">Sign up</a></nav>
        <h1>Log in</h1>
        <form method="post" action="/login">
          <input type="text" name="email" />
          <input type="password" name="password" />
          <button type="submit">Log in</button>
        </form></body></html>"""

    @app.post("/login")
    def login(email: str = Form(""), password: str = Form("")):
        if email != user or password != secret:
            return HTMLResponse("<h1>Wrong</h1>", status_code=401)
        response = RedirectResponse("/welcome", status_code=303)
        response.set_cookie("session", user, httponly=True)
        return response

    @app.get("/signup", response_class=HTMLResponse)
    def signup() -> str:
        return "<h1>Sign up</h1>"

    @app.get("/welcome", response_class=HTMLResponse)
    def welcome() -> str:
        return f"<h1>Welcome, {user}</h1>"

    return app


async def test_generated_accounts_use_a_domain_validators_accept(monkeypatch) -> None:
    monkeypatch.delenv("TENANTCHECK_ACCOUNT_DOMAIN", raising=False)
    assert new_account_credentials().email.endswith("@example.com")
    monkeypatch.setenv("TENANTCHECK_ACCOUNT_DOMAIN", "scan.localhost.test")
    assert new_account_credentials().email.endswith("@scan.localhost.test")


class _StoragePage:
    def __init__(self, data: dict) -> None:
        self.data = data

    async def evaluate(self, _script: str, _arg: object) -> dict:
        return self.data


async def test_read_stored_token_finds_a_bearer_token() -> None:
    token = "a" * 64
    page = _StoragePage({"mb_token": token, "theme": "dark"})
    assert await read_stored_token(page) == token


async def test_read_stored_token_ignores_short_or_missing_values() -> None:
    assert await read_stored_token(_StoragePage({"token": "x"})) is None
    assert await read_stored_token(_StoragePage({})) is None


class _FakeResponse:
    status = 200
    url = "http://app/api"

    async def text(self) -> str:
        return "{}"


class _FakeRequest:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def fetch(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return _FakeResponse()


class _FakeContext:
    def __init__(self) -> None:
        self.request = _FakeRequest()


async def test_stored_bearer_token_is_replayed_for_that_account() -> None:
    browser = BrowserClient(object(), "http://app")
    context = _FakeContext()
    browser._contexts["acct-1"] = context
    browser._bearers["acct-1"] = "tok_" + "x" * 40
    await browser.request("GET", "/api/v1/personas", token="acct-1")
    _, kwargs = context.request.calls[0]
    assert kwargs["headers"]["authorization"] == "Bearer tok_" + "x" * 40


async def test_anonymous_requests_carry_no_bearer_token() -> None:
    browser = BrowserClient(object(), "http://app")
    context = _FakeContext()
    browser._anonymous = context
    await browser.request("GET", "/api/v1/personas")
    _, kwargs = context.request.calls[0]
    assert "authorization" not in kwargs["headers"]


async def test_signup_mode_explains_a_login_only_app(browser_page: object) -> None:
    with serve(make_login_only_app()) as base, pytest.raises(LoginOnlyError):
        await provision_account(browser_page, base, Credentials("x@y.test", "pw"))


async def test_supplied_credentials_log_in_through_a_username_field(browser_page: object) -> None:
    with serve(make_login_only_app()) as base:
        result = await provision_account(
            browser_page, base, Credentials("alice", "pw12345"), create=False
        )
    assert result.logged_in is True


async def test_submit_uses_the_form_button_not_a_nav_link(browser_page: object) -> None:
    with serve(make_login_with_signup_link_app()) as base:
        result = await provision_account(
            browser_page, base, Credentials("alice", "pw12345678"), create=False
        )
    assert result.logged_in is True


async def test_byoa_scans_a_two_account_app(browser_session: object) -> None:
    with serve(make_webapp(vulnerable=True)) as base:
        for email in ("owner@x.test", "other@x.test"):
            await _signup(base, email)
        browser = BrowserClient(
            browser_session,
            base,
            accounts=[
                Credentials("owner@x.test", "pw12345"),
                Credentials("other@x.test", "pw12345"),
            ],
        )
        try:
            result = await scan(browser, browser_target(browser))
        finally:
            await browser.aclose()
    assert result.resources_probed >= 1
    assert any(finding.check == "isolation" for finding in result.findings)


async def _signup(base: str, email: str) -> None:
    async with httpx.AsyncClient(base_url=base) as client:
        response = await client.post(
            "/signup", data={"name": "T", "email": email, "password": "pw12345"}
        )
    assert response.status_code == 303
