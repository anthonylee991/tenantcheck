"""UI provisioning (B2): heuristics create an account; the fallback covers unfamiliar forms.

Browser-backed, so these skip cleanly when no engine is installed (CI portability).
"""

from __future__ import annotations

from harness import serve

from controls.webapp import make_webapp
from verifier.ui import Credentials, provision_account
from verifier.vision import Action, ScriptedFallback


async def test_heuristics_create_an_account(browser_page: object) -> None:
    credentials = Credentials("alice@example.com", "hunter2pw")
    with serve(make_webapp()) as base:
        result = await provision_account(browser_page, base, credentials)
    assert result.logged_in is True
    assert result.used_fallback is False
    assert result.detail == "heuristics"


async def test_fallback_handles_an_unfamiliar_form(browser_page: object) -> None:
    fallback = ScriptedFallback(
        [
            Action("type", target="example.com", text="bob@example.com"),
            Action("type", target="Password", text="hunter2pw"),
            Action("click", target="Create account"),
            Action("done"),
        ]
    )
    with serve(make_webapp(obfuscated=True)) as base:
        result = await provision_account(
            browser_page, base, Credentials("bob@example.com", "hunter2pw"), fallback=fallback
        )
    assert result.logged_in is True
    assert result.used_fallback is True
    assert fallback.calls > 0


async def test_default_fallback_spends_nothing_and_leaves_no_session(browser_page: object) -> None:
    with serve(make_webapp(obfuscated=True)) as base:
        result = await provision_account(
            browser_page, base, Credentials("carol@example.com", "hunter2pw")
        )
    assert result.logged_in is False
    assert result.used_fallback is False
    assert result.detail == "no findable form"


async def test_provisioning_can_log_a_returning_user_in(browser_page: object) -> None:
    credentials = Credentials("dave@example.com", "hunter2pw")
    app = make_webapp()
    with serve(app) as base:
        created = await provision_account(browser_page, base, credentials)
        assert created.logged_in is True
        await browser_page.context.clear_cookies()
        again = await provision_account(browser_page, base, credentials, create=False)
    assert again.logged_in is True
