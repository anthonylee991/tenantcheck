"""Browser driver: act as a real user on any hosted app.

The moat. Stage 1 probes over HTTP/PostgREST; this is the layer that lets the
verifier drive an unfamiliar UI the way a person would, so it works on apps with no
discoverable API.

Design notes (learned on the VPS, see docs/BROWSER_DRIVER.md):

- **Portability is config, not code.** The same driver runs on a Windows workstation
  and on a headless Linux VPS. Everything that differs (headless, display, sandbox,
  channel/executable) is a `BrowserConfig` field, overridable by `TENANTCHECK_BROWSER_*`
  env vars, so no deployment edits code.
- **Minimal fingerprint.** No user-agent override (it desyncs TLS/UA), and as few
  Chromium flags as possible — lessons from an earlier in-house browser stack.
- **Browser deps are optional.** `patchright` (preferred) or `playwright` are imported
  lazily; the core verifier still installs and its tests still pass without a browser.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Self

log = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}


class BrowserUnavailable(RuntimeError):
    """Raised when no browser engine is installed (or a bad engine was named)."""


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in _TRUE


def _sandbox_failure(exc: BaseException) -> bool:
    """True when Chromium refused to start because no usable sandbox exists.

    Ubuntu 23.10+ ships an AppArmor policy that blocks the unprivileged user
    namespaces Chromium's sandbox needs (Docker images and GitHub runners hit this).
    The failure is only knowable at launch time, so the session retries without it.
    """
    text = str(exc).lower()
    return "sandbox" in text and ("no usable sandbox" in text or "sandboxing failed" in text)


def _default_no_sandbox() -> bool:
    """Containers/CI usually run as root, where Chrome needs --no-sandbox.

    Auto-on for a POSIX root process; never on Windows.
    """
    if os.name == "nt":
        return False
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid) and geteuid() == 0


@dataclass(frozen=True)
class BrowserConfig:
    engine: str = "patchright"
    headless: bool = True
    viewport_width: int = 1280
    viewport_height: int = 800
    locale: str = "en-US"
    timezone: str = "America/New_York"
    timeout_ms: int = 30_000
    slow_mo_ms: int = 0
    user_data_dir: str | None = None
    channel: str | None = None
    executable_path: str | None = None
    no_sandbox: bool = False
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    proxy_server: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> BrowserConfig:
        env = os.environ if env is None else env
        viewport = env.get("TENANTCHECK_BROWSER_VIEWPORT", "1280x800")
        try:
            width, height = (int(part) for part in viewport.lower().split("x", 1))
        except ValueError:
            width, height = 1280, 800
        raw_args = env.get("TENANTCHECK_BROWSER_ARGS", "")
        extra = tuple(a for a in raw_args.replace(",", " ").split() if a)
        return cls(
            engine=env.get("TENANTCHECK_BROWSER_ENGINE", "patchright").strip().lower(),
            headless=_as_bool(env.get("TENANTCHECK_BROWSER_HEADLESS"), True),
            viewport_width=width,
            viewport_height=height,
            locale=env.get("TENANTCHECK_BROWSER_LOCALE", "en-US"),
            timezone=env.get("TENANTCHECK_BROWSER_TIMEZONE", "America/New_York"),
            timeout_ms=int(env.get("TENANTCHECK_BROWSER_TIMEOUT_MS", "30000")),
            slow_mo_ms=int(env.get("TENANTCHECK_BROWSER_SLOW_MO_MS", "0")),
            user_data_dir=env.get("TENANTCHECK_BROWSER_USER_DATA_DIR") or None,
            channel=env.get("TENANTCHECK_BROWSER_CHANNEL") or None,
            executable_path=env.get("TENANTCHECK_BROWSER_EXECUTABLE_PATH") or None,
            no_sandbox=_as_bool(env.get("TENANTCHECK_BROWSER_NO_SANDBOX"), _default_no_sandbox()),
            extra_args=extra,
            proxy_server=env.get("TENANTCHECK_BROWSER_PROXY") or None,
        )

    @property
    def headful(self) -> bool:
        return not self.headless

    @property
    def requires_display(self) -> bool:
        """Headful on Linux needs a display (Xvfb on a VPS); Windows has one."""
        return self.headful and os.name != "nt"

    def launch_args(self) -> list[str]:
        args = ["--disable-dev-shm-usage"]
        if self.no_sandbox:
            args.append("--no-sandbox")
        args.extend(self.extra_args)
        return args

    def launch_kwargs(self) -> dict:
        kwargs: dict = {
            "headless": self.headless,
            "args": self.launch_args(),
            "chromium_sandbox": not self.no_sandbox,
        }
        if self.slow_mo_ms:
            kwargs["slow_mo"] = self.slow_mo_ms
        if self.executable_path:
            kwargs["executable_path"] = self.executable_path
        if self.channel:
            kwargs["channel"] = self.channel
        if self.proxy_server:
            kwargs["proxy"] = {"server": self.proxy_server}
        return kwargs

    def context_kwargs(self) -> dict:
        return {
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            "locale": self.locale,
            "timezone_id": self.timezone,
        }

    def display_hint(self) -> str:
        if not self.requires_display:
            return ""
        return (
            "Headful browsing on Linux needs a display. On a VPS run:\n"
            "  Xvfb :99 -screen 0 1920x1080x24 &\n"
            "  export DISPLAY=:99\n"
            "or set TENANTCHECK_BROWSER_HEADLESS=true for the headless path."
        )


def load_engine(engine: str):
    """Return the async_playwright entrypoint for the configured engine."""
    if engine == "patchright":
        try:
            from patchright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on install
            raise BrowserUnavailable(
                "patchright is not installed; run: pip install 'tenantcheck[browser]' "
                "and: patchright install chromium"
            ) from exc
        return async_playwright
    if engine == "playwright":
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            raise BrowserUnavailable(
                "playwright is not installed; run: pip install 'tenantcheck[browser]' "
                "and: playwright install chromium"
            ) from exc
        return async_playwright
    raise BrowserUnavailable(f"unknown browser engine: {engine!r} (use 'patchright' or 'playwright')")


class BrowserSession:
    """Async context manager owning a Playwright driver + browser + context.

        async with BrowserSession() as session:
            page = await session.new_page()
            await page.goto("https://example.com")
    """

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self.config = config or BrowserConfig.from_env()
        self._playwright = None
        self._browser = None
        self._context = None

    async def __aenter__(self) -> Self:
        async_playwright = load_engine(self.config.engine)
        self._playwright = await async_playwright().start()
        chromium = self._playwright.chromium
        try:
            await self._launch(chromium)
        except Exception as exc:
            if self.config.no_sandbox or not _sandbox_failure(exc):
                raise
            log.warning(
                "Chromium could not start its sandbox (%s); retrying without it. "
                "Set TENANTCHECK_BROWSER_NO_SANDBOX=1 to make this explicit.",
                exc,
            )
            self.config = replace(self.config, no_sandbox=True)
            await self._launch(chromium)
        self._context.set_default_timeout(self.config.timeout_ms)
        return self

    async def _launch(self, chromium: Any) -> None:
        if self.config.user_data_dir:
            self._context = await chromium.launch_persistent_context(
                self.config.user_data_dir,
                **self.config.launch_kwargs(),
                **self.config.context_kwargs(),
            )
        else:
            self._browser = await chromium.launch(**self.config.launch_kwargs())
            self._context = await self._browser.new_context(**self.config.context_kwargs())


    async def new_page(self) -> Any:
        if self._context is None:
            raise BrowserUnavailable("session is not started; use 'async with'")
        return await self._context.new_page()

    async def new_context(self, **kwargs: Any) -> Any:
        """A fresh, isolated browser context (its own cookies).

        Used to hold independent accounts side by side. Requires a launched browser;
        a persistent profile (`user_data_dir`) owns a single context by design.
        """
        if self._browser is None:
            raise BrowserUnavailable(
                "isolated per-account contexts need a launched browser, not a "
                "persistent user_data_dir profile"
            )
        context = await self._browser.new_context(**{**self.config.context_kwargs(), **kwargs})
        context.set_default_timeout(self.config.timeout_ms)
        return context

    async def __aexit__(self, *exc: object) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                await _quietly(closer.close())
        if self._playwright is not None:
            await _quietly(self._playwright.stop())
        self._context = self._browser = self._playwright = None


async def _quietly(awaitable: Any) -> None:
    try:
        await awaitable
    except Exception:
        log.debug("browser teardown failed", exc_info=True)
