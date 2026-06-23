"""geniefy-v3 agent core — model integration + token budgeting (U32).

The ``LLMClient`` wraps the generation model (``LLM-agent-core.md`` §5): structured/JSON
output, temperature ~0 for determinism, bounded ``max_tokens``, retries with backoff,
and it surfaces AI-Gateway masking/blocking as a warning rather than failing silently
(D4). Cost predictability (D32 / NFR-B) is supported by a ``TokenCounter`` (estimate a
prompt's input tokens *before* sending) and a generic ``Budgeter`` that runs an ordered
**compaction pipeline** until a prompt fits its per-call budget.

Hermetic by construction (D1/D17): the actual model call is an injected ``ChatTransport``
— the reference FMAPI transport (a Databricks **serving endpoint**, OpenAI-compatible
chat-completions, model id from ``RunConfig.model_endpoint`` e.g. ``databricks-claude-
sonnet-4-6`` / ``databricks-claude-opus-4-8`` per D28) is constructed at the app layer
(U5), not here. This module imports only stdlib and is fully testable with fakes.

The Reasoner (U4 §3.4) and Judge (U4 §3.5) build on this; they supply the domain-specific
reducers (trim context → summarize → reduce profile → shrink batch, U24 §2) to ``Budgeter``.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 4096  # a cap — actual output (and cost) is what the model emits (U77)
DEFAULT_MAX_RETRIES = 5    # cover 429/rate-limit bursts; full-jitter backoff + Retry-After (U80/D47)


# ─────────────────────────────────────────────────────────────────────────────
# Token counting (D32) — pluggable; a real tokenizer can be injected
# ─────────────────────────────────────────────────────────────────────────────
@runtime_checkable
class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class HeuristicTokenCounter:
    """A dependency-free estimate (~4 chars/token). Good enough to *bound* cost; swap
    in a model-accurate tokenizer at the app layer for tighter budgets (D32)."""

    def __init__(self, chars_per_token: float = 4.0):
        if chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")
        self._cpt = chars_per_token

    def count(self, text: str) -> int:
        return max(1, round(len(text) / self._cpt)) if text else 0


# ─────────────────────────────────────────────────────────────────────────────
# Messages / response
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    warnings: list[str] = field(default_factory=list)  # e.g. gateway masked/blocked (D4)
    raw: Mapping[str, Any] = field(default_factory=dict)

    def json(self) -> Any:
        """Parse ``text`` as JSON (structured-output path), tolerant of a ```json … ``` markdown
        fence — models like Claude via FMAPI wrap JSON in a code fence even when told "no prose"
        (U77, caught live at deploy). Falls back to the outermost ``{…}``. Raises ``LLMError`` on
        truly malformed output so the caller can retry/flag rather than crash."""
        text = (self.text or "").strip()
        if text.startswith("```"):  # strip a leading ```json (or ```) fence + the trailing ```
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
            text = text.strip()
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            start, end = text.find("{"), text.rfind("}")  # last resort: the outermost object
            if 0 <= start < end:
                try:
                    return json.loads(text[start:end + 1])
                except (json.JSONDecodeError, TypeError):
                    pass
            raise LLMError("bad_json", f"model output is not valid JSON: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────
class LLMError(Exception):
    """A model-call failure surfaced to the orchestrator (U4 §8)."""

    def __init__(self, code: str, message: str, *, detail: Any | None = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.detail = detail


class TokenBudgetExceeded(Exception):
    """The assembled prompt exceeds the per-call input-token budget (D32). The caller
    (Reasoner) catches this, runs compaction via :class:`Budgeter`, and retries."""

    def __init__(self, needed: int, budget: int):
        super().__init__(f"prompt needs ~{needed} input tokens > budget {budget}")
        self.needed = needed
        self.budget = budget


# ─────────────────────────────────────────────────────────────────────────────
# Transport (injected) — the actual model call
# ─────────────────────────────────────────────────────────────────────────────
@runtime_checkable
class ChatTransport(Protocol):
    """Sends a chat-completion request and returns an OpenAI-compatible response dict
    (``choices[0].message.content``, optional ``usage``). The reference impl is a
    Databricks serving endpoint client built at the app layer (U5)."""

    def send(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        response_format: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


def _retry_after_seconds(exc: Exception) -> float | None:
    """Extract a ``Retry-After`` (seconds) from a rate-limit error if the transport exposes one
    — e.g. ``openai.RateLimitError`` carries ``.response.headers['retry-after']``. Hermetic: we
    never import the transport's SDK; we duck-type the common shape. ``None`` ⇒ no hint, use
    jittered backoff. (U80/D47.)"""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        val = headers.get("retry-after") or headers.get("Retry-After")
        return max(0.0, float(val)) if val is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# LLMClient
# ─────────────────────────────────────────────────────────────────────────────
class LLMClient:
    """Wraps a :class:`ChatTransport` with determinism defaults, retries, structured
    output, token accounting, and an optional per-call input-token budget (D32)."""

    def __init__(
        self,
        transport: ChatTransport,
        *,
        model_endpoint: str,
        counter: TokenCounter | None = None,
        default_temperature: float = DEFAULT_TEMPERATURE,
        default_max_tokens: int = DEFAULT_MAX_TOKENS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._transport = transport
        self.model_endpoint = model_endpoint
        self.counter = counter or HeuristicTokenCounter()
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens
        self.max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep

    def count_tokens(self, messages: Sequence[ChatMessage]) -> int:
        return sum(self.counter.count(m.content) for m in messages)

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: Mapping[str, Any] | None = None,
        max_input_tokens: int | None = None,
    ) -> LLMResponse:
        """Call the model. If ``max_input_tokens`` is set and the prompt exceeds it,
        raise :class:`TokenBudgetExceeded` *before* sending (no wasted call); the caller
        compacts and retries. Retries transient transport failures with backoff."""
        if max_input_tokens is not None:
            need = self.count_tokens(messages)
            if need > max_input_tokens:
                raise TokenBudgetExceeded(need, max_input_tokens)

        payload = [m.to_dict() for m in messages]
        attempts = self.max_retries + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                raw = self._transport.send(
                    payload,
                    model=self.model_endpoint,
                    max_tokens=max_tokens or self.default_max_tokens,
                    temperature=self.default_temperature if temperature is None else temperature,
                    response_format=response_format,
                )
                return self._parse(raw)
            except LLMError:
                raise  # a structured model error is not retried here
            except Exception as exc:  # transient transport/network error
                last_exc = exc
                if attempt < attempts - 1:
                    self._sleep(self._retry_delay(exc, attempt))
        raise LLMError("transport_error", f"model call failed after {attempts} attempts: {last_exc}",
                       detail=str(last_exc))

    def _retry_delay(self, exc: Exception, attempt: int) -> float:
        """Honor a server ``Retry-After`` (rate-limit/429) when the error carries one; else
        **full-jitter exponential backoff** — ``uniform(0, base * 2**attempt)`` — to spread
        retries and avoid a thundering herd against the AI-Gateway rate limits (U80/D47)."""
        retry_after = _retry_after_seconds(exc)
        if retry_after is not None:
            return retry_after
        return random.uniform(0.0, self._backoff_base * (2 ** attempt))

    def complete_json(self, messages: Sequence[ChatMessage], **kwargs: Any) -> tuple[Any, LLMResponse]:
        """Structured-output convenience: request JSON and parse it. Returns
        ``(parsed_obj, response)``; raises ``LLMError('bad_json')`` on malformed output."""
        rf = kwargs.pop("response_format", {"type": "json_object"})
        resp = self.complete(messages, response_format=rf, **kwargs)
        return resp.json(), resp

    @staticmethod
    def _parse(raw: Mapping[str, Any]) -> LLMResponse:
        choices = raw.get("choices") or []
        if not choices:
            raise LLMError("empty_response", "model returned no choices", detail=raw)
        choice = choices[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if content is None:
            raise LLMError("empty_response", "model returned no content", detail=raw)
        usage = raw.get("usage") or {}
        warnings: list[str] = []
        # AI Gateway guardrail signals (D4) — surfaced, not swallowed.
        if raw.get("guardrails") or choice.get("flagged") or msg.get("masked"):
            warnings.append("ai_gateway: content was masked/flagged by a guardrail")
        return LLMResponse(
            text=content,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            finish_reason=choice.get("finish_reason"),
            warnings=warnings,
            raw=raw,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Budgeter (D32 / U24 §2) — generic ordered compaction
# ─────────────────────────────────────────────────────────────────────────────
# A reducer shrinks a message list (e.g. trim context, summarize, reduce profile
# verbosity, shrink the column batch). It returns the new messages; if it can't reduce
# further it returns them unchanged. Each is paired with a stable action label.
Reducer = Callable[[list[ChatMessage]], list[ChatMessage]]


@dataclass
class BudgetResult:
    messages: list[ChatMessage]
    actions: list[str]  # labels of reducers actually applied (for observability + UI honesty)
    tokens: int
    within_budget: bool


class Budgeter:
    """Runs an ordered compaction pipeline until a prompt fits ``max_input_tokens``
    (D32). The Reasoner supplies the domain reducers; this just orchestrates + records
    what was done, so the UI can honestly say "context summarized to fit budget"
    (D23 Principle 5)."""

    def __init__(self, counter: TokenCounter, max_input_tokens: int):
        if max_input_tokens <= 0:
            raise ValueError("max_input_tokens must be positive")
        self.counter = counter
        self.budget = max_input_tokens

    def tokens(self, messages: Sequence[ChatMessage]) -> int:
        return sum(self.counter.count(m.content) for m in messages)

    def fit(self, messages: Sequence[ChatMessage], reducers: Sequence[tuple[str, Reducer]]) -> BudgetResult:
        msgs = list(messages)
        actions: list[str] = []
        tok = self.tokens(msgs)
        if tok <= self.budget:
            return BudgetResult(msgs, actions, tok, True)
        for label, reducer in reducers:
            before = tok
            msgs = list(reducer(msgs))
            tok = self.tokens(msgs)
            if tok < before:  # only record a reducer that actually shrank the prompt
                actions.append(label)
            if tok <= self.budget:
                return BudgetResult(msgs, actions, tok, True)
        # Still over after all reducers — caller decides (send with a truncation marker
        # + a draft warning, per U24 §2). within_budget=False is the honest signal.
        return BudgetResult(msgs, actions, tok, False)
