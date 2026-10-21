# tenantcheck

Black-box verification for deployed apps — for people who build with AI and don't read code.

[![ci](https://github.com/anthonylee991/tenantcheck/actions/workflows/ci.yml/badge.svg)](https://github.com/anthonylee991/tenantcheck/actions/workflows/ci.yml)

Give it a deployed URL. It creates two accounts, finds a record one of them owns, then
checks whether the other account — or a stranger with no login at all — can read or
change it. When they can, you get the evidence and a copy-paste fix prompt.

## Install

```bash
pip install tenantcheck                  # core: control apps + submission API
pip install "tenantcheck[browser]"       # + drive real UIs (signup, login, discovery, writes)
patchright install chromium              # browser engine for the extra
```

## Quickstart

```bash
tenantcheck verify-domain https://your-app.example   # prints the token to publish
tenantcheck scan https://your-app.example            # hosted app (ownership checked first)
tenantcheck scan http://localhost:3000 --browser     # local app: no token needed
```

A scan takes seconds. `tenantcheck scan --help` lists the rest.

## What it checks

- **Cross-account read** — does account B get a record account A owns?
- **Cross-account write** — can B change it? Browser mode creates one record of its
  own and only ever mutates that record; your data is never touched.
- **Anonymous access** to both.

It works on three app shapes behind one `Target` seam: a discoverable Supabase backend
(the audience's dominant bug: missing Row Level Security), an arbitrary app driven
through its own UI, and the local control twins used as bench ground truth.

## What it does not check

Authentication strength, injection, XSS, CSRF, infrastructure, or availability. It is
one check done well, not a penetration test. See [Limitations](#limitations).

See [`SPEC.md`](SPEC.md) for what it is, the goals, the approach, the evaluation, and the definition of done.

## Status

**Stage 1 (isolation), vertical slice.** One check, measured end to end:
- provision two independent accounts,
- discover a record owned by one,
- replay the fetch *and a benign mutation* as the other account and anonymously,
- report cross-account read/write with evidence + a copy-paste fix prompt.

Works against three targets (same prober, different `Target` shape): the control
twin, **OWASP Juice Shop**, and a real **Supabase** project (missing Row Level
Security — the audience's dominant bug).

The product door is `tenantcheck scan <url>`: it reads the deployed frontend as a
client, finds the Supabase URL + public key the app ships to every browser, then
runs the scan. No repo, no agent, no config.

**Stage 2 (behaviour / regression), vertical slice.** Fingerprint each flow
(status + response *shape*) on a known-good state, replay, diff, and flag a
previously-working flow that silently broke or changed. Measured against the
`good`/`regressed_*` control twins (`bench/run_regression.py`).

**Stage 2 memory.** A `BaselineStore` remembers the last known-good fingerprint
per app and advances it **only when nothing regressed** — so a regression keeps
being reported until it's fixed, and the last green version is never forgotten.
The store is plain versioned JSON on disk (swappable later); the product door
uses it automatically, keyed by the submitted URL. Demonstrated as a four-run
timeline in `bench/run_baseline.py`.

**Ownership gate (the consent boundary).** The product door refuses to scan until
the submitter proves they control the app's domain — via a DNS TXT record, a
`/.well-known/tenantcheck-verification.txt` file, or a `<meta>` tag on the page. The
token is derived from the host and shown to the user; publishing it *is* the
proof. `tenantcheck scan <url>` enforces this (`tenantcheck verify-domain <url>` checks
it alone). Measured with **0 false accepts** (`bench/run_ownership.py`).

**Local apps & bring-your-own-accounts.** An app on this machine needs no token:
loopback (`localhost`, `127.0.0.1`, `::1`) is treated as inherently owned, so
`tenantcheck scan http://localhost:3000 --browser` just works. The hosted API refuses
private targets (SSRF guard). Apps that are **login-only** (no signup) are scanned
with two existing accounts supplied at run time: `--account user1:pass --account
user2:pass` (implies `--browser`). Credentials are used in memory only, never
stored or logged; a login-only app without them gets a clear message telling you
what to supply. Scan-generated accounts use `@example.com` (reserved for docs);
override the domain with `TENANTCHECK_ACCOUNT_DOMAIN` if an app rejects it. Measured
against a loopback twin with two supplied accounts (`bench/run_local.py`).

The browser-driving capability that lets it act as a real user on any hosted app
(the moat), in two steps. **B1** is the session lifecycle + env-driven portability
(`src/verifier/browser.py`, `verifier[browser]`). **B2** is UI account
provisioning (`src/verifier/ui.py`): point it at an app URL and credentials and it
drives the signup/login form the way a person would — find the fields, fill, submit,
confirm a signed-in session. Conventional forms are handled deterministically; an
unfamiliar form falls through to an **optional** vision fallback
(`src/verifier/vision.py`, off by default, hard-capped — see
[`docs/VISION_FALLBACK.md`](docs/VISION_FALLBACK.md)). **B3** learns the app's data
endpoints by watching the browser's own traffic (`src/verifier/network.py`): a
collection call and a record call become the replayable candidates. **B4** turns those
into a browser-backed `Target` (`src/verifier/browser_target.py`): two isolated browser
contexts sign up through the UI, discovery reads the owner's record over the browser,
and the **unchanged** isolation prober replays it as the other account. That makes the
same checks work on an app with no discoverable API. If the app keeps its session in
`localStorage` (a bearer token rather than a cookie), the scanner reads it and replays
it on the requests it makes as that account. If the dashboard only lists a collection
and opens records on a detail page, the scanner takes an id from the owner's own
listing and fetches the child path. To test **writes** without touching your data,
the scan creates one record of its own through the app's create form and tries to
change that as the other account (`TENANTCHECK_BROWSER_WRITE=0` keeps it read-only; an
app with no create form is simply scanned read-only). The product door uses it as a
fallback when no Supabase config is found: `tenantcheck scan <url> --browser`, or
`TENANTCHECK_BROWSER_SCAN=1 verifier serve` for the API. See
[`docs/BROWSER_DRIVER.md`](docs/BROWSER_DRIVER.md).

**Submission API (the surface users touch).** `tenantcheck serve` runs a FastAPI
service so a non-developer can paste a URL and get a report without a terminal.
`POST /api/submissions {url}` runs ownership → scan and returns the report; if
ownership isn't proven it returns `ownership_required` with the exact token to
publish, and `POST /api/submissions/{id}/recheck` tries again. State (submissions,
baselines) persists under `TENANTCHECK_STATE_DIR` (default `~/.tenantcheck`). Measured
via `bench/run_api.py`.

When no record can be reached at all, the report says **"Nothing to test"** rather
than "No problems found": a scan that proved nothing must not look like a pass.

## Layout

```
src/verifier/       the product:
                      models/http/accounts/discovery/isolation/scan/report  (core + control shape)
                      target.py            the Target seam
                      juice_shop.py        OWASP Juice Shop shape
                      supabase.py          Supabase config extraction + RLS shape
                      frontend.py          crawl a deployed frontend for backend config
                      url_scan.py          the URL-in door
                      ownership.py         domain-ownership proof (consent gate)
                      browser.py           browser driver (B1: session + config)
                      ui.py                UI provisioning (B2: drive a signup/login form)
                      vision.py            optional, off-by-default vision fallback
                      network.py           traffic-based resource discovery (B3)
                      browser_target.py    browser-backed Target (B4: unchanged prober over the UI)
                      browser_door.py      the URL door's browser fallback
                      behavior.py          Stage 2 baseline store + regression diff
                      store.py             submission state (SQLite)
                      api.py               submission API (`tenantcheck serve`)
                      cli.py               `tenantcheck scan <url>`
controls/           reference apps used as bench ground truth (vuln / secure twins, fixture frontend, HTML web app)
bench/              the harness + RESULTS.md (every claim traces here)
tests/              isolation, url-in and config-extraction tests
```

## Run

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"     # Windows
pytest
python bench/run.py                        # internal control (vuln vs secure twin)
python bench/run_juice_shop.py             # external app (needs a running Juice Shop)
python bench/run_supabase.py               # external Supabase/RLS (needs a running Supabase)
python bench/run_url.py                    # URL-in flow (fixture frontend → real Supabase)
python bench/run_regression.py             # Stage 2 regression (good vs regressed twins)
python bench/run_baseline.py               # Stage 2 store (4-run timeline, advances only when green)
python bench/run_ownership.py              # ownership gate (real proofs vs non-proofs)
python bench/run_api.py                    # submission API two-step flow
python bench/run_browser.py                # browser driver (needs tenantcheck[browser])
python bench/run_ui.py                     # UI provisioning, heuristics + fallback (needs tenantcheck[browser])
python bench/run_network.py                # traffic-based resource discovery (needs tenantcheck[browser])
python bench/run_browser_target.py         # browser target: unchanged prober over the UI (needs tenantcheck[browser])
python bench/run_url_browser.py            # URL door → browser fallback, non-Supabase app (needs tenantcheck[browser])
python bench/run_local.py                  # loopback + BYOA: local app, two supplied accounts (needs tenantcheck[browser])
```

Bring up the external app control:

```bash
docker run -d --rm -p 3011:3000 bkimminich/juice-shop
```

Bring up the Supabase control (throwaway project, remapped ports), then seed it:

```bash
npx --yes supabase@latest start
docker exec -i supabase_db_<project> psql -U postgres -d postgres < bench/seed_supabase.sql
```

Scan a real app — paste the URL, no repo or config (ownership is checked first):

```bash
tenantcheck verify-domain https://your-app.example   # prints the token to publish
tenantcheck scan https://your-app.example
tenantcheck scan https://your-app.example --browser   # any app, not just Supabase
tenantcheck scan http://localhost:3000 --browser      # local app: no token needed
tenantcheck scan https://private.example --account me@x.com:pw1 --account other@x.com:pw2
                                                   # login-only app: two supplied accounts
# write testing creates one record of its own; TENANTCHECK_BROWSER_WRITE=0 keeps it read-only
```

Or run the web service and let users paste a URL (no terminal needed):

```bash
tenantcheck serve --host 127.0.0.1 --port 8000
# POST /api/submissions {"url": "https://your-app.example"}
# set TENANTCHECK_BROWSER_SCAN=1 to also scan apps that aren't Supabase-backed
```

## The seam that matters

The scanner is generic over a `Target` (`src/verifier/target.py`): how to make two
accounts, and how to discover a record one owns. The `controls/` twin, OWASP Juice
Shop (`src/verifier/juice_shop.py`), a real Supabase project
(`src/verifier/supabase.py`), and any real submission are each one `Target`.
The isolation prober never changes when a new app shape is added.

## Limitations

These are real and measured, not disclaimers:

- **One bug class.** Isolation (read and write). Auth strength, injection, XSS, CSRF
  and infrastructure are out of scope by design.
- **Black-box.** A record that exists but is reachable only by navigation the scanner
  doesn't perform is not tested. No click-through exploration yet.
- **Write verbs.** Browser mode learns the create call from the app's own form, then
  probes with `PATCH`. An app that only accepts `PUT` (or a separate write endpoint)
  can produce a false negative.
- **One record, one shape.** The scan creates a single record per run; no pagination,
  no rate-limit handling, no concurrency.
- **Thin evidence base.** Recall 1.00 / 0 false positives on the control twins and
  OWASP Juice Shop, plus one real app. Every number in
  [`bench/RESULTS.md`](bench/RESULTS.md) traces to a raw JSON run in `bench/results/`.
  Your app may not look like any of them.

## Authorized use

tenantcheck drives the app as a real client with real accounts. Use it only on apps
you own or are authorized to test. The ownership gate and the private-target refusal
exist to make accidental misuse hard, not to grant you permission.

## License

MIT — see [LICENSE](LICENSE).
