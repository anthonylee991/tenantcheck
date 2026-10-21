"""Vision fallback: what to do when the heuristics can't find the next control.

B1 gives us a browser; B2's heuristics drive a normal signup form; this is the net
underneath. It screenshots the page and asks a multimodal model for **one** action,
then executes it and repeats — bounded by a hard call cap.

It is **off by default** (`NullFallback`). A paid model runs only when a key is
configured *and* the caller opts in, and never past its budget. See
docs/VISION_FALLBACK.md for the cost model and the escalation policy.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
KINDS = {"click", "type", "press", "scroll", "wait", "done", "give_up"}


class BudgetExceeded(RuntimeError):
    """Raised when a fallback tries to make more model calls than its budget allows."""


@dataclass
class Budget:
    max_calls: int

    def __post_init__(self) -> None:
        self.calls = 0

    def spend(self) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"vision fallback budget exhausted ({self.max_calls} calls)")
        self.calls += 1

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.calls)


@dataclass(frozen=True)
class Action:
    kind: str
    target: str = ""
    text: str = ""
    key: str = ""
    coordinates: tuple[int, int] | None = None
    reason: str = ""


def parse_action(data: dict[str, Any]) -> Action:
    """Turn a model reply into an Action, defensively (bad reply -> give_up)."""
    kind = str(data.get("action", "")).strip().lower()
    if kind not in KINDS:
        return Action("give_up", reason=f"unrecognised action: {kind or '(none)'}")
    coords = data.get("coordinates")
    coordinates = None
    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
        try:
            coordinates = (int(coords[0]), int(coords[1]))
        except (TypeError, ValueError):
            coordinates = None
    return Action(
        kind=kind,
        target=str(data.get("target", "") or ""),
        text=str(data.get("text", "") or ""),
        key=str(data.get("key", "") or ""),
        coordinates=coordinates,
        reason=str(data.get("reason", "") or ""),
    )


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model reply (strips ``` fences, finds the object)."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                value = json.loads(match.group())
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


@runtime_checkable
class Fallback(Protocol):
    enabled: bool

    async def next_action(self, page: Any, goal: str, context: str = "") -> Action: ...
    async def aclose(self) -> None: ...


class NullFallback:
    """The default: refuses to decide, spends nothing."""

    enabled = False

    async def next_action(self, page: Any, goal: str, context: str = "") -> Action:
        return Action("give_up", reason="vision fallback disabled")

    async def aclose(self) -> None:
        return None


class ScriptedFallback:
    """Replays a fixed list of actions. For tests — never touches a model."""

    enabled = True

    def __init__(self, actions: Iterable[Action]) -> None:
        self._actions = list(actions)
        self.calls = 0

    async def next_action(self, page: Any, goal: str, context: str = "") -> Action:
        if self.calls >= len(self._actions):
            return Action("give_up", reason="script exhausted")
        action = self._actions[self.calls]
        self.calls += 1
        return action

    async def aclose(self) -> None:
        return None


_SYSTEM_PROMPT = (
    "You are a careful browser agent. You look at one screenshot of a web page and "
    "decide the single next action toward the goal. Respond with ONLY JSON: "
    '{"action": "click|type|press|scroll|wait|done|give_up", '
    '"target": "human description of the element", "text": "text to type", '
    '"key": "Enter|Tab|Escape", "coordinates": [x, y], "reason": "why"}. '
    "Prefer describing the element in 'target' over guessing coordinates. "
    "'done' = goal achieved; 'give_up' = you cannot make progress."
)


class VisionFallback:
    """OpenRouter multimodal fallback. Opt-in; hard-capped; never runs unbidden."""

    enabled = True

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "qwen/qwen3-vl-30b-a3b-thinking",
        max_calls: int = 8,
        base_url: str = OPENROUTER_API,
        client: Any | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("VisionFallback needs an API key")
        self.api_key = api_key
        self.model = model
        self.budget = Budget(max_calls)
        self.base_url = base_url
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def _http(self) -> Any:
        if self._client is None or getattr(self._client, "is_closed", False):
            import httpx

            self._client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True
        return self._client

    async def next_action(self, page: Any, goal: str, context: str = "") -> Action:
        self.budget.spend()
        image = base64.b64encode(await page.screenshot(type="png", full_page=False)).decode()
        prompt = f"Goal: {goal}"
        if context:
            prompt += f"\nContext: {context}"
        prompt += "\n\nWhat single action should I take next?"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}},
                    ],
                },
            ],
            "max_tokens": 512,
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        client = await self._http()
        try:
            response = await client.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 - any failure is "no action"
            log.warning("vision fallback call failed: %s", exc)
            return Action("give_up", reason=f"vision call failed: {exc}")
        return parse_action(extract_json(content))

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = [
    "Action",
    "Budget",
    "BudgetExceeded",
    "Fallback",
    "NullFallback",
    "ScriptedFallback",
    "VisionFallback",
    "extract_json",
    "parse_action",
]
