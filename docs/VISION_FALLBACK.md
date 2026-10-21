# Vision fallback (UI provisioning)

Stage B2 provisions accounts by **acting like a person** on an unfamiliar signup form.
Deterministic heuristics do this most of the time. The **vision fallback** is the
safety net for when they don't: the verifier screenshots the page, asks a multimodal
model "what's the next single action?", and executes it.

This document scopes that fallback before any of it is wired to a paid model.

---

## 1. Where it sits

```
provision_account(page, url, credentials)
    │
    ├── heuristics: find the form, fill email/password, submit   ← first, cheap, deterministic
    │        │ (fails: no email field / no submit button / unknown layout)
    │        ▼
    └── FALLBACK (opt-in): screenshot → model → one action → repeat, bounded
```

The fallback never replaces the heuristics; it is only consulted when a heuristic
step cannot find its target.

---

## 2. Non-negotiables

1. **Off by default.** The default is `NullFallback` — it returns `give_up` and
   spends nothing. A paid model runs only when a key is configured *and* the caller
   opts in. This honours the standing spend rule.
2. **Bounded, with a hard cap.** `max_calls` (default small) and `max_steps`; the
   cap is enforced in `Budget.spend()`, which raises `BudgetExceeded` and aborts the
   loop. Never "run until it works".
3. **Cost is stated before it is spent.** See §5; the operator approves an estimate
   and a cap, exactly like any other paid run.
4. **No bot-defeat in scope.** The fallback is for *unfamiliar, normal* UI, not for
   defeating CAPTCHAs/anti-bot. Solving human-verification challenges is out of scope
   and, for the audience's apps, not needed to test *their* access control.
5. **Deterministic core stays independent.** With the fallback disabled (the default),
   B2 still works on any app whose signup form follows common conventions.

---

## 3. Interface

```python
class Action:            # what the model returns
    kind: str            # click | type | press | scroll | wait | done | give_up
    target: str = ""     # human description of the element (click)
    text: str = ""       # text to enter (type)
    key: str = ""        # key name (press)
    coordinates: tuple[int, int] | None = None
    reason: str = ""

class Fallback(Protocol):
    enabled: bool
    async def next_action(self, page, goal: str, context: str = "") -> Action: ...
    async def aclose(self) -> None: ...

NullFallback      # enabled=False; next_action -> give_up          (DEFAULT)
ScriptedFallback  # replays a fixed list of Actions               (tests, no spend)
VisionFallback    # OpenRouter multimodal model                   (opt-in, capped)
```

`execute_action(page, action)` (in `ui.py`) performs one step, preferring a
**text/role locator** over raw coordinates — models describe elements well but give
poor pixel coordinates (a hard-won lesson from an earlier agent build).

---

## 4. Escalation policy (when do we call it?)

Heuristics report *why* they stalled:

| Stall | Fallback prompt goal |
|---|---|
| No email/password field found on the auth page | "Find the input for the email address and focus it." |
| No submit control found | "Click the button that submits this form." |
| No visible change after submit | "The form was submitted; what is on screen now?" |
| Unknown interstitial (cookie/consent/onboarding) | "Dismiss the banner / continue the flow." |

Each call returns **one** action; the loop re-screenshots every step, so a bad guess
is recoverable. If the model returns `give_up`, or the budget is exhausted, the
provisioning attempt fails cleanly (and is reported as such, never as success).

---

## 5. Cost model (estimate before spending)

One call = 1 viewport screenshot + a short instruction, returning ≤ ~512 tokens.

| Term | Rough size |
|---|---|
| Image tokens (1280×800) | ~1,400 |
| System + user prompt | ~350 |
| Output | ≤ 512 (usually < 100) |
| **Total per call** | **~1,800 in / ~150 out** |

Illustrative cost (per call, and for a 10-step fallback attempt):

| Model class | $/M in · $/M out | Per call | 10-step attempt | 100 scans/mo (10% escalate) |
|---|---|---|---|---|
| free tier (`:free`) | 0 · 0 | $0 | $0 | $0 (rate-limited) |
| small VLM (~$0.10 · $0.40) | 0.10 · 0.40 | ~$0.00024 | ~$0.0024 | ~$0.024 |
| mid VLM (~$0.30 · $1.20) | 0.30 · 1.20 | ~$0.00072 | ~$0.0072 | ~$0.072 |

So the fallback is **cents per month at expected volumes** — the binding constraint
is rate limits and reliability, not cost. Default cap: `max_calls=8` per provisioning
(the order of magnitude in the table).

---

## 6. Model & transport

- Transport: OpenRouter (`https://openrouter.ai/api/v1/chat/completions`); a
  conventional chat-completions shape.
- Primary model configurable (`TENANTCHECK_VISION_MODEL`); a small/free VLM is fine for
  "where is the email box?". API key via `TENANTCHECK_OPENROUTER_API_KEY`.
- Response parsing is defensive (strip fences, find the JSON object); an unparseable
  reply maps to `give_up`, never a crash.

---

## 7. What is built now (B2)

| Piece | Status |
|---|---|
| `BrowserSession` lifecycle + config (B1) | built |
| `ui.py` heuristics (find/fill/submit, dismiss banners, verify session) | **built** |
| `vision.py`: `Action`, `Fallback`, `NullFallback`, `ScriptedFallback`, `Budget` | **built** |
| `VisionFallback` (real OpenRouter call) | **code present, not exercised** (needs approval + key) |
| `controls/webapp.py` HTML fixture to drive | **built** |
| Tests + bench for provisioning | **built** |

`VisionFallback` is written but deliberately **never invoked by tests or the bench** —
those use `ScriptedFallback`, so the suite and CI spend nothing. Wiring it to a real
model is a separate, explicitly-approved step.

---

## 8. Open questions

1. Which model is the best speed/accuracy point for "locate the email field"? (Needs a
   measured A/B, which is a paid run.)
2. Should a failed heuristic *always* escalate, or only after N unknown steps? (Tune
   once we have real apps.)
3. Multi-step interstitials (email verification, onboarding wizards) — how far to follow
   before asking the user for a test login? (Ties to SPEC open question #3.)
