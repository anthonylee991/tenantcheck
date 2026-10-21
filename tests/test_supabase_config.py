"""Frontend config extraction for Supabase-backed apps."""

from __future__ import annotations

from verifier.supabase import extract_config


def test_extract_jwt_anon_key() -> None:
    text = (
        'const u="https://abcdefghijklmnopqrst.supabase.co";'
        'const k="eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.abc123_XYZ";'
    )
    cfg = extract_config(text)
    assert cfg is not None
    assert cfg.url == "https://abcdefghijklmnopqrst.supabase.co"
    assert cfg.anon_key.startswith("eyJ")


def test_extract_publishable_key() -> None:
    text = (
        "url: https://abcdefghijklmnopqrst.supabase.co "
        "key: sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH"
    )
    cfg = extract_config(text)
    assert cfg is not None
    assert cfg.anon_key == "sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH"


def test_extract_none_when_absent() -> None:
    assert extract_config("<html>no supabase here</html>") is None
