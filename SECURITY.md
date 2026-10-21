# Security policy

## Reporting a vulnerability

Open a private security advisory on GitHub
(<https://github.com/anthonylee991/tenantcheck/security/advisories/new>) or email the
maintainer. Do not open a public issue for a vulnerability in tenantcheck itself.

Please include the version, the command you ran, and the smallest app shape that
reproduces it. A fixture in `controls/` plus a failing test is ideal.

## Authorized use

tenantcheck sends real requests to the app you point it at, as two real accounts, and
(unless `TENANTCHECK_BROWSER_WRITE=0`) creates one record of its own to test writes. It
is a testing tool for apps **you own or are authorized to test**. Scanning someone
else's app is not something the maintainers can authorize for you.

The tool enforces this in two ways, not by trusting the operator:

- **Ownership gate.** For a hosted URL it refuses to scan until the domain proves
  control via a DNS TXT record, a `/.well-known/` file, or a meta tag on the page.
- **Loopback only for local apps.** Private/loopback targets are accepted only from
  the local CLI; the hosted submission API refuses RFC1918, link-local and loopback
  targets, so it cannot be used to reach inside a network.

## What the tool does to the target

- Creates two throwaway accounts (or logs into two you supply).
- Reads the owner's own records; replays those reads and a benign write as the other
  account and with no credentials.
- With browser write probing: creates **one** record of its own through the app's
  create form and only ever mutates that record. Your existing rows are not touched.
- Never executes the target's code; it acts as a client (browser or HTTP).

Credentials supplied with `--account` are used in memory only and are never written
to the report, the store, or logs.

## Out of scope

tenantcheck is not a penetration test. It does not test injection, XSS, CSRF,
authentication bypass, server-side bugs, or infrastructure. It checks one thing:
whether one account can reach another account's data.
