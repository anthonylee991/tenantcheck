"""tenantcheck CLI — `tenantcheck scan <url>`."""

from __future__ import annotations

import asyncio
import sys

import httpx
import typer
from rich import print as rprint
from rich.markup import escape

from .browser import BrowserUnavailable
from .browser_door import scan_browser
from .ownership import OwnershipError, verify_ownership
from .report import render
from .ui import Credentials, LoginOnlyError, ProvisioningError
from .url_scan import scan_url

app = typer.Typer(help="tenantcheck - black-box verification for deployed apps.", add_completion=False)

ACCOUNT_OPTION = typer.Option(
    None,
    "--account",
    help="user:pass for a private app; pass it twice (two accounts). Implies --browser.",
)


@app.callback()
def main() -> None:
    """Black-box verification for deployed apps."""
    # Non-dev users are on default Windows consoles (cp1252); never crash on output.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


@app.command()
def scan(
    url: str,
    browser: bool = typer.Option(
        False, "--browser", help="If the app isn't Supabase, scan it through the browser UI."
    ),
    account: list[str] | None = ACCOUNT_OPTION,
) -> None:
    """Verify a deployed app by URL (Stage 1: cross-account data exposure)."""
    try:
        accounts = _parse_accounts(account)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    use_browser = browser or accounts is not None
    raise SystemExit(asyncio.run(_scan(url, browser=use_browser, accounts=accounts)))


def _parse_accounts(raw: list[str] | None) -> list[Credentials] | None:
    if not raw:
        return None
    accounts: list[Credentials] = []
    for entry in raw:
        user, separator, password = entry.partition(":")
        if not separator or not user:
            raise ValueError(f"expected user:password, got {entry!r}")
        accounts.append(Credentials(user, password))
    if len(accounts) != 2:
        raise ValueError("give exactly two --account values (the owner and a second account)")
    return accounts


@app.command("verify-domain")
def verify_domain(url: str) -> None:
    """Check whether you own a domain (required before scanning)."""
    raise SystemExit(asyncio.run(_verify_domain(url)))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the submission API: paste a URL, get a report (no terminal for users)."""
    import uvicorn

    from .api import app_factory

    uvicorn.run(app_factory(), host=host, port=port)


async def _verify_domain(url: str) -> int:
    try:
        result = await verify_ownership(url)
    except httpx.HTTPError as exc:
        rprint(f"[red]Could not reach {url}.[/red] {exc}")
        return 3
    if result.verified:
        rprint(f"[green]Ownership proven[/green] via {result.method}.")
        return 0
    rprint(escape(result.instructions()))
    return 1


async def _scan(
    url: str,
    *,
    browser: bool = False,
    accounts: list[Credentials] | None = None,
) -> int:
    browser_scan = (
        (lambda target: scan_browser(target, accounts=accounts)) if browser else None
    )
    try:
        found = await scan_url(
            url,
            require_ownership=True,
            browser_scan=browser_scan,
        )
    except OwnershipError as exc:
        rprint(escape(exc.result.instructions()))
        return 4
    except LoginOnlyError:
        rprint(f"[yellow]This app is login-only (no signup).[/yellow] {escape(url)}")
        rprint(
            "  Re-run with two existing accounts: "
            f"tenantcheck scan {escape(url)} --account user1:pass --account user2:pass"
        )
        return 5
    except ProvisioningError as exc:
        rprint(f"[yellow]Could not create a test account.[/yellow] {exc}")
        return 5
    except BrowserUnavailable as exc:
        rprint(f"[yellow]Browser scanning is unavailable.[/yellow] {exc}")
        return 5
    except httpx.HTTPError as exc:
        rprint(f"[red]Could not reach {url}.[/red] {exc}")
        return 3
    if found is None:
        hint = (
            "This app does not appear to be Supabase-backed."
            if browser
            else "Try --browser to scan apps with no discoverable API."
        )
        rprint(
            "[yellow]No supported stack found.[/yellow] "
            "Stage 1 reads the Supabase config a deployed frontend ships; " + hint
        )
        return 2
    rprint(escape(render(found.result)))
    return 0


if __name__ == "__main__":
    app()
