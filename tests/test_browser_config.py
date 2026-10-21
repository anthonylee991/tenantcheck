"""Browser driver config: one driver, two deployments (Windows workstation, Linux VPS)."""

from __future__ import annotations

import pytest

from verifier.browser import BrowserConfig, BrowserUnavailable, load_engine


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
