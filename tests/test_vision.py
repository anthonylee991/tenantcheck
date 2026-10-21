"""The vision fallback: parse defensively, spend within budget, off by default."""

from __future__ import annotations

import pytest

from verifier.vision import (
    Action,
    Budget,
    BudgetExceeded,
    NullFallback,
    ScriptedFallback,
    VisionFallback,
    extract_json,
    parse_action,
)


def test_parse_action_reads_a_click_with_coordinates() -> None:
    action = parse_action({"action": "Click", "target": "Sign up button", "coordinates": [10, 20]})
    assert action.kind == "click"
    assert action.target == "Sign up button"
    assert action.coordinates == (10, 20)


def test_parse_action_rejects_the_unknown() -> None:
    assert parse_action({"action": "teleport"}).kind == "give_up"
    assert parse_action({}).kind == "give_up"


def test_parse_action_tolerates_bad_coordinates() -> None:
    assert parse_action({"action": "click", "coordinates": ["x", "y"]}).coordinates is None
    assert parse_action({"action": "click", "coordinates": [5]}).coordinates is None


def test_extract_json_handles_fences_and_prose() -> None:
    assert extract_json('```json\n{"action": "done"}\n```')["action"] == "done"
    assert extract_json('Sure! {"action": "click", "target": "Go"} thanks')["target"] == "Go"
    assert extract_json("not json at all") == {}


def test_budget_stops_at_the_cap() -> None:
    budget = Budget(max_calls=1)
    budget.spend()
    assert budget.remaining == 0
    with pytest.raises(BudgetExceeded):
        budget.spend()


async def test_null_fallback_never_decides_or_spends() -> None:
    fallback = NullFallback()
    assert fallback.enabled is False
    action = await fallback.next_action(page=None, goal="anything")
    assert action.kind == "give_up"


async def test_scripted_fallback_replays_then_gives_up() -> None:
    fallback = ScriptedFallback([Action("click", target="one"), Action("done")])
    assert (await fallback.next_action(None, "goal")).target == "one"
    assert (await fallback.next_action(None, "goal")).kind == "done"
    assert (await fallback.next_action(None, "goal")).kind == "give_up"
    assert fallback.calls == 2


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, payload: dict | None = None, *, boom: bool = False) -> None:
        self.payload = payload or {}
        self.boom = boom
        self.posts: list[dict] = []

    async def post(self, url: str, json: dict, headers: dict) -> _Resp:
        if self.boom:
            raise RuntimeError("network down")
        self.posts.append(json)
        return _Resp(
            {"choices": [{"message": {"content": '{"action": "type", "target": "Email", "text": "a@b.c"}'}}]}
        )

    async def aclose(self) -> None:
        return None


class _Page:
    async def screenshot(self, **kwargs: object) -> bytes:
        return b"\x89PNG\r\n fake bytes"


async def test_vision_fallback_parses_a_reply_and_spends_budget() -> None:
    client = _Client()
    fallback = VisionFallback("key", client=client, max_calls=2)
    action = await fallback.next_action(_Page(), "sign up")
    assert action.kind == "type"
    assert action.target == "Email"
    assert fallback.budget.remaining == 1
    assert client.posts and client.posts[0]["messages"][1]["content"][1]["type"] == "image_url"


async def test_vision_fallback_enforces_its_budget() -> None:
    fallback = VisionFallback("key", client=_Client(), max_calls=1)
    await fallback.next_action(_Page(), "sign up")
    with pytest.raises(BudgetExceeded):
        await fallback.next_action(_Page(), "sign up")


async def test_vision_fallback_failure_is_a_clean_give_up() -> None:
    fallback = VisionFallback("key", client=_Client(boom=True), max_calls=1)
    assert (await fallback.next_action(_Page(), "sign up")).kind == "give_up"


def test_vision_fallback_requires_a_key() -> None:
    with pytest.raises(ValueError):
        VisionFallback("")
