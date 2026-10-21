# Browser driver

The moat. Stage 1 probes a Supabase backend over HTTP/PostgREST; the browser driver
is how the verifier acts as a **real logged-in user** on an app with no discoverable
API â€” driving signup, navigation, and forms the way a person would.

This document is the scoping + operations reference. The code is
`src/verifier/browser.py`; browser dependencies are optional (`verifier[browser]`).

---

## 1. Why not reuse a big browser-pool library

A prior in-house browser pool had already solved the hard part (persistent contexts,
proxy stickiness, zombie-Chrome cleanup). We **borrow its lessons**, but we do not
import it:

| Why borrow the lessons | Why not import the module |
|---|---|
| No user-agent override (desyncs TLS/UA, which triggers bot loops) | It imports `asyncpg`, a captcha service, `psutil`, and a settings layer that wants a `.env` + Postgres |
| Minimal Chromium flags (only `--disable-dev-shm-usage` [+ images]) | It is coupled to accounts, proxy sessions and a database |
| Persistent context via `launch_persistent_context` when an extension is needed | tenantcheck must stay installable by its (non-developer) audience |
| Zombie-browser / profile-lock cleanup | Different job (bulk scraping vs driving one app) |

So tenantcheck owns a small, config-driven driver and copies the **decisions**, not
the code.

---

## 2. Portability: one driver, two deployments

The same `BrowserSession` runs on a Windows workstation and a headless Linux VPS.
Everything that differs is a `BrowserConfig` field, all overridable by env vars â€” no
deployment edits code.

| Concern | Windows workstation (local dev) | Linux VPS (where it runs for real) |
|---|---|---|
| Display | real desktop; headful works directly | **Xvfb** `:99` + `export DISPLAY=:99` if headful |
| Headless | ok | ok â€” default; avoids the display entirely |
| Chrome sandbox | n/a | root/CI needs `--no-sandbox` (auto-on for POSIX root; set `TENANTCHECK_BROWSER_NO_SANDBOX=1` where user namespaces are blocked, e.g. Docker or GitHub runners) |
| Channel / binary | Playwright's bundled Chromium | same, or `channel=chrome` / `executable_path` |
| Python path | repo root | `PYTHONPATH=â€¦/src:â€¦` |

### Environment variables (`BrowserConfig.from_env`)

| Var | Default | Notes |
|---|---|---|
| `TENANTCHECK_BROWSER_ENGINE` | `patchright` | `patchright` (preferred) or `playwright` |
| `TENANTCHECK_BROWSER_HEADLESS` | `true` | `false` needs a display on Linux (see below) |
| `TENANTCHECK_BROWSER_VIEWPORT` | `1280x800` | `WIDTHxHEIGHT`; bad value â†’ default |
| `TENANTCHECK_BROWSER_NO_SANDBOX` | rootâ†’`true` | forces `--no-sandbox` + `chromium_sandbox=false` |
| `TENANTCHECK_BROWSER_CHANNEL` | *(none)* | e.g. `chrome` (use the system Chrome) |
| `TENANTCHECK_BROWSER_EXECUTABLE_PATH` | *(none)* | explicit Chrome binary |
| `TENANTCHECK_BROWSER_USER_DATA_DIR` | *(none)* | set â†’ persistent context (keeps login state) |
| `TENANTCHECK_BROWSER_TIMEOUT_MS` | `30000` | default action timeout |
| `TENANTCHECK_BROWSER_SLOW_MO_MS` | `0` | slow actions for debugging |
| `TENANTCHECK_BROWSER_LOCALE` / `_TIMEZONE` | `en-US` / `America/New_York` | context hints |
| `TENANTCHECK_BROWSER_ARGS` | *(none)* | extra flags, space/comma separated |
| `TENANTCHECK_BROWSER_PROXY` | *(none)* | `http://host:port` |
| `TENANTCHECK_BROWSER_WRITE` | `1` | `0` keeps scans read-only (no create, no write probe) |

### VPS recipe (headful, if a flow blocks the headless path)

```bash
pgrep Xvfb >/dev/null || (Xvfb :99 -screen 0 1920x1080x24 &)
export DISPLAY=:99
export TENANTCHECK_BROWSER_HEADLESS=false
export TENANTCHECK_BROWSER_NO_SANDBOX=true
# Chrome sandbox on Ubuntu 23.10+ (persist in /etc/sysctl.d/60-chrome-userns.conf):
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
```

If `DISPLAY` is unset while headful on Linux, `BrowserConfig.display_hint()` returns
the exact fix, so the failure is self-explaining.

### Install

```bash
pip install "tenantcheck[browser]"
patchright install chromium      # or: playwright install chromium
```

---

## 3. What is built (B1 + B2)

`BrowserConfig` (pure, fully testable without a browser) + `BrowserSession`
(lifecycle: driver â†’ browser â†’ context â†’ page), with the launch/context kwargs
derived from config and the anti-fingerprint defaults learned from an earlier
in-house browser stack.

```python
from verifier.browser import BrowserConfig, BrowserSession

config = BrowserConfig.from_env()
async with BrowserSession(config) as session:
    page = await session.new_page()
    await page.goto("https://my-app.example")
```

The config layer is measured by `tests/test_browser_config.py` (defaults, env
overrides, sandbox arg, display detection, clear error for a bad engine) â€” no
browser process required, so it runs in CI.

**B2 â€” UI account provisioning** (`src/verifier/ui.py`) drives a real signup/login
form to a signed-in session:

```python
from verifier.ui import Credentials, provision_account

result = await provision_account(page, "https://my-app.example",
                                 Credentials("test+1@example.com", "..."))
assert result.logged_in
```

Conventional forms are handled deterministically; an unfamiliar form falls through
to the optional, off-by-default vision fallback (`src/verifier/vision.py`). Measured
by `bench/run_ui.py` (3/3 scenarios) and `tests/test_ui.py`.

**B3 â€” resource discovery from traffic** (`src/verifier/network.py`) learns the app's
data endpoints by watching the browser's own XHR/fetch calls:

```python
from verifier.network import capture, candidates

calls = await capture(page, lambda: page.goto("https://my-app.example/dashboard"))
resources = candidates(calls)   # collection + record endpoints, noise dropped
```

`candidates()` is pure and unit-tested; `capture()` needs a page. Measured by
`bench/run_network.py` (2/2: collection + record) and `tests/test_network.py`.

**B4 â€” the browser target** (`src/verifier/browser_target.py`) plugs the above into
the **unchanged** isolation prober via the `Target` seam:

```python
from verifier.browser_target import BrowserClient, browser_target
from verifier.scan import scan
from verifier.ui import Credentials

browser = BrowserClient(session, "https://my-app.example")           # self-signup
browser = BrowserClient(session, "https://my-app.example", accounts=[  # login-only
    Credentials("owner@example.com", "..."),
    Credentials("other@example.com", "..."),
])
result = await scan(browser, browser_target(browser))  # same prober, no API needed
await browser.aclose()
```

`BrowserClient` is `Client`-shaped; `token` names an isolated browser context (its own
cookies), `token=None` is anonymous. A session stored as a bearer token in
`localStorage` is read once per account and replayed as `Authorization: Bearer â€¦`, so
token-auth SPAs work without app-specific code. When the dashboard opens a record on
a detail page, record paths are derived from an id in the owner's own collection
payload. For **writes**, `add_record` fills the app's own create form with a nonce and
submits it; the POST the app makes tells the scanner the endpoint and the field to
reuse, and only that scanner-owned record is ever mutated. No create form means a
read-only scan (not a failure). Measured by `bench/run_browser_target.py` (recall 1.00
reads *and* writes, 0 false positives) and `tests/test_browser_target.py`.

**Wired into the product door.** `src/verifier/browser_door.py` exposes
`scan_browser(url)`, and `scan_url(..., browser_scan=scan_browser)` uses it when the
frontend ships no Supabase config. Opt-in: `tenantcheck scan <url> --browser`, or
`TENANTCHECK_BROWSER_SCAN=1` for the submission API. Measured by
`bench/run_url_browser.py` (recall 1.00, 0 false positives, ~5.2s/URL) and
`tests/test_url_browser.py`.

### What a real app changed

A local FastAPI + Vite app (bearer token in `localStorage`, `/login` with a "Sign up"
nav link, a list-only dashboard) exposed five things the fixtures did not. Each is
now covered by a test:

| What happened | Fix |
|---|---|
| `test+1@example.test` â†’ HTTP 422: strict validators reserve `.test` | generated accounts use `@example.com` (`TENANTCHECK_ACCOUNT_DOMAIN` to override) |
| No cookies; session was `localStorage['...token']` | read a token-ish storage key and replay it as `Authorization` |
| The submit step matched the text "Sign up" and followed a *nav link* to `/signup` | submit the password field's own `<form>` button first; text match is the fallback |
| Dashboard only calls `GET /personas`; records live at `/personas/{id}` | derive child paths from ids in the owner's listing |
| Unknown `/api/...` paths returned `200` with an HTML body (SPA catch-all), inventing a record | derived/observed records must answer **JSON** with a marker that is not just the id |

The same app, scanned with two supplied accounts: owner `200`, the other account
`404`, anonymous `401` - reported as one record checked, no problems found. That is
a field observation from one manual run, not a benchmark row; the measured numbers
stay in `bench/RESULTS.md`.

---

## 4. Phased plan

| Phase | Deliverable | Status |
|---|---|---|
| **B1** | Session lifecycle + env-driven config + portability | **built** (`browser.py`) |
| **B2** | **Provision two accounts** through the UI (heuristic signup/login with a vision fallback for unfamiliar forms); durable session via `user_data_dir` | **built** (`ui.py` + `vision.py`) |
| **B3** | **Resource discovery** from observed network traffic (XHR/fetch â†’ candidate record URLs), the browser equivalent of the PostgREST enumeration | **built** (`network.py`) |
| **B4** | **`browser_target`** implementing the existing `Target` seam, so the *unchanged* isolation prober runs over the browser | **built** (`browser_target.py`) |

B4 is the payoff: the prober proved against the control twin, Juice Shop, and real
Supabase is generic over `Target`; a browser-backed `Target` makes the same checks
work on apps with no API.

---

## 5. Safety (unchanged from the SPEC)

- **Authorized targets only** â€” the browser path sits behind the same ownership gate.
- **A client, not an executor** â€” it drives the public UI; it never executes the app's code.
- **Polite** â€” throttle, and never attack availability or integrity of real user data.
- **No exfiltration** â€” prove access with a minimal marker; destroy test accounts after a run.
