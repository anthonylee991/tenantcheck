"""UI provisioning (B2): create an account by acting like a person.

Given an app URL and credentials, drive the signup (or login) form the way a human
would: find the fields, fill them, submit, and confirm a signed-in session. Common
conventions are handled deterministically; when a form is unfamiliar the optional
vision fallback (see docs/VISION_FALLBACK.md) is consulted, off by default.

Apps that are login-only (no signup path) can still be scanned when the caller
supplies two existing accounts (bring-your-own-accounts); `provision_account(...,
create=False)` logs in instead of signing up.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .vision import Action, BudgetExceeded, Fallback, NullFallback

log = logging.getLogger(__name__)


class ProvisioningError(RuntimeError):
    """Raised when an account could not be created or logged into."""


class LoginOnlyError(ProvisioningError):
    """Raised when an app has no signup path, so it needs supplied accounts."""

    def __init__(self, url: str) -> None:
        super().__init__(f"{url} is login-only (no signup link or signup page found)")
        self.url = url


BANNER_TEXTS = (
    "accept all", "accept", "i agree", "agree", "got it", "allow all", "ok", "continue",
)
SIGNUP_TEXTS = (
    "sign up", "signup", "sign-up", "register", "create account", "create an account",
    "get started", "start free", "try for free", "join", "create your account",
)
LOGIN_TEXTS = ("log in", "login", "sign in", "signin", "sign-in")
SUBMIT_TEXTS = (
    "create account", "create your account", "sign up", "signup", "register", "submit",
    "continue", "create", "next", "log in", "login", "sign in",
)
SIGNUP_PATHS = ("/signup", "/sign-up", "/register", "/auth/signup", "/users/sign_up", "/join")
LOGIN_PATHS = ("/login", "/sign-in", "/signin", "/auth/login")
CREATE_TEXTS = ("new", "add", "create")

EMAIL_SELECTORS = (
    'input[type="email"]',
    'input[autocomplete="email"]',
    'input[name*="email" i]',
    'input[id*="email" i]',
    'input[placeholder*="email" i]',
    'input[name="username" i]',
    'input[autocomplete="username"]',
    'input[name*="user" i]',
    'input[id*="user" i]',
    'input[placeholder*="user" i]',
)
PASSWORD_SELECTORS = ('input[type="password"]',)
NAME_SELECTORS = (
    'input[autocomplete="name"]',
    'input[name*="name" i]',
    'input[id*="name" i]',
    'input[placeholder*="name" i]',
)
TEXT_FIELD_SELECTORS = ('input[type="text"]', "textarea", "input:not([type])")
SESSION_COOKIE_HINTS = ("session", "token", "auth", "jwt", "access")
TOKEN_KEY_HINTS = ("token", "jwt", "auth", "session", "bearer")
AUTH_URL_HINTS = ("login", "signin", "sign-in", "signup", "sign-up", "register", "/auth")


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str

    @property
    def name(self) -> str:
        return self.email.split("@", 1)[0].replace(".", " ").replace("_", " ").title()


def new_account_credentials() -> Credentials:
    """A throwaway account. The domain is reserved for docs, so no inbox is involved.

    `example.test` is *rejected* by strict validators (email-validator treats `.test`
    as special-use), which fails signup on real apps. `example.com` is accepted and
    still reserved; override with TENANTCHECK_ACCOUNT_DOMAIN if an app blocks it.
    """
    domain = os.environ.get("TENANTCHECK_ACCOUNT_DOMAIN", "").strip() or "example.com"
    return Credentials(f"vrfr_{uuid.uuid4().hex[:10]}@{domain}", "Passw0rd!vrfr")


@dataclass
class ProvisionResult:
    email: str
    logged_in: bool
    used_fallback: bool
    detail: str = ""


# --- low-level page helpers ---------------------------------------------------


async def _locator_if_visible(page: Any, selector: str, timeout_ms: int) -> Any | None:
    try:
        locator = page.locator(selector).first
        if await locator.count() and await locator.is_visible(timeout=timeout_ms):
            return locator
    except Exception:  # noqa: BLE001 - a bad selector or detached node just moves on
        return None
    return None


async def _role_locator(page: Any, role: str, name: re.Pattern[str], timeout_ms: int) -> Any | None:
    try:
        locator = page.get_by_role(role, name=name).first
        if await locator.count() and await locator.is_visible(timeout=timeout_ms):
            return locator
    except Exception:  # noqa: BLE001 - role may not exist on this page
        return None
    return None


async def _first_visible(page: Any, selectors: tuple[str, ...], timeout_ms: int = 1500) -> Any | None:
    for selector in selectors:
        locator = await _locator_if_visible(page, selector, timeout_ms)
        if locator is not None:
            return locator
    return None


async def _first_visible_in(
    scope: Any, selectors: tuple[str, ...], timeout_ms: int = 500
) -> Any | None:
    """Like `_first_visible`, but only inside `scope` (a form, a dialog)."""
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if await locator.count() and await locator.is_visible(timeout=timeout_ms):
                return locator
        except Exception:
            log.debug("selector failed inside scope", exc_info=True)
            continue
    return None


async def _is_visible(item: Any, timeout_ms: int = 500) -> bool:
    try:
        return bool(await item.is_visible(timeout=timeout_ms))
    except Exception:  # noqa: BLE001 - detached node is simply not visible
        return False


async def _click(locator: Any) -> bool:
    try:
        await locator.click(timeout=5000)
        return True
    except Exception:  # noqa: BLE001 - element vanished or was covered
        return False


async def _click_text(page: Any, texts: tuple[str, ...] | list[str], timeout_ms: int = 1500) -> bool:
    for text in texts:
        if not text:
            continue
        pattern = re.compile(re.escape(text), re.IGNORECASE)
        for role in ("button", "link"):
            locator = await _role_locator(page, role, pattern, timeout_ms)
            if locator is not None and await _click(locator):
                return True
        locator = await _locator_if_visible(page, f"text=/{re.escape(text)}/i", timeout_ms)
        if locator is not None and await _click(locator):
            return True
    return False


async def _has_text(page: Any, texts: tuple[str, ...] | list[str], timeout_ms: int = 500) -> bool:
    for text in texts:
        if not text:
            continue
        pattern = re.compile(re.escape(text), re.IGNORECASE)
        for role in ("button", "link"):
            if await _role_locator(page, role, pattern, timeout_ms) is not None:
                return True
    return False


def _clean_target(description: str) -> str:
    text = description.strip()
    text = re.sub(r"\s+(button|link|icon|field|element|box|input)\b.*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(in|on|at|of|from|near)\s+.*", "", text, flags=re.IGNORECASE)
    return text.strip()


async def _goto(page: Any, url: str) -> bool:
    try:
        await page.goto(url, wait_until="load")
        return True
    except Exception:  # noqa: BLE001 - a missing auth path is expected
        return False


async def _settle(page: Any) -> None:
    try:
        await page.wait_for_load_state("load", timeout=5000)
    except Exception:  # noqa: BLE001 - a non-navigating step never settles
        log.debug("page did not settle after action")
    await asyncio.sleep(0.2)


async def dismiss_banners(page: Any) -> None:
    await _click_text(page, BANNER_TEXTS, timeout_ms=600)


async def _has_signout(page: Any) -> bool:
    for text in ("sign out", "log out", "logout", "sign-out"):
        locator = await _role_locator(page, "button", re.compile(text, re.IGNORECASE), 500)
        if locator is not None:
            return True
    return False


async def read_stored_token(page: Any) -> str | None:
    """Find a bearer token the app keeps in localStorage/sessionStorage.

    Many SPAs authenticate API calls with a token rather than a cookie, so neither
    session detection nor our own replayed requests can rely on cookies alone.
    """
    try:
        found = await page.evaluate(
            """(hints) => {
              const out = {};
              for (const store of [localStorage, sessionStorage]) {
                for (let i = 0; i < store.length; i++) {
                  const key = store.key(i);
                  if (hints.some(h => key.toLowerCase().includes(h))) {
                    out[key] = store.getItem(key);
                  }
                }
              }
              return out;
            }""",
            list(TOKEN_KEY_HINTS),
        )
    except Exception:  # noqa: BLE001 - no storage on about:blank or a sandboxed frame
        return None
    if not isinstance(found, dict):
        return None
    for value in found.values():
        if isinstance(value, str) and 20 <= len(value) <= 4096:
            return value
    return None


async def _logged_in(page: Any) -> bool:
    if await _has_signout(page):
        return True
    if await read_stored_token(page) is not None:
        return True
    url = (page.url or "").lower()
    if any(hint in url for hint in AUTH_URL_HINTS):
        return False
    try:
        cookies = await page.context.cookies()
    except Exception:  # noqa: BLE001 - no context means no session cookie
        return False
    return any(
        any(hint in cookie.get("name", "").lower() for hint in SESSION_COOKIE_HINTS)
        for cookie in cookies
    )


async def _await_session(page: Any, timeout: float = 2.5) -> bool:
    """A single-page app updates its session after the form submit; give it a moment."""
    deadline = time.monotonic() + timeout
    while True:
        if await _logged_in(page):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.2)


# --- action execution (for the fallback) --------------------------------------


async def execute_action(page: Any, action: Action) -> bool:
    """Perform one fallback action. Prefers locating by description over coordinates."""
    if action.kind == "click":
        target = _clean_target(action.target)
        if target and await _click_text(page, [target]):
            await _settle(page)
            return True
        if action.coordinates:
            await page.mouse.click(*action.coordinates)
            await _settle(page)
            return True
        return False
    if action.kind == "type":
        if action.target:
            locator = await _first_visible(
                page,
                (
                    f'input[placeholder*="{action.target}" i]',
                    f'[aria-label*="{action.target}" i]',
                    f'input[name*="{action.target}" i]',
                ),
            )
            if locator is not None:
                await locator.fill(action.text)
                return True
            if await _click_text(page, [_clean_target(action.target)]):
                await page.keyboard.type(action.text)
                return True
        await page.keyboard.type(action.text)
        return True
    if action.kind == "press":
        await page.keyboard.press(action.key or "Enter")
        await _settle(page)
        return True
    if action.kind == "scroll":
        await page.mouse.wheel(0, 400)
        return True
    if action.kind == "wait":
        await asyncio.sleep(1)
        return True
    return False


async def _assist(page: Any, fallback: Fallback, goal: str, max_steps: int = 8) -> bool:
    for _ in range(max_steps):
        try:
            action = await fallback.next_action(page, goal)
        except BudgetExceeded:
            return False
        if action.kind == "done":
            return True
        if action.kind == "give_up":
            return False
        if not await execute_action(page, action):
            return False
        await asyncio.sleep(0.3)
    return False


# --- provisioning -------------------------------------------------------------


async def _open_signup(page: Any, base_url: str) -> str:
    """Open a signup form. Returns 'form', 'login_only', or 'none'."""
    if await _click_text(page, SIGNUP_TEXTS):
        await _settle(page)
        return "form"
    for path in SIGNUP_PATHS:
        if await _goto(page, base_url.rstrip("/") + path) and await _first_visible(
            page, ("form",), timeout_ms=1000
        ):
            return "form"
    await _goto(page, base_url)
    if await _first_visible(page, PASSWORD_SELECTORS):
        return "login_only" if await _has_text(page, LOGIN_TEXTS) else "form"
    return "none"


async def _open_login(page: Any, base_url: str) -> bool:
    if await _first_visible(page, PASSWORD_SELECTORS):
        return True
    if await _click_text(page, LOGIN_TEXTS):
        await _settle(page)
        if await _first_visible(page, PASSWORD_SELECTORS):
            return True
    for path in LOGIN_PATHS:
        if await _goto(page, base_url.rstrip("/") + path) and await _first_visible(
            page, PASSWORD_SELECTORS, timeout_ms=1000
        ):
            return True
    return False


async def _locator_and_count(page: Any, selector: str) -> tuple[Any | None, int]:
    try:
        locator = page.locator(selector)
        return locator, await locator.count()
    except Exception:  # noqa: BLE001 - a bad selector yields nothing
        return None, 0


async def _all_visible(page: Any, selectors: tuple[str, ...]) -> list[Any]:
    found: list[Any] = []
    for selector in selectors:
        locator, count = await _locator_and_count(page, selector)
        if locator is None:
            continue
        for index in range(count):
            item = locator.nth(index)
            if await _is_visible(item):
                found.append(item)
    return found


async def _can_fill(page: Any) -> bool:
    email = await _first_visible(page, EMAIL_SELECTORS)
    password = await _first_visible(page, PASSWORD_SELECTORS)
    return email is not None and password is not None


async def _all_visible_in(scope: Any, selectors: tuple[str, ...]) -> list[Any]:
    found: list[Any] = []
    for selector in selectors:
        try:
            locator = scope.locator(selector)
            count = await locator.count()
        except Exception:
            log.debug("selector failed inside form", exc_info=True)
            continue
        for index in range(count):
            item = locator.nth(index)
            if await _is_visible(item):
                found.append(item)
    return found


async def _form_of(item: Any) -> Any | None:
    try:
        form = item.locator("xpath=ancestor::form").first
        return form if await form.count() else None
    except Exception:  # noqa: BLE001 - detached node or no form
        return None


async def _click_first(locator: Any) -> bool:
    try:
        target = locator.first
        if await target.count() and await target.is_visible() and await target.is_enabled():
            return await _click(target)
    except Exception:  # noqa: BLE001 - detached node or a bad selector
        return False
    return False


async def _submit_form(page: Any, password: Any | None) -> bool:
    """Submit the form that holds the password. Never click a nav *link* named "Sign up".

    The form's own submit button is the only unambiguous target: a text match would
    happily follow a header link and abandon the half-filled form.
    """
    submit = 'button[type="submit"], input[type="submit"]'
    if password is not None:
        form = await _form_of(password)
        if form is not None and await _click_first(form.locator(submit)):
            return True
    if await _click_first(page.locator(submit)):
        return True
    for text in SUBMIT_TEXTS:
        pattern = re.compile(re.escape(text), re.IGNORECASE)
        locator = await _role_locator(page, "button", pattern, 1000)
        if locator is not None and await _click(locator):
            return True
    return False


async def _fill_and_submit(page: Any, credentials: Credentials, *, include_name: bool) -> None:
    if include_name:
        name_field = await _first_visible(page, NAME_SELECTORS)
        if name_field is not None:
            await name_field.fill(credentials.name)
    email_field = await _first_visible(page, EMAIL_SELECTORS)
    if email_field is not None:
        await email_field.fill(credentials.email)
    password_field = None
    for field in await _all_visible(page, PASSWORD_SELECTORS):
        await field.fill(credentials.password)
        password_field = password_field or field
    if not await _submit_form(page, password_field) and password_field is not None:
        await password_field.press("Enter")
    await _settle(page)


async def _blank_form(page: Any) -> Any | None:
    """A visible form with a text field and no password field: the app's create form."""
    try:
        forms = page.locator("form")
        count = await forms.count()
    except Exception:  # noqa: BLE001 - no forms at all
        return None
    for index in range(count):
        form = forms.nth(index)
        if not await _is_visible(form):
            continue
        if await _first_visible_in(form, PASSWORD_SELECTORS) is not None:
            continue
        if await _first_visible_in(form, TEXT_FIELD_SELECTORS) is not None:
            return form
    return None


async def add_record(page: Any, nonce: str) -> bool:
    """Create one record through the app's own UI, so the scan owns what it attacks.

    Fills the app's create form with a nonce and submits it; the caller watches the
    traffic to learn the endpoint and payload the app itself uses. Returns False when
    no create affordance exists (the app is read-only to us), which is not an error.
    """
    form = await _blank_form(page)
    if form is None:
        if not await _click_text(page, CREATE_TEXTS):
            return False
        await _settle(page)
        form = await _blank_form(page)
        if form is None:
            return False

    filled = 0
    for field in await _all_visible_in(form, TEXT_FIELD_SELECTORS):
        try:
            if await field.input_value():
                continue
            await field.fill(nonce)
            filled += 1
        except Exception:
            log.debug("could not fill a create field", exc_info=True)
            continue
    if not filled:
        return False

    submit = 'button[type="submit"], input[type="submit"]'
    if not await _click_first(form.locator(submit)):
        submit_button = await _first_visible_in(form, ('button:not([type="button"])',))
        if submit_button is None or not await _click(submit_button):
            return False
    await asyncio.sleep(0.6)  # let the request that saves the record finish
    return True


async def provision_account(
    page: Any,
    app_url: str,
    credentials: Credentials,
    *,
    fallback: Fallback | None = None,
    create: bool = True,
    max_steps: int = 10,
) -> ProvisionResult:
    """Create (or log into) an account, returning whether a session was established."""
    fallback = fallback or NullFallback()
    await page.goto(app_url, wait_until="load")
    await dismiss_banners(page)

    detail = "heuristics"
    if create:
        state = await _open_signup(page, app_url)
        if state == "login_only":
            raise LoginOnlyError(app_url)
        if state == "none":
            raise ProvisioningError(f"no signup form found at {app_url}")
        if await _can_fill(page):
            await _fill_and_submit(page, credentials, include_name=True)
        else:
            detail = "no findable form"
    else:
        detail = "heuristics (login)"
        if not await _open_login(page, app_url):
            raise ProvisioningError(f"no login form found at {app_url}")
        if await _can_fill(page):
            await _fill_and_submit(page, credentials, include_name=False)
        else:
            detail = "no findable form"

    logged_in = await _await_session(page)

    if not logged_in and fallback.enabled:
        goal = (
            f"Sign in to this app. If you see a signup form, create an account with "
            f"email {credentials.email} and password {credentials.password}; otherwise "
            f"log in with those credentials. Fill the form and submit it. You are done "
            f"when the page shows you are signed in."
        )
        await _assist(page, fallback, goal, max_steps=max_steps)
        logged_in = await _await_session(page, timeout=1.0)
        detail += " + fallback"

    if not logged_in and create:
        await _goto(page, app_url)
        if await _open_login(page, app_url) and await _can_fill(page):
            await _fill_and_submit(page, credentials, include_name=False)
        logged_in = await _await_session(page, timeout=1.5)

    return ProvisionResult(
        email=credentials.email,
        logged_in=logged_in,
        used_fallback=fallback.enabled and "fallback" in detail,
        detail=detail,
    )
