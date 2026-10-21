# Bench results

Every claim about the verifier traces to a run here. New runs are appended by
`bench/run.py` (internal control), `bench/run_juice_shop.py` (external app),
`bench/run_supabase.py` (external Supabase/RLS), `bench/run_url.py` (the product
door), `bench/run_regression.py` and `bench/run_baseline.py` (Stage 2),
`bench/run_ownership.py` (the consent gate) and `bench/run_api.py` (the submission
API) and `bench/run_browser.py` (the browser driver) and `bench/run_ui.py` (UI
account provisioning) and `bench/run_network.py` (traffic discovery),
`bench/run_browser_target.py` (the browser target) and `bench/run_url_browser.py`
(the URL door's browser fallback); raw JSON lives in `bench/results/`.

**Stage 1 (access control).** The prober provisions two independent accounts,
discovers a record one owns, and replays the fetch as the other account and
anonymously â€” then replays a benign *mutation* the same way. It must **find the
vulns** and stay **silent on the clean twin**. The prober itself is unchanged
across all four targets; only the `Target` shape differs.

- *Internal control* â€” `vuln` (2 seeded bugs: cross-account read + write) vs
  `secure` (identical surface, ownership check added, 0 bugs). Measures recall
  **and** false positives.
- *External app* â€” **OWASP Juice Shop** (deliberately vulnerable, not authored by
  us; basket IDOR at `GET /rest/basket/{id}`). Measures recall only (no clean
  twin). Passing here shows the check is not welded to the app it was written
  against.
- *External Supabase/RLS* â€” a throwaway Supabase (the audience's real stack).
  Ground truth from `bench/seed_supabase.sql`:
  - buggy (RLS **disabled**): `public.items` (owner `user_id`), `public.posts`
    (owner `author_id`, NOT NULL columns â€” exercises the column heuristics);
  - clean (RLS **enabled**, owner-only): `public.items_secure`, `public.posts_secure`;
  - ignored: `public.post_titles` (a **view** â€” must be skipped, never flagged).

  One probe pass must flag both buggy tables (read+write, authenticated+anon), leave
  both clean twins alone, and skip the view. Reproduce:
  `npx --yes supabase@latest start`, apply `bench/seed_supabase.sql` via
  `docker exec -i supabase_db_<project> psql -U postgres -d postgres`, then
  `python bench/run_supabase.py`.
- *URL-in (the product door)* â€” `bench/run_url.py` serves a fixture frontend
  (Vite-shaped HTML + JS bundle carrying the Supabase URL and public key), then
  runs the real `scan_url` flow: crawl frontend â†’ extract config â†’ provision two
  users â†’ probe. Asserts the buggy table is flagged and the clean twin is not.
  This is the path `tenantcheck scan <url>` takes. Measured **0.96s** end to end.

**Stage 2 (behaviour / regression).** The gate fingerprints each flow on a
known-good state (status + response *shape*: which fields, of what type), then
replays and diffs against a changed state. It must flag a silent regression and
stay silent when nothing changed. `bench/run_regression.py` takes the baseline on
the `good` control app and compares against twins that differ by one behaviour:
`good` (no change â†’ 0), `regressed_field` (GET one invoice silently drops `note` â†’
1), `regressed_error` (GET one invoice returns 500 â†’ 1). Measured **recall 1.00 on
the seeded regressions, 0 false positives on no-change, 0.05s**.

**Stage 2 store (memory across runs).** `bench/run_baseline.py` runs a four-step
timeline against a temp `BaselineStore`: `good` (baseline established) â†’ `good`
(still green, baseline advances) â†’ `regressed_field` (regression flagged, baseline
**not** advanced â€” the last known-good is kept) â†’ `good` (fixed, advances again).
Budget versions go 1â†’2â†’2â†’3, which is the proof the gate remembers the last green.
Measured **recall 1.00, 0 false positives on the healthy runs**.

**Ownership gate (the consent boundary).** `bench/run_ownership.py` points the
gate at five fixture states: three that publish a real proof (a `<meta>` tag, a
`/.well-known/tenantcheck-verification.txt` file, a DNS TXT record) and two that do
not (no proof at all, and a *wrong* token). The gate must accept all three real
proofs and refuse both non-proofs â€” the safety property is **0 false accepts**.
Measured **proof recall 1.00, 0 false accepts, 0.06s**.

**Submission API (the surface users touch).** `bench/run_api.py` drives the real
FastAPI app with a `TestClient` through the two-step flow a non-developer actually
goes through: submit an app with no proof â†’ `ownership_required` + token; publish
the token and recheck â†’ `complete` + report; submit a proven app â†’ `complete`
straight away. Ownership is checked for real (fixture frontend over ASGI); the scan
is stubbed, because this bench measures the **API contract, not the scan**.
Measured **statuses as expected (3/3), ownership enforced, 0.06s**.

**Browser driver B1 (the moat, first step).** `bench/run_browser.py` serves the
fixture frontend on a real port and drives it through `BrowserSession` (patchright,
headless): launch â†’ navigate â†’ read the DOM back. It must load the page and read the
expected title/DOM. Measured **ok (title + DOM read), ~13.5s launch / ~6.1s navigate
/ 0.04s read** (cold launch; the numbers are lifecycle cost, not a check). The driver
and its portability config are unit-tested in `tests/test_browser_config.py` (no
browser needed).

**UI provisioning B2 (the moat, second step).** `bench/run_ui.py` serves the fixture
web app (a real HTML signup/login with a session cookie) on a real port and drives it
through the browser. Three scenarios: (1) a normal form â€” heuristics find the fields
and create the account, **no fallback**; (2) an **obfuscated** form (no `type`/`name`/
`id` hooks) with a scripted fallback â€” the heuristics fail, the fallback is consulted
and succeeds; (3) an obfuscated form with the **default** (disabled) fallback â€”
nothing is spent and no session is created. Measured **3/3 outcomes as expected,
~1.2â€“1.8s per provisioning, fallback used exactly when expected**. The heuristics and
fallback wiring are unit-tested (`tests/test_vision.py`) and integration-tested
(`tests/test_ui.py`, skipped with no engine installed).

**Traffic discovery B3 (the moat, third step).** `bench/run_network.py` drives the
fixture web app with the B2 provisioning step, then **captures the XHR/fetch traffic**
its dashboard makes while loading. From that raw traffic it must derive both the
collection endpoint (`GET /api/invoices`) and a **record** endpoint
(`GET /api/invoices/<id>`), while discarding the document, static assets and
third-party noise. Measured **2/2 (collection + record) derived, 3 calls captured,
0.04s to derive** (provisioning ~2.1s). The pure analyzer (traffic in â†’ resources out)
is unit-tested in `tests/test_network.py`, with a browser capture test that skips
without an engine.

**Browser target B4 (the moat, the payoff).** `bench/run_browser_target.py` scans two
fixture apps that expose **no documented API**: it signs two accounts up through the
UI (B2), learns the record endpoint from the dashboard's own traffic (B3), creates one
record of its own through the app's create form, and replays both the read and a PATCH
as the other account and anonymously with the **unchanged** isolation prober. The
`vulnerable` app has broken object-level authorization on the record route (any
signed-in user can fetch *or change* any invoice by id) and must be flagged; the
secure twin must stay silent. Measured **recall 1.00 (1/1 cross-account read and 1/1
cross-account write), 0 false positives, ~3.6s per scan**. Only the scanner-created
record is mutated; the user's own rows are never touched. The marker that proves the
leak is taken from the owner's own record returned over the browser, so the prober is
reused verbatim â€” the `Target` seam holds.

**URL door â†’ browser fallback (the product promise).** `bench/run_url_browser.py`
submits a **non-Supabase** app the way a user would â€” plain `scan_url(url)` â€” and the
door falls back to the browser scan when it finds no Supabase config. The app is not
reachable by the old path at all, so this measures the promise "paste any URL". Same
twin design: `vulnerable` (cross-account read â†’ must be found) and secure (must stay
silent). Measured **stack resolved as `browser` for both, recall 1.00, 0 false
positives, ~5.2s per URL**. Enabled on the CLI with `tenantcheck scan <url> --browser` and
on the API with `TENANTCHECK_BROWSER_SCAN=1`, so the default path stays fast and
dependency-free.

**Local apps + bring-your-own-accounts.** `bench/run_local.py` serves the fixture app
on **loopback**, pre-creates two accounts, and scans it the way a local user would:
ownership resolves as `loopback` with no token published, and the scanner logs in to
each supplied account (`--account user:pass` twice) instead of signing up. Same twin
design: `vulnerable` (must be found) and secure (must stay silent). Measured **recall
1.00, 0 false positives, ~5s per scan** (read + write on both twins). This is the path
for an app that isn't hosted and for private/invite-only apps; the hosted API refuses
private targets (`blocked_target`) so it can't be pointed inside a network.

| Run (UTC) | Target | Expected | Found | Recall | False pos. | File |
|---|---|---|---|---|---|---|
| 20261019T090317 | Stage 1: internal control (vuln+secure) | 2 | 2 | 1.00 | 0 | isolation-20261019T090317.json |
| 20261019T090318 | Stage 1: OWASP Juice Shop (external) | 1 | 1 | 1.00 | n/a | juice-shop-20261019T090318.json |
| 20261019T091300 | Stage 1: Supabase throwaway (RLS) | 2 | 2 | 1.00 | 0.00 | supabase-20261019T091300.json |
| 20261019T090321 | Stage 1: URL-in (fixture frontend â†’ real Supabase) | 1 | 1 | 1.00 | 0.00 | url-20261019T090321.json |
| 20261019T091638 | Stage 2: regression (good vs 2 regressed twins) | 2 | 2 | 1.00 | 0 | regression-20261019T091638.json |
| 20261019T112106 | Stage 2: store (4-run timeline, advance only when green) | 1 | 1 | 1.00 | 0 | baseline-20261019T112106.json |
| 20261019T114134 | Ownership gate (3 real proofs + 2 non-proofs, fixture) | 3 | 3 | 1.00 | 0 | ownership-20261019T114134.json |
| 20261019T121127 | Submission API (two-step flow, ownership real, scan stubbed) | 3 | 3 | n/a | 0 | api-20261019T121127.json |
| 20261019T132624 | Browser driver B1 (launch + navigate + DOM read, fixture) | 1 | 1 | n/a | 0 | browser-20261019T132624.json |
| 20261019T142432 | UI provisioning B2 (heuristic + fallback, fixture) | 3 | 3 | 1.00 | 0 | ui-20261019T142432.json |
| 20261019T185527 | Traffic discovery B3 (XHR/fetch â†’ collection + record, fixture) | 2 | 2 | 1.00 | 0 | network-20261019T185527.json |
| 20261019T200744 | Browser target B4 (unchanged prober over the UI; vuln+secure) | 1 | 1 | 1.00 | 0 | browser-target-20261019T200744.json |
| 20261020T164703 | URL-in â†’ browser fallback (non-Supabase app; vuln+secure) | 1 | 1 | 1.00 | 0 | url-browser-20261020T164703.json |
| 20261020T192938 | Local loopback + BYOA (no token; two supplied accounts) | 1 | 1 | 1.00 | 0 | local-20261020T192938.json |
| 20261020T200707 | UI provisioning B2 (heuristic + fallback, fixture) | 3 | 3 | 1.00 | 0 | ui-20261020T200707.json |
| 20261020T200714 | Browser target B4 (unchanged prober over the UI; vuln+secure) | 1 | 1 | 1.00 | 0 | browser-target-20261020T200714.json |
| 20261020T200723 | URL-in â†’ browser fallback (non-Supabase app; vuln+secure) | 1 | 1 | 1.00 | 0 | url-browser-20261020T200723.json |
| 20261020T200734 | Local loopback + BYOA (no token; two supplied accounts) | 1 | 1 | 1.00 | 0 | local-20261020T200734.json |
| 20261020T224745 | Browser target B4 (unchanged prober over the UI; read+write on a scan-created record) | 1 | 1 | 1.00 | 0 | browser-target-20261020T224745.json |
| 20261020T224920 | UI provisioning B2 (heuristic + fallback, fixture) | 3 | 3 | 1.00 | 0 | ui-20261020T224920.json |
| 20261020T224932 | URL-in â†’ browser fallback (non-Supabase app; vuln+secure) | 1 | 1 | 1.00 | 0 | url-browser-20261020T224932.json |
| 20261020T224945 | Local loopback + BYOA (no token; two supplied accounts) | 1 | 1 | 1.00 | 0 | local-20261020T224945.json |
| 20261020T224948 | Traffic discovery B3 (XHR/fetch â†’ collection + record, fixture) | 2 | 2 | 1.00 | 0 | network-20261020T224948.json |
