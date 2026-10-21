# tenantcheck

**An external, black-box verifier for apps built by people who can't read the code.**

*Build Specification — Version 0.1 — October 2026*
*Project: `mework/verifier` (working name — branding TBD)*
*Status: draft*

---

## Context: the gap this exists to close

Research across the AI-builder community (r/nocode scans, 510 Trustpilot reviews, SWE-CI, and OWASP-adjacent studies) converges on one thing: **the pain is not the build, it's what happens after.** The people who feel it most are *not* developers — they build on Lovable, Bolt, Replit, Base44 and cannot read the code they shipped.

| Evidence | Number | Source class |
|---|---|---|
| #1 fear: keeping an AI-built app working long-term | **92/100** pain signal (151 r/nocode posts) | community |
| Agents that break previously-working code during maintenance | **75%** of 18 models (100 repos, 233 days) | research (SWE-CI) |
| Tasks that lose earlier-correct behavior over a multi-turn conversation | **43–73%** | research (ASE 2026) |
| AI-built apps with exposed secrets / vulns | **400 secrets, 2,000+ vulns** across 5,600 apps | research (Escape.tech) |
| Lovable apps with weak database access rules | **170 of 1,645** | research (M. Palmer) |
| AI-generated code containing security vulnerabilities | **45%** | research (Veracode) |

Two mechanism-level findings decide the design:

1. **The failure is silent.** Agents "prioritize runnable code over correctness" and suppress errors, so the app *looks* like it works. Broken access control (User A reading User B's rows) is #1 on the OWASP Top Ten and invisible in a single-user demo.
2. **The only fix that consistently works is independent verification against prior behavior.** A "Verification Gate" — check the new state against the *previous* working state, rollback/reset on regression — was the single strategy that improved every model tested (DeepSeek-V3 75.8→87.9%; Llama-3.1-8B 31.6→47.3%). And OWASP is explicit that a test suite written by the *same agent that wrote the code* provides no independent assurance.

Every existing answer assumes a repo, a terminal, and a coding agent (context layers, spec files, agent-written tests, markdown-exporting scanners) — which is exactly the surface the affected audience does not have. Verification for a **deployed URL**, requiring **no repo and no agent**, is unowned.

---

## 1. What it is

**Verifier is a service that watches a deployed app the way a stranger would.** You give it your app's URL and prove you own it. It then acts as a real user: it creates two independent accounts, exercises the app's flows, records how the app behaves, and reports — in plain language — when **one account can reach another account's data**, or when **a change silently broke something that used to work**. It re-checks on every deploy.

Hard properties (these define it):

- **Black-box.** It reads the *running app over the network*, never the source. No repo, no code, no build step.
- **A client, not an executor.** It behaves as a browser/user (HTTP + UI). It does not execute the user's code — the boundary is intact.
- **Independent by construction.** Nothing it does is written by the agent that built the app. It is the different-family adversary.
- **Plain-language output.** Findings are for someone who can't read a stack trace, each paired with a **copy-paste fix prompt** they can hand back to their builder.
- **Continuous.** It remembers how the app behaved before, so it can say what *changed*.

**It is not a SkillVault pillar.** SkillVault is memory/skills for agent users reached through a CLI/MCP/keychain. Verifier is a web service for non-dev builders reached through a URL. They share infrastructure only (see §3.7). Separate product, separate audience, separate business.

---

## 2. What we want to achieve

### 2.1 Primary outcomes

1. **Give non-dev builders confidence** that their AI-built app is safe and still works — turning the #1 documented fear into a checked box.
2. **Detect the failure classes that kill small products:** cross-account data exposure (isolation) and silent regression.
3. **Convert findings into action:** every finding ships with evidence *and* a fix prompt their builder can act on.
4. **Be usable with zero technical skill:** paste a URL, click "verify," read a report. No terminal, ever.

### 2.2 Non-goals (v1)

- Not a source-code scanner or SAST tool (no repo).
- Not a full penetration test (depth over breadth on a narrow, high-value class).
- Not general uptime/performance monitoring (that space is crowded and is not the trust-killer).
- Not the local-repo / dev-agent case (a possible later minority door; not the first build).
- Not the no-code *workflow* case (n8n/Make/Bubble) in v1 — a connector form, later.

### 2.3 Priority order (build in this order)

1. **Isolation** — "can User A reach User B's data?" Highest value, needs zero code understanding, #1 trust-killer.
2. **Behavior baseline / regression** — "we remember how it worked; it changed."
3. **Attempt memory** — "stop going in circles: here is what each change broke."

---

## 3. How we plan to do it

### 3.1 End-to-end pipeline

```
  submit URL
      │
      ▼
  ┌─────────────────┐   fail
  │ ownership proof │──────────► refuse (no proof, no scan)
  └─────────────────┘
      │ pass
      ▼
  ┌─────────────────┐
  │ target model    │  discover signup/login, nav, forms, resources (URLs w/ ids), API calls
  └─────────────────┘
      │
      ▼
  ┌─────────────────┐
  │ provision 2     │  account A + account B (automated signup, or user-supplied test logins)
  │ accounts        │
  └─────────────────┘
      │
      ▼
  ┌──────────────────────────────┐
  │ CHECKS                       │
  │ 1. isolation (v1)            │  attempt B→A access across every reachable resource
  │ 2. behavior baseline (v2)    │  fingerprint flows; replay; diff
  │ 3. attempt memory (v3)       │  record change → outcome
  └──────────────────────────────┘
      │
      ▼
  ┌─────────────────┐
  │ memory (per-app)│  baselines, findings, attempt history
  └─────────────────┘
      │
      ▼
  ┌─────────────────┐
  │ report + handoff│  plain-language finding + evidence + copy-paste fix prompt
  └─────────────────┘
      │
      ▼
  continuous: rerun on deploy (webhook) or schedule → alert on new finding/regression
```

### 3.2 Submission & ownership proof (the consent boundary)

A scan runs **only** on an app the submitter proves they own. Proof by any of: DNS TXT record, an HTML `<meta>` tag, or an uploaded token file — the Google Search Console pattern. This is simultaneously the legal/ethics boundary and the anti-abuse control: no proof, no scan. (See §3.6.)

### 3.3 Target model

From the running app, discover:
- the auth surfaces (signup, login, session shape),
- navigable screens and the forms/actions on them,
- **resources**: every URL/route/param that references a record (e.g. `/api/invoices/1043`, `?order_id=…`),
- the app's own API calls (observed from network traffic) — the fast path to resource enumeration.

Output is a site map + resource inventory: the things worth probing. No source access; all of it is observed as a client.

### 3.4 The three checks

**1. Isolation (v1).** Generalize the "two-user test" to every reachable resource: with account B's credentials, attempt to reach each resource owned by account A by tampering — ID swap, parameter swap, direct API replay, forged-token reuse, unauthenticated fetch. Report any cross-account **read** or **write**, with the exact request that proved it. This is IDOR/BOLA detection, expressed as behavior a non-dev understands: *"Logged in as Anna, we opened Sam's invoice."*

**2. Behavior baseline (v2).** On a known-good state, record a behavioral fingerprint of the app's key flows: screen reachability and content signature, form outcomes, and the shape of each observed API request/response. After a change, replay the same flows and diff. A previously-working behavior that changes (or a flow that stops completing) is flagged. This is characterization/golden testing, black-box — the exact "verification gate" the research validates, without needing the user's tests.

**3. Attempt memory (v3).** Record each observed change and its outcome (change → what broke → whether the next attempt fixed it), so history is visible and loops are broken: *"This is the third time a change to checkout has broken the cart."* For the non-dev flow, this surfaces as a change log of what touched what.

### 3.5 Report & handoff (the action layer)

Each finding is: plain-language title, severity, **evidence** (the request/screenshot that proves it), and a **copy-paste fix prompt** for their builder (Lovable/Bolt/Cursor/etc.). The fix prompt is a first-class output — the ecosystem's real gap is not *finding* problems but *acting* on them. Report is shareable (a link a founder can send a freelancer or investor).

### 3.6 Trust, legal & safety (non-negotiable)

- **Authorized targets only** (ownership proof). Unauthorized scanning is refused.
- **Rate-limited, polite client.** Respect `robots`, throttle, identify the crawler.
- **No exfiltration.** It proves *access*, shows a minimal marker, and never drains data. Test accounts and their data are destroyed after a run.
- **ToS awareness.** Driving a deployed app as a user is a client act; the service stays within the app's normal public surface and never exploits beyond read/write access demonstration.
- **Accessibility-safe.** No attacks that would damage availability or integrity of real user data.

### 3.7 Infrastructure reuse (not product coupling)

| Capability | Reuse | Notes |
|---|---|---|
| Browser/UI driving, accounts, proxies, captcha | Prior in-house stack: *lessons, not code* (see §3.9) | The moat: act as a real user on any hosted app |
| Memory store (baselines, findings, attempts) | Plain versioned JSON on disk | Swappable later; the verifier's identity is not the store |
| Different-family adversary concept | An unrelated in-house app | Framing: the verifier is never the app's own author |
| Distribution | new: web app, URL-in | No CLI, no keychain, no agent |

### 3.8 Build stages

- **Stage 1 (v1):** submission + ownership proof + account provisioning + isolation check + report/fix-prompt. Web-only.
- **Stage 2 (v2):** behavior baseline capture + replay/diff + regression findings.
- **Stage 3 (v3):** attempt memory + continuous re-run on deploy + alerting.

### 3.9 Browser driver (the moat)

Stage 1 probes a discoverable API (Supabase/PostgREST). To work on an app with no
API, the verifier must act as a **real logged-in user**. `src/verifier/browser.py` is
a small, config-driven driver (patchright preferred, optional dependency). The same
driver runs on a Windows workstation and a headless Linux VPS — everything that
differs (headless, display/Xvfb, sandbox, channel) is a `BrowserConfig` field
overridable by `TENANTCHECK_BROWSER_*` env vars, so no deployment edits code. It borrows the
launch lessons of an earlier in-house browser stack (no UA override, minimal flags)
but not its module, which was coupled to accounts/proxy/DB. Phased plan **B1** session+config (built) →
**B2** UI account provisioning (built, §3.10) → **B3** resource discovery from network
traffic (built, §3.11) → **B4** a `browser_target` implementing the existing `Target` seam so the
unchanged prober runs over the browser (built, §3.12). See
[docs/BROWSER_DRIVER.md](docs/BROWSER_DRIVER.md).

### 3.10 UI account provisioning

`src/verifier/ui.py` turns the driver into an outcome: given an app URL and
credentials, create (or log into) an account the way a person would. It navigates to
the signup/login page, finds the fields by common conventions (`input[type=email]`,
`autocomplete`, name/id/placeholder hints, visible labels), fills and submits, then
confirms a **signed-in session** (a session cookie, or a sign-out control).
Deterministic and free for the forms most apps ship.

An unfamiliar form (no findable fields) falls through to an **optional vision
fallback** (`src/verifier/vision.py`): screenshot the page, ask a multimodal model
for one action (`click`/`type`/`press`/`scroll`/`wait`/`done`/`give_up`), execute it,
repeat. It is **off by default** (a `NullFallback` that spends nothing) and hard-capped
by a `Budget`, so a paid call only happens when explicitly enabled. Prefer verifying a
session over wandering: if provisioning fails, the check does not proceed on a guess.
See [docs/VISION_FALLBACK.md](docs/VISION_FALLBACK.md).

### 3.11 Resource discovery from network traffic

Stage 1 discovers resources by introspecting a public API (PostgREST). The browser
equivalent is `src/verifier/network.py`: use the app as a logged-in person and record
the **XHR/fetch** calls it makes, then keep the ones that look like data endpoints.
Documents, static assets and known analytics hosts are dropped; each survivor is
classified as a **collection** (`GET /api/invoices`) or a **record** (`GET
/api/invoices/<id>`, recognised from a numeric/UUID/prefixed id segment or an
`id=eq.` filter). Identical calls are deduped. `candidates()` is pure (calls in,
resources out) and unit-tested without a browser; `TrafficRecorder`/`capture()` attach
to a Playwright page. The output is the replayable candidate list B4 turns into
isolation probes.

### 3.12 Browser-backed target

`src/verifier/browser_target.py` proves the `Target` seam holds: the Stage 1 prober
runs **unchanged** against an app that exposes no documented API. `BrowserClient` is
`Client`-shaped — `request(method, path, token=...) -> .status_code/.text/.json()` —
but routes each request through a Playwright context, so a request carries exactly the
cookies a real browser would for that account. Accounts are **independent browser
contexts** (each signed up through the UI, B2); discovery reads the owner's dashboard
traffic (B3), keeps record endpoints, fetches one as the owner, and takes a distinctive
value from the response as the marker `probe_isolation` asserts on. Two things real
apps required: a session kept in `localStorage` is read and **replayed as a bearer
token** on that account's requests (no cookies involved), and when the dashboard only
lists a collection, record paths are **derived from ids in the owner's own payload**.
A candidate counts as a record only if it answers JSON with a marker that is not just
the id it was asked for - a catch-all that returns HTML with `200` must not become a
"record" (it would make an empty scan look like a pass). `browser_target()`
returns a normal `Target`, so `scan()` and the four probes are reused verbatim.
Write probes need a record the verifier is allowed to mutate, so the scan creates one
of its own through the app's create form, learns the endpoint and field from the
request the app itself made, and arms a PATCH probe for that record only. When no
create affordance exists the scan is read-only, which the result reflects rather than
guessing (and `TENANTCHECK_BROWSER_WRITE=0` forces read-only).

The product door uses this as its fallback: `scan_url(url, browser_scan=scan_browser)`
runs it when the frontend ships no Supabase config, returning a `UrlScan` with
`stack="browser"`. It is opt-in - `tenantcheck scan <url> --browser` on the CLI, and
`TENANTCHECK_BROWSER_SCAN=1` for the API - so the default path stays fast and needs no
browser. See [docs/BROWSER_DRIVER.md](docs/BROWSER_DRIVER.md).

### 3.13 Local apps and bring-your-own-accounts

Two realities the self-signup path assumes away: many DIY apps are **private or
invite-only** (no signup), and many are not hosted at all - they run on the
builder's machine.

- **Loopback is inherently owned.** `verify_ownership` short-circuits for
  `localhost`, `127.0.0.0/8`, and `::1` (method `"loopback"`): the runner is on the
  machine, so there is nothing to publish. `tenantcheck scan http://localhost:3000
  --browser` therefore works with no token. The **hosted API refuses private
  targets** (`is_private_target`: loopback, RFC1918, link-local, `.local`/
  `.internal`) with `blocked_target`, so the submission service can never be used
  to reach inside a network (SSRF). Hostname-level only; DNS-rebinding defence is
  part of hosting, not this slice.
- **Bring-your-own-accounts (BYOA).** Apps with no signup are scanned with two
  existing accounts: `--account user:pass`, given twice (implies `--browser`), or
  `scan_browser(url, accounts=[...])`. Each account is logged in
  (`provision_account(..., create=False)`) in its own browser context. Credentials
  are used in memory only - never written to the store, report, or logs. When no
  accounts are supplied and no signup path exists, `provision_account` raises
  `LoginOnlyError` and the CLI says exactly what to re-run with.
- **Generated accounts are always valid.** Scan-generated addresses use
  `@example.com` (reserved for documentation); strict validators reject
  `example.test`, which failed a real signup with HTTP 422. `TENANTCHECK_ACCOUNT_DOMAIN`
  overrides the domain for apps that require a real one.
- **Field recognition is broader.** Identity fields are matched by
  `type=email`, `autocomplete="username"`, and `name`/`id`/`placeholder` containing
  `user` or `email`, so username-based logins (common in private apps) are handled
  without the vision fallback. A page that has only a login form (password field +
  "sign in" affordance) is reported as login-only rather than "no form", which is
  what happened on the first real private app we tried.

Measured on a loopback twin with two supplied accounts
(`bench/run_local.py`): local scans need no token, the vulnerable twin is found,
the secure twin stays silent.

---

## 4. How we will evaluate it

**Rule (inherited):** no accuracy, recall, or cost claim ships until it traces to a run in `verifier/bench/results/…` and a line in `verifier/bench/RESULTS.md`. Every number in a report or a doc is reproducible from that.

### 4.1 Corpus

- **Positive controls (known bugs):** OWASP **Juice Shop**, **crAPI**, **DVWA** — labeled access-control flaws to measure isolation detection.
- **Seeded corpus:** generate/collect a set of hosted apps and inject a known inventory of bugs (cross-account read, cross-account write, missing auth on a route) with ground-truth labels.
- **Negative controls (clean apps):** apps with *no* seeded bug, to measure false positives.
- **Paired regression set:** pairs of "before/after" app states where the change is known to have silently broken a specific flow, plus pairs where nothing broke.

### 4.2 Metrics

| Metric | For | Why it matters |
|---|---|---|
| **Recall** (seeded bugs found) | isolation, regression | Misses = false confidence (the worst outcome) |
| **Precision / false-positive rate** | isolation, regression | Non-devs abandon noisy tools; FP budget is tiny |
| **Evidence validity** | all | A finding without a reproducible request is not a finding |
| **Cost per scan**, **wall-clock per scan** | all | Must be cheap enough to run on every deploy |
| **Actionability** (qualitative, early) | report | Does a non-dev understand + act? |

### 4.3 Falsification

The project is wrong if: isolation recall cannot exceed a useful floor on the positive corpus; or false positives cannot be held low enough for a non-dev to trust it (target: near-zero on clean apps); or cost/scan makes continuous use uneconomic. These are kill criteria, not tuning goals.

### 4.4 Bench harness

`verifier/bench/` — a runner that takes a target set, runs the checks, and emits a confusion matrix + cost/latency, writing to `results/` (timestamped) and appending a summary line to `RESULTS.md`. Built before the report UI, so the product is measured from day one.

---

## 5. Definition of done

### 5.1 v1 (isolation verifier) — DoD

A run is "done" when **all** hold:

1. A non-technical user can submit a URL, prove ownership, and receive a report **without a terminal**.
2. The service provisions **two independent accounts** automatically on a self-serve-signup app (and accepts user-supplied logins when signup isn't automatable).
3. The isolation check probes **every enumerated resource** and reports cross-account read/write with the **exact reproducing request** as evidence.
4. On the labeled corpus, isolation **recall ≥ target** and **false-positive rate ≤ target** are *measured and recorded* (targets set at bench-harness time, then held).
5. Every finding includes a **copy-paste fix prompt** for a common builder.
6. Ownership proof is enforced — scanning an unauthorized target is refused.
7. `verifier/bench/RESULTS.md` carries the run backing every claim in the product/report.
8. Test accounts and their data are destroyed after each run.

### 5.2 v2 (behavior baseline) — DoD

Adds: fingerprint capture of key flows; replay + diff across two app states; detection of a **seeded silent regression** (measured recall) with a low false-positive rate; a baseline per app version in the memory store; regression findings surfaced in the same plain-language report.

### 5.3 v3 (attempt memory + continuous) — DoD

Adds: change→outcome history per app; re-run automatically on deploy (webhook) or schedule; alerts on new findings/regressions; reports show "what changed since last green" and loop signals ("this flow has broken N times").

### 5.4 Launch DoD (the whole product)

- All three stages meet their DoD and their bench claims are in `RESULTS.md`.
- End-to-end on a real external app the team didn't write: submit → proof → accounts → checks → report → fix prompt, unattended.
- Zero-terminal onboarding verified with a non-technical tester.
- Cost per scan within a stated budget at continuous frequency.
- Legal/consent flow (ownership proof, polite client, data destruction) verified.

---

## 6. Open questions

1. **Name/brand** — `verifier` is a working name.
2. **Ownership proof** — all three methods are implemented (`src/verifier/ownership.py`:
   meta tag → well-known file → DNS TXT, first one wins). Still open: which method is
   *actually available* to non-devs on each host (Lovable/Bolt/Base44 subdomains vs
   custom domains), which decides the default we show first.
3. **Account provisioning** — BYOA is now implemented (two supplied accounts), which
   covers invite-only apps; still open is how far to automate signup (email/OTP
   handling) before asking for the two logins by default.
4. **Depth vs breadth on isolation** — how aggressive the probe set is before it risks the ToS/ethics line.
5. **Pricing** — free first scan → subscription for continuous, vs per-app.
6. **Throughput of the adversarial floor** — at what recall/FP does it need human review before shipping a finding as "verified."
