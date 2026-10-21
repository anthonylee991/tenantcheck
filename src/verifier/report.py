"""Render findings as plain language, plus copy-paste fix prompts.

The ecosystem's real gap is not finding problems but *acting* on them: a
non-technical builder cannot turn "IDOR in GET /api/invoices/{id}" into a change.
The fix prompt is a first-class output — it is designed to be pasted into the
builder's AI (Lovable, Bolt, Cursor, ...).
"""

from __future__ import annotations

from .models import Finding, ScanResult

FIX_PROMPTS: dict[str, str] = {
    "isolation": (
        "In my app, a signed-in user can see another user's data. "
        "For every endpoint that reads or writes a record by id "
        "(for example GET /api/invoices/{id}), check that the record belongs to the "
        "signed-in user, and return 404 if it does not. Do this on the server side, "
        "not just by hiding elements in the UI. Then add a test that signs in as two "
        "different users and confirms one cannot open the other's records."
    ),
    "unauthenticated": (
        "Some of my API routes return data without requiring a login. "
        "Add an authentication check to every route that reads or writes user data, "
        "so a request with no credentials is rejected (401). Then add a test that "
        "calls those routes without a token and confirms they are refused."
    ),
    "cross_account_write": (
        "In my app, a signed-in user can change another user's data. For every endpoint "
        "that updates or deletes a record by id (for example PATCH /api/invoices/{id}), "
        "check on the server that the record belongs to the signed-in user, and return 404 "
        "if it does not. Then add a test that signs in as two different users and confirms "
        "one cannot update or delete the other's records."
    ),
    "unauthenticated_write": (
        "Some of my API routes let a caller change or delete data without logging in. "
        "Add an authentication check to every route that writes user data so an "
        "unauthenticated request is rejected (401). Then add a test that calls those routes "
        "with no token and confirms they are refused."
    ),
    "regression": (
        "Something that used to work in my app now fails, or returns different data, "
        "after my latest change. Compare the current behaviour against the last known-good "
        "version, find what changed, and restore the original behaviour. Then add a test "
        "that checks these flows still return the same status and the same fields."
    ),
}


def _evidence_lines(finding: Finding) -> list[str]:
    if finding.check == "regression":
        lines = [
            f"flow: {finding.evidence.get('flow', '')}",
            f"before: HTTP {finding.evidence.get('before_status', '')}",
            f"now:    HTTP {finding.evidence.get('status', '')}",
        ]
        for key in ("removed_fields", "type_changes"):
            values = finding.evidence.get(key) or []
            if values:
                lines.append(f"{key.replace('_', ' ')}: {', '.join(values)}")
        return lines
    return [
        (
            f"{finding.evidence.get('method', '')} {finding.evidence.get('path', '')} "
            f"-> HTTP {finding.evidence.get('status', '')} "
            f"(as {finding.evidence.get('as', '')})"
        ),
        str(finding.evidence.get("body", ""))[:300],
    ]


def render(result: ScanResult) -> str:
    lines: list[str] = [f"# Verification report - {result.target}", ""]

    if result.baseline_status:
        lines += [f"Behaviour baseline: {result.baseline_status}", ""]

    if not result.findings:
        if result.resources_probed == 0:
            lines += [
                "Nothing to test: no record could be reached through this app's UI.",
                "This does not mean your data is safe - it means the scan proved nothing.",
                "",
            ]
        else:
            lines += [
                "No problems found.",
                (
                    f"({result.resources_probed} record(s) checked across two accounts "
                    "and anonymously.)"
                ),
                "",
            ]
    else:
        lines += [
            (
                f"Found {len(result.findings)} problem(s) across "
                f"{result.resources_probed} checked record(s):"
            ),
            "",
        ]
        for finding in result.findings:
            lines += [
                f"## {finding.title}  [{finding.severity}]",
                "",
                finding.detail,
                "",
                "Evidence:",
                "```",
                *_evidence_lines(finding),
                "```",
                "",
            ]

    if result.findings:
        checks = {finding.check for finding in result.findings}
        lines += ["---", "", "## Fix prompt(s) (paste into your builder)", ""]
        for check in sorted(checks):
            lines += ["```", FIX_PROMPTS[check], "```", ""]
    return "\n".join(lines)
