"""Unit tests for the Stage 2 behaviour diff."""

from __future__ import annotations

from verifier.behavior import _flatten, _signature, compare


def _obs(status: int = 200, ok: bool = True, fields: dict[str, str] | None = None) -> dict:
    return {"status": status, "ok": ok, "fields": fields if fields is not None else {"id": "str"}}


def test_signature_flattens_array_of_objects() -> None:
    sig = _signature([{"id": 1, "note": "x"}])
    assert _flatten(sig) == {"[].id": "int", "[].note": "str"}


def test_no_change_has_no_findings() -> None:
    base = {"read": _obs(fields={"id": "str", "note": "str"})}
    assert compare(base, {"read": _obs(fields={"id": "str", "note": "str"})}) == []


def test_field_removed_is_high_severity() -> None:
    base = {"read": _obs(fields={"id": "str", "note": "str"})}
    findings = compare(base, {"read": _obs(fields={"id": "str"})})
    assert len(findings) == 1
    assert findings[0].severity == "high"
    assert findings[0].evidence["removed_fields"] == ["note"]


def test_flow_broke_is_high_severity() -> None:
    base = {"read": _obs(status=200, ok=True)}
    findings = compare(base, {"read": _obs(status=500, ok=False)})
    assert len(findings) == 1
    assert findings[0].severity == "high"
    assert "fails" in findings[0].detail


def test_type_changed_is_medium_severity() -> None:
    base = {"read": _obs(fields={"amount": "int"})}
    findings = compare(base, {"read": _obs(fields={"amount": "str"})})
    assert len(findings) == 1
    assert findings[0].severity == "medium"


def test_missing_flow_is_flagged() -> None:
    base = {"read": _obs()}
    findings = compare(base, {})
    assert len(findings) == 1
    assert "no longer reachable" in findings[0].detail
