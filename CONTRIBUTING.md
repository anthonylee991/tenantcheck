# Contributing

Thanks for looking. This is a small, opinionated codebase; the fastest way in is a
failing test that reproduces a real app behaviour.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Windows
# .venv/bin/pip install -e ".[dev]"       # macOS / Linux
```

Browser work (UI provisioning, browser target) needs the optional extras:

```bash
pip install -e ".[dev,browser]"
patchright install chromium
```

The suite skips browser tests automatically when no engine is installed, so the core
tests run anywhere.

## Checks

```bash
ruff check .
pytest -q
```

Both must pass before a PR. Benches are not part of CI; run the one you changed and
commit the new JSON under `bench/results/` with the row it appends to
`bench/RESULTS.md`.

## Rules that keep the claims honest

1. **Measured, never typed.** No accuracy, recall or timing claim in docs or a report
   unless it traces to a file in `bench/results/` and a row in `bench/RESULTS.md`.
2. **Never touch the user's data.** A write probe may only mutate a record the scan
   created itself.
3. **A scan that proved nothing says so.** Zero reachable records is "Nothing to
   test", never "no problems found".
4. **No secrets in the repo.** No API keys, real customer URLs, or supplied
   credentials in tests, fixtures or logs.

## Reporting a security issue

See [SECURITY.md](SECURITY.md).
