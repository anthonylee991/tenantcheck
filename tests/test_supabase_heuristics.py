"""Unit tests for the Supabase column/insert heuristics (pure, no network)."""

from __future__ import annotations

from verifier.supabase import _insert_payload, _pick_owner, _pick_text


def test_pick_owner_recognizes_role_suffix() -> None:
    assert _pick_owner({"title": {}, "author_id": {}, "body": {}}) == "author_id"
    assert _pick_owner({"seller_id": {}, "name": {}}) == "seller_id"
    assert _pick_owner({"created_by": {}, "name": {}}) == "created_by"


def test_pick_owner_prefers_known_names() -> None:
    assert _pick_owner({"seller_id": {}, "user_id": {}}) == "user_id"


def test_pick_owner_ignores_org_scoped_and_missing() -> None:
    assert _pick_owner({"org_id": {}, "team_id": {}, "title": {}}) is None
    assert _pick_owner({"title": {}, "body": {}}) is None


def test_pick_text_skips_owner_and_non_text() -> None:
    props = {
        "author_id": {"format": "uuid", "type": "string"},
        "created_at": {"format": "date-time", "type": "string"},
        "body": {"format": "text", "type": "string"},
    }
    assert _pick_text(props, exclude=("author_id",)) == "body"


def test_insert_payload_fills_required_and_marker() -> None:
    props = {
        "id": {"format": "uuid", "type": "string"},
        "author_id": {"format": "uuid", "type": "string"},
        "title": {"format": "text", "type": "string"},
        "body": {"format": "text", "type": "string"},
        "published": {"format": "boolean", "type": "boolean"},
        "created_at": {"format": "timestamp with time zone", "type": "string", "default": "now()"},
    }
    payload = _insert_payload(props, ["title", "body"], "author_id", "title", "UID", "NONCE")
    # body is required without a default -> synthesized; published not required and
    # created_at has a default -> both omitted; owner + marker always set.
    assert payload == {"body": "vrfr", "author_id": "UID", "title": "NONCE"}


def test_insert_payload_uses_enum_first_value() -> None:
    props = {
        "id": {"format": "uuid", "type": "string"},
        "user_id": {"format": "uuid", "type": "string"},
        "name": {"format": "text", "type": "string"},
        "status": {"type": "string", "enum": ["draft", "published"]},
    }
    payload = _insert_payload(props, ["name", "status"], "user_id", "name", "UID", "NONCE")
    assert payload["status"] == "draft"
