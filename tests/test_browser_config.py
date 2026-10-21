"""Browser driver config: one driver, two deployments (Windows workstation, Linux VPS)."""

from __future__ import annotations

import pytest

from verifier.browser import (
    BrowserConfig,
    BrowserSession,
    BrowserUnavailable,
    _sandbox_failure,
    load_engine,
)


def test_defaults_are_headless_and_dependency_free() -> None:
    config = BrowserConfig.from_env({})
    assert config.engine == "patchright"
    assert config.headless is True
    assert config.requires_display is False
    assert config.launch_args() == ["--disable-dev-shm-usage"]


def test_env_overrides() -> None:
    config = BrowserConfig.from_env(
        {
            "TENANTCHECK_BROWSER_ENGINE": "playwright",
            "TENANTCHECK_BROWSER_HEADLESS": "false",
            "TENANTCHECK_BROWSER_VIEWPORT": "1920x1080",
            "TENANTCHECK_BROWSER_NO_SANDBOX": "1",
            "TENANTCHECK_BROWSER_CHANNEL": "chrome",
            "TENANTCHECK_BROWSER_ARGS": "--foo --bar,baz",
        }
    )
    assert config.engine == "playwright"
    assert config.headless is False
    assert (config.viewport_width, config.viewport_height) == (1920, 1080)
    assert config.channel == "chrome"
    assert config.extra_args == ("--foo", "--bar", "baz")
    assert "--no-sandbox" in config.launch_args()
    assert config.launch_kwargs()["chromium_sandbox"] is False
    assert config.context_kwargs()["viewport"] == {"width": 1920, "height": 1080}


def test_bad_viewport_falls_back() -> None:
    config = BrowserConfig.from_env({"TENANTCHECK_BROWSER_VIEWPORT": "not-a-size"})
    assert (config.viewport_width, config.viewport_height) == (1280, 800)


def test_headful_on_linux_requires_a_display() -> None:
    headful = BrowserConfig(headless=False)
    if headful.requires_display:  # Linux / VPS
        assert "Xvfb" in headful.display_hint()
    else:  # Windows workstation - a display is always present
        assert headful.display_hint() == ""


def test_unknown_engine_is_a_clear_error() -> None:
    with pytest.raises(BrowserUnavailable):
        load_engine("internet-explorer")


def test_a_sandbox_refusal_is_recognised_but_a_network_error_is_not() -> None:
    assert _sandbox_failure(RuntimeError("Chromium sandboxing failed! No usable sandbox!"))
    assert not _sandbox_failure(RuntimeError("net::ERR_CONNECTION_REFUSED"))


class _FakeContext:
    def set_default_timeout(self, _timeout: int) -> None:
        pass

    async def close(self) -> None:
        pass


class _FakeBrowser:
    async def new_context(self, **_kwargs: object) -> _FakeContext:
        return _FakeContext()

    async def close(self) -> None:
        pass


class _FakeChromium:
    """Fails the first launch like a runner without user namespaces, then succeeds."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def launch(self, **kwargs: object) -> _FakeBrowser:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("Chromium sandboxing failed! No usable sandbox!")
        return _FakeBrowser()


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()

    async def stop(self) -> None:
        pass


class _FakeStarter:
    def __init__(self) -> None:
        self.playwright = _FakePlaywright()

    async def start(self) -> _FakePlaywright:
        return self.playwright


async def test_a_missing_sandbox_retries_once_without_it(monkeypatch) -> None:
    starter = _FakeStarter()
    monkeypatch.setattr("verifier.browser.load_engine", lambda _engine: lambda: starter)

    async with BrowserSession(BrowserConfig()) as session:
        assert session._context is not None

    first, second = starter.playwright.chromium.calls
    assert first["chromium_sandbox"] is True
    assert second["chromium_sandbox"] is False
    assert "--no-sandbox" in second["args"]


async def test_an_explicit_no_sandbox_is_not_retried(monkeypatch) -> None:
    starter = _FakeStarter()
    monkeypatch.setattr("verifier.browser.load_engine", lambda _engine: lambda: starter)

    with pytest.raises(RuntimeError, match="sandboxing failed"):
        async with BrowserSession(BrowserConfig(no_sandbox=True)):
            pass
