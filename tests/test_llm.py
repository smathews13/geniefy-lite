"""Tests for the agent-core model integration + budgeting (U33).

Covers U32 against U4 §5 (structured output, retries, gateway warnings) and D32 (token
counting, per-call input budget, ordered compaction). Hermetic — the transport, token
counter, and sleep are all injected fakes; no real model call.

Run: ``PYTHONPATH=src pytest tests/test_llm.py``
"""
from __future__ import annotations

import pytest

from geniefy_core.llm import (
    Budgeter,
    ChatMessage,
    HeuristicTokenCounter,
    LLMClient,
    LLMError,
    LLMResponse,
    TokenBudgetExceeded,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────
def _ok_response(text: str = '{"ok": true}', usage: dict | None = None, **extra) -> dict:
    d = {
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
    }
    d.update(extra)
    return d


class FakeTransport:
    """Returns a canned response; can raise transient errors a number of times first."""

    def __init__(self, response: dict | None = None, fail_times: int = 0, exc=RuntimeError("boom")):
        self.response = response if response is not None else _ok_response()
        self.fail_times = fail_times
        self.exc = exc
        self.calls: list[dict] = []

    def send(self, messages, *, model, max_tokens, temperature, response_format=None):
        self.calls.append(
            {"messages": messages, "model": model, "max_tokens": max_tokens,
             "temperature": temperature, "response_format": response_format}
        )
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.exc
        return self.response


def _client(transport, **kw):
    kw.setdefault("model_endpoint", "databricks-claude-sonnet-4-6")
    kw.setdefault("counter", HeuristicTokenCounter())
    kw.setdefault("sleep", lambda s: None)  # no real sleep in tests
    return LLMClient(transport, **kw)


# ─────────────────────────────────────────────────────────────────────────────
# Token counter
# ─────────────────────────────────────────────────────────────────────────────
def test_counter_proportional_and_empty():
    c = HeuristicTokenCounter(chars_per_token=4)
    assert c.count("") == 0
    assert c.count("a" * 40) == 10
    assert c.count("a" * 41) >= 10


def test_counter_rejects_bad_ratio():
    with pytest.raises(ValueError):
        HeuristicTokenCounter(chars_per_token=0)


def test_client_count_tokens_sums_messages():
    cl = _client(FakeTransport())
    n = cl.count_tokens([ChatMessage("system", "a" * 40), ChatMessage("user", "b" * 40)])
    assert n == 20


# ─────────────────────────────────────────────────────────────────────────────
# complete — happy path, params, usage, warnings
# ─────────────────────────────────────────────────────────────────────────────
def test_complete_happy_path():
    t = FakeTransport(_ok_response(text="hello", usage={"prompt_tokens": 7, "completion_tokens": 3}))
    r = _client(t).complete([ChatMessage("user", "hi")])
    assert r.text == "hello" and r.input_tokens == 7 and r.output_tokens == 3
    assert r.finish_reason == "stop" and r.warnings == []


def test_complete_defaults_temperature_zero_and_passes_params():
    t = FakeTransport()
    _client(t).complete([ChatMessage("user", "hi")], max_tokens=256)
    call = t.calls[0]
    assert call["temperature"] == 0.0 and call["max_tokens"] == 256
    assert call["model"] == "databricks-claude-sonnet-4-6"


def test_complete_temperature_override():
    t = FakeTransport()
    _client(t).complete([ChatMessage("user", "hi")], temperature=0.7)
    assert t.calls[0]["temperature"] == 0.7


def test_gateway_masking_surfaced_as_warning():
    t = FakeTransport(_ok_response(guardrails={"masked": True}))
    r = _client(t).complete([ChatMessage("user", "hi")])
    assert any("ai_gateway" in w for w in r.warnings)


def test_empty_choices_is_error():
    t = FakeTransport({"choices": []})
    with pytest.raises(LLMError) as ei:
        _client(t).complete([ChatMessage("user", "hi")])
    assert ei.value.code == "empty_response"


# ─────────────────────────────────────────────────────────────────────────────
# Token-budget guard (D32) — raise before sending
# ─────────────────────────────────────────────────────────────────────────────
def test_input_budget_exceeded_does_not_call_transport():
    t = FakeTransport()
    msgs = [ChatMessage("user", "x" * 4000)]  # ~1000 tokens
    with pytest.raises(TokenBudgetExceeded) as ei:
        _client(t).complete(msgs, max_input_tokens=100)
    assert ei.value.budget == 100 and ei.value.needed >= 1000
    assert t.calls == []  # no wasted model call


def test_input_within_budget_proceeds():
    t = FakeTransport()
    r = _client(t).complete([ChatMessage("user", "short")], max_input_tokens=100)
    assert r.text and len(t.calls) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Retries / backoff
# ─────────────────────────────────────────────────────────────────────────────
def test_retries_then_succeeds():
    t = FakeTransport(fail_times=2)  # fail twice, succeed on the 3rd
    sleeps: list[float] = []
    cl = _client(t, max_retries=2, sleep=lambda s: sleeps.append(s))
    r = cl.complete([ChatMessage("user", "hi")])
    assert r.text and len(t.calls) == 3 and len(sleeps) == 2  # 2 backoff waits


def test_persistent_failure_raises_after_attempts():
    t = FakeTransport(fail_times=99)
    cl = _client(t, max_retries=2, sleep=lambda s: None)
    with pytest.raises(LLMError) as ei:
        cl.complete([ChatMessage("user", "hi")])
    assert ei.value.code == "transport_error" and len(t.calls) == 3  # max_retries+1


def test_backoff_is_full_jitter_within_bounds(monkeypatch):
    # full-jitter backoff DRAWS uniform(0, base*2**attempt) per retry — plain backoff would never
    # call uniform at all, so asserting the draws distinguishes jitter from the old fixed backoff.
    import geniefy_core.llm as _llm
    drawn: list[tuple] = []

    def fake_uniform(lo, hi):
        drawn.append((lo, hi))
        return hi

    monkeypatch.setattr(_llm.random, "uniform", fake_uniform)
    t = FakeTransport(fail_times=3)
    sleeps: list[float] = []
    _client(t, max_retries=3, backoff_base=2.0, sleep=lambda s: sleeps.append(s)).complete([ChatMessage("user", "hi")])
    assert drawn == [(0.0, 2.0 * (2 ** i)) for i in range(3)]          # full-jitter bounds, per retry
    assert all(0.0 <= s <= 2.0 * (2 ** i) for i, s in enumerate(sleeps))


def test_rate_limit_honors_retry_after():
    # a 429-style error carrying Retry-After is obeyed verbatim (U80/D47)
    class _RateLimit(Exception):
        def __init__(self):
            super().__init__("429 Too Many Requests")
            self.response = type("R", (), {"headers": {"retry-after": "9"}})()
    t = FakeTransport(fail_times=1, exc=_RateLimit())
    sleeps: list[float] = []
    _client(t, sleep=lambda s: sleeps.append(s)).complete([ChatMessage("user", "hi")])
    assert sleeps == [9.0]


def test_default_retry_budget_covers_bursts():
    # default budget is raised (5) so 429 bursts get more attempts: 5 retries → 6 calls (U80/D47)
    t = FakeTransport(fail_times=99)
    with pytest.raises(LLMError):
        _client(t, sleep=lambda s: None).complete([ChatMessage("user", "hi")])
    assert len(t.calls) == 6


# ─────────────────────────────────────────────────────────────────────────────
# Structured JSON
# ─────────────────────────────────────────────────────────────────────────────
def test_complete_json_parses_and_sets_response_format():
    t = FakeTransport(_ok_response(text='{"definition": "the order key"}'))
    obj, resp = _client(t).complete_json([ChatMessage("user", "draft")])
    assert obj["definition"] == "the order key"
    assert t.calls[0]["response_format"] == {"type": "json_object"}


def test_bad_json_raises():
    t = FakeTransport(_ok_response(text="not json {"))
    with pytest.raises(LLMError) as ei:
        _client(t).complete_json([ChatMessage("user", "draft")])
    assert ei.value.code == "bad_json"


def test_response_json_helper():
    assert LLMResponse(text='{"a": 1}').json() == {"a": 1}
    with pytest.raises(LLMError):
        LLMResponse(text="oops").json()


def test_response_json_strips_markdown_fence():
    # models (Claude via FMAPI) wrap JSON in a ```json fence even when told "no prose" (U77)
    assert LLMResponse(text='```json\n{"table": {"proposed_comment": "x"}}\n```').json() == {"table": {"proposed_comment": "x"}}
    assert LLMResponse(text='```\n[1, 2]\n```').json() == [1, 2]
    # preamble + object recovered via the outermost-{} fallback
    assert LLMResponse(text='Here is the JSON:\n{"a": 1}').json() == {"a": 1}


# ─────────────────────────────────────────────────────────────────────────────
# Budgeter — ordered compaction (D32 / U24 §2)
# ─────────────────────────────────────────────────────────────────────────────
def _msgs(*sizes: int) -> list[ChatMessage]:
    return [ChatMessage("user", "x" * s) for s in sizes]


def test_budgeter_under_budget_no_reducers():
    b = Budgeter(HeuristicTokenCounter(), max_input_tokens=100)
    res = b.fit(_msgs(40), reducers=[])  # ~10 tokens
    assert res.within_budget and res.actions == [] and res.tokens == 10


def test_budgeter_applies_reducers_in_order_until_fit():
    b = Budgeter(HeuristicTokenCounter(), max_input_tokens=20)  # 20 tokens = 80 chars

    def trim(msgs):  # drop to 200 chars (~50 tokens) — shrinks but not enough
        return [ChatMessage(m.role, m.content[:200]) for m in msgs]

    def summarize(msgs):  # collapse to 40 chars (~10 tokens) — now under budget
        return [ChatMessage("user", "summary")]

    res = b.fit(_msgs(4000), reducers=[("trim_context", trim), ("summarize_context", summarize)])
    assert res.within_budget
    assert res.actions == ["trim_context", "summarize_context"]


def test_budgeter_skips_ineffective_reducer():
    b = Budgeter(HeuristicTokenCounter(), max_input_tokens=20)
    noop = lambda msgs: msgs                      # no shrink → not recorded
    fix = lambda msgs: [ChatMessage("user", "ok")]  # shrinks under budget
    res = b.fit(_msgs(4000), reducers=[("noop", noop), ("fix", fix)])
    assert res.within_budget and res.actions == ["fix"]


def test_budgeter_still_over_after_all_reducers():
    b = Budgeter(HeuristicTokenCounter(), max_input_tokens=5)
    weak = lambda msgs: [ChatMessage("user", "x" * 100)]  # ~25 tokens, still over 5
    res = b.fit(_msgs(4000), reducers=[("weak", weak)])
    assert not res.within_budget and res.tokens > 5


def test_budgeter_rejects_bad_budget():
    with pytest.raises(ValueError):
        Budgeter(HeuristicTokenCounter(), max_input_tokens=0)
