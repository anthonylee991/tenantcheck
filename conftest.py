"""Make `verifier` (src/), `controls` and the test helpers importable without an install.

Also provides the shared `browser_page` fixture; browser-backed tests skip cleanly
when no engine is installed, so the suite still runs in CI without a browser.
"""

import pathlib
import sys

import pytest
import pytest_asyncio

_ROOT = pathlib.Path(__file__).parent
for _p in (str(_ROOT / "src"), str(_ROOT), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from verifier.browser import BrowserConfig, BrowserSession, BrowserUnavailable


@pytest_asyncio.fixture
async def browser_session() -> object:
    try:
        session = BrowserSession(BrowserConfig())
        await session.__aenter__()
    except BrowserUnavailable as exc:  # pragma: no cover - depends on install
        pytest.skip(f"browser not installed: {exc}")
    try:
        yield session
    finally:
        await session.__aexit__(None, None, None)


@pytest_asyncio.fixture
async def browser_page(browser_session: object) -> object:
    return await browser_session.new_page()
