r"""Complete the non-word entries with an LLM.

Only records that the extractor could not resolve alone are sent here — that
is, records with ``status == "pending"`` and ``type != "word"``.  Words that a
dictionary could not resolve stay the lemmatizer's business; the LLM never
sees them.

The provider is described entirely by :func:`common.config.get_openai_settings`
(an OpenAI-compatible Chat Completions endpoint), so nothing here is tied to a
particular vendor.  When the provider advertises JSON-schema support the
structured-output path is used; otherwise the prompt demands strict JSON and
the reply is validated and retried.

Output is English only, and the analysis is angled per record type:

* a **phrase** is explained in terms of meaning, usage and near-synonyms;
* a **sentence** is mined for structure and verb usage, not translated.

Etymology and first-use metadata are deliberately left empty.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Sequence

from common.config import (
    CODE_BAD_RESPONSE,
    CODE_OK,
    LLM_DICTIONARY_NAME,
    SOURCE_EXTRACTOR,
    SOURCE_LLM,
    STATUS_PENDING,
    TYPE_PHRASE,
    TYPE_SENTENCE,
    TYPE_WORD,
    Artifacts,
    BoldEntry,
    empty_entry,
    get_llm_settings,
    get_openai_settings,
    normalize_entry,
)

from lookup import write_json

from . import llm_reasoning

__all__ = ["SYSTEM_PROMPT", "complete_entries", "run", "schema_for"]


#: Upper bound on the tokens a completion may spend.  A six-field structured
#: reply needs a few hundred at most; the ceiling exists to stop a model that
#: ignores the reasoning-suppression hints from running away on a runaway
#: chain of thought.
MAX_COMPLETION_TOKENS = 400

#: How many ordinary failures in a row abort the whole stage.  A burst like
#: this means the endpoint is down or throttling, not that one record is odd.
CONSECUTIVE_FAILURE_LIMIT = 5


class LLMAbort(RuntimeError):
    """A fatal, non-retriable provider verdict (401/403).

    Raised when the request was rejected on authentication or permission
    grounds: retrying cannot help, so the entire batch is abandoned rather
    than hammering the endpoint once per record.
    """


class ReasoningUnsupported(RuntimeError):
    """The provider does not understand the current reasoning-suppression field.

    Signals the caller to move on to the next candidate payload instead of
    treating the failure as a genuine completion failure.
    """


SYSTEM_PROMPT = (
    "You are a lexicographer writing concise English study notes for a "
    "language learner. Always reply with a single JSON object and nothing "
    "else. Never invent etymology, first-use dates, or metadata fields."
)

_PHRASE_INSTRUCTION = (
    "The input is a multi-word PHRASE. Focus on what the phrase means, when a "
    "speaker would use it, and any close synonymous expressions. Give the "
    "synonyms in the `synonyms` array."
)

_SENTENCE_INSTRUCTION = (
    "The input is a full SENTENCE. Do NOT translate or explain the whole "
    "sentence. Instead explain the sentence PATTERN and the vocabulary used, "
    "especially the verb: its form, its meaning here, and how the pattern is "
    "reused. Put the reusable part of the pattern in `shortDefs`."
)

#: The JSON schema the model must satisfy.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pos", "definition", "examples"],
    "properties": {
        "pos": {
            "type": "string",
            "description": "part of speech, e.g. 'phrase' or 'sentence pattern'",
        },
        "definition": {
            "type": "string",
            "description": "one-line English explanation",
        },
        "examples": {
            "type": "array",
            "items": {"type": "string"},
            "description": "one to three short English example sentences",
        },
        "synonyms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "near-synonymous expressions (phrases only)",
        },
        "antonyms": {
            "type": "array",
            "items": {"type": "string"},
        },
        "shortDefs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "compact glosses",
        },
    },
}


def schema_for() -> dict[str, Any]:
    """Return the JSON schema used for structured output."""
    return _RESPONSE_SCHEMA


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _instruction_for(record_type: str) -> str:
    if record_type == TYPE_SENTENCE:
        return _SENTENCE_INSTRUCTION
    return _PHRASE_INSTRUCTION


def _user_prompt(entry: BoldEntry) -> str:
    """Build the per-record user message."""
    return (
        f"{_instruction_for(entry.type)}\n\n"
        f"Bold text: {entry.word!r}\n"
        f"Type: {entry.type}\n"
        f"Source sentence: {entry.sentence!r}\n\n"
        "Reply with JSON using these keys: "
        "pos (string), definition (string), examples (array of strings), "
        "synonyms (array of strings), antonyms (array of strings), "
        "shortDefs (array of strings). "
        "Every string must be in English."
    )


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------

def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        found: list[str] = []
        for item in value:
            found.extend(_as_string_list(item))
        return found
    return []


def _validate(payload: Any) -> dict[str, Any]:
    """Validate a decoded reply against the schema; raise on a violation."""
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")

    definition = str(payload.get("definition") or "").strip()
    if not definition:
        raise ValueError("missing 'definition'")

    return {
        "pos": str(payload.get("pos") or "").strip(),
        "definition": definition,
        "examples": _as_string_list(payload.get("examples")),
        "synonyms": _as_string_list(payload.get("synonyms")),
        "antonyms": _as_string_list(payload.get("antonyms")),
        "shortDefs": _as_string_list(payload.get("shortDefs")),
    }


def _to_standard_entry(entry: BoldEntry, payload: dict[str, Any]) -> dict[str, Any]:
    """Map a validated LLM reply onto the standard entry schema."""
    result = empty_entry()
    result["word"] = entry.word
    result["language"] = "en"
    result["dictionary"] = LLM_DICTIONARY_NAME
    result["section"] = "alpha"
    result["offensive"] = False
    result["pos"] = payload["pos"] or entry.type
    result["senses"] = [
        {
            "pos": payload["pos"] or entry.type,
            "definition": payload["definition"],
            "examples": payload["examples"],
        }
    ]
    result["shortDefs"] = payload["shortDefs"] or [payload["definition"]]
    result["synonyms"] = payload["synonyms"]
    result["antonyms"] = payload["antonyms"]
    return normalize_entry(result)


# ---------------------------------------------------------------------------
# Provider interaction
# ---------------------------------------------------------------------------

def _create_client() -> Any:
    """Create the OpenAI-compatible client from the shared settings.

    The client carries the LLM's own timeout, which is deliberately *not* the
    dictionary stage's 20s: a reasoning model frequently needs longer before it
    emits its first byte, and the SDK default of 600s would let one stalled
    request block the pipeline for ten minutes.

    ``max_retries=0`` disables the SDK's built-in retrying on purpose.  It would
    otherwise multiply with this module's own retry loop, turning a configured
    "3 retries" into up to nine requests and stacking two independent timeouts.
    Retrying is owned here, so the logs stay explainable.
    """
    from openai import OpenAI

    api_key, base_url, _model = get_openai_settings()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set but USE_LLM is enabled."
        )
    llm = get_llm_settings()
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": llm["timeout"],
        "max_retries": 0,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _request_completion(
    client: Any,
    *,
    model: str,
    entry: BoldEntry,
    use_schema: bool,
    attempts: int = 4,
    timeout: float = 60.0,
    backoff: float = 0.5,
    extra_body: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Ask the provider, retrying transient failures with linear backoff.

    Retry policy:

    * authentication/permission verdicts (401/403) are fatal — raised as
      :class:`LLMAbort` so the caller can abandon the batch immediately;
    * a rejected ``json_schema`` downgrades to ``json_object`` once and
      continues (the prompt still spells out the required keys);
    * a rejected reasoning-suppression field is raised as
      :class:`ReasoningUnsupported` so the caller can try the next candidate;
    * everything else — timeouts, rate limits, 5xx, malformed JSON — is
      retried up to *attempts* times with a linearly growing pause.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(entry)},
    ]

    last_error: Exception | None = None

    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(max(backoff, 0.0) * attempt)
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0,
                "timeout": timeout,
            }
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            if extra_body:
                kwargs["extra_body"] = extra_body
            if use_schema:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "excerpt_entry",
                        "strict": True,
                        "schema": _RESPONSE_SCHEMA,
                    },
                }
            else:
                kwargs["response_format"] = {"type": "json_object"}

            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            return _validate(json.loads(content))
        except LLMAbort:
            raise
        except ReasoningUnsupported:
            raise
        except Exception as exc:  # noqa: BLE001 - provider errors vary widely
            last_error = exc
            if _classify_llm_error(exc) == "auth":
                raise LLMAbort(exc) from exc
            # The reasoning check must come first.  Providers report an unknown
            # ``extra_body`` field with wording such as ``invalid_request_error``
            # that the schema test below also matches, and downgrading the
            # schema would leave the rejected field in place on every retry.
            if extra_body and llm_reasoning.is_reasoning_rejection(exc):
                raise ReasoningUnsupported(str(exc)) from exc
            # A provider that rejects json_schema should fall back once.
            if use_schema and _looks_like_schema_rejection(exc):
                use_schema = False
                continue

    raise RuntimeError(f"LLM completion failed: {last_error}")


def _looks_like_schema_rejection(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("json_schema", "response_format", "unsupported", "invalid_request")
    )


def _extract_status(text: str) -> int:
    """Pull the HTTP status out of an SDK error string, or ``0`` when absent.

    The OpenAI SDK renders failures as ``Error code: 429 - {...}``; aggregators
    keep that shape, so the digits right after the marker are the status.
    """
    marker = "Error code: "
    if marker not in text:
        return 0
    tail = text.split(marker, 1)[1]
    digits = ""
    for char in tail:
        if char.isdigit():
            digits += char
        else:
            break
    return int(digits) if digits else 0


def _classify_llm_error(exc: Exception) -> str:
    """Classify a provider failure into a retry decision.

    Returns one of ``auth`` (give up at once), ``rate_limit``, ``server``,
    ``network``, ``bad_reply``, or ``other``.  Only ``auth`` is treated as
    fatal; the rest are worth another attempt.
    """
    text = str(exc)
    lowered = text.lower()
    status = _extract_status(text)

    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server"
    if isinstance(exc, (TimeoutError, ConnectionError)) or "timeout" in lowered:
        return "network"
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "bad_reply"
    return "other"


def _summarize_error(exc: Exception) -> str:
    """Reduce a verbose provider error to one actionable line.

    Aggregators wrap the upstream fault in a large nested payload; printing it
    whole buries the log.  Pull out the HTTP status and, when present, the
    innermost provider message — the part a user can act on.
    """
    text = str(exc)

    status = _extract_status(text) or ""

    detail = ""
    # OpenRouter nests the real message inside metadata.raw; the openai SDK
    # renders that dict with single quotes, other paths use double quotes, so
    # both spellings are searched.
    for key in ('"raw":', "'raw':", '"message":', "'message':"):
        if key not in text:
            continue
        chunk = text.split(key, 1)[1].lstrip()
        if chunk.startswith(('"', "'")):
            quote = chunk[0]
            try:
                detail = chunk[1 : chunk.index(quote, 1)].strip()
            except ValueError:
                detail = ""
        if detail:
            break
    # A raw blob may itself be escaped JSON; in that case take its inner
    # message rather than the braces.
    if detail.startswith("{"):
        for inner_key in ('"message":', "'message':"):
            if inner_key in detail:
                inner = detail.split(inner_key, 1)[1].lstrip()
                if inner.startswith(('"', "'")):
                    quote = inner[0]
                    try:
                        detail = inner[1 : inner.index(quote, 1)].strip()
                    except ValueError:
                        pass
                break
    if detail:
        detail = detail.replace("\\n", " ").split(". ")[0].strip().rstrip(".")

    head = f"HTTP {status}" if status else "request failed"
    return f"{head}: {detail}" if detail else head


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class _CircuitBreaker:
    """Stop the stage once failures clearly are not about one record.

    Two triggers:

    * an authentication/permission verdict (401/403) — retrying cannot help, so
      the batch is abandoned at once;
    * *consecutive_limit* ordinary failures in a row — a run of them means the
      endpoint is down or throttling, not that a particular phrase is cursed.

    The tripped state is **not** persisted.  It describes one run, and a stale
    "aborted" flag on disk would wrongly suppress the *next* run, which is
    exactly the mistake ``llm_check`` avoids by never caching transient
    verdicts.  Entries left untouched stay ``pending`` and are retried on the
    next invocation through the existing idempotency rule.
    """

    def __init__(self, consecutive_limit: int = CONSECUTIVE_FAILURE_LIMIT,
                 enabled: bool = True):
        self._limit = max(1, consecutive_limit)
        self._enabled = enabled
        self._consecutive = 0
        self._tripped = False
        self._reason = ""

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def reason(self) -> str:
        return self._reason

    def trip(self, exc: Exception) -> None:
        """Trip on a fatal verdict, recording the actionable reason."""
        self._tripped = True
        self._reason = f"fatal: {_summarize_error(exc)}"

    def record_failure(self) -> None:
        self._consecutive += 1
        if self._enabled and self._consecutive >= self._limit:
            self._tripped = True
            self._reason = f"aborted after {self._consecutive} consecutive failures"

    def record_success(self) -> None:
        self._consecutive = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def complete_entries(
    entries: Sequence[BoldEntry],
    *,
    client: Any = None,
    model: str = "",
    use_schema: bool = True,
    workers: int | None = None,
) -> list[BoldEntry]:
    """Complete every pending non-word entry in place and return the list.

    Entries already marked ``filled`` are skipped, which makes re-running the
    pipeline idempotent.

    ``workers`` controls concurrency.  The default of 1 (or ``None``, which
    resolves to the configured value) keeps the historical strictly sequential
    behaviour.  Above 1 the requests are issued by a thread pool — but only the
    *request and parse* half moves off the main thread: entries are updated and
    messages are printed afterwards, in input order, on the calling thread.  No
    worker touches a file, because ``lookup.write_json`` rewrites
    ``.words.json``/``.errors.json`` with a read-merge-write that would race if
    several threads ran it at once.
    """
    targets = [
        entry
        for entry in entries
        if entry.type != TYPE_WORD and entry.source == SOURCE_EXTRACTOR
    ]
    if not targets:
        return list(entries)

    if client is None:
        client = _create_client()
    llm = get_llm_settings()
    if not model:
        model = get_openai_settings()[2]
    if workers is None:
        workers = llm["workers"]

    total = len(targets)
    breaker = _CircuitBreaker()
    # Serial runs enforce the breaker only for the fatal (401/403) verdict: a
    # handful of flaky records in an otherwise healthy run must not cost the
    # remaining ones their turn.  Concurrent runs need the stricter
    # consecutive-failure rule, because they are the ones that can hammer a
    # rate-limited endpoint with several doomed requests at once.
    breaker._enabled = workers > 1
    reasoning = llm_reasoning.ReasoningState(override=llm["reasoning"])

    def _one(index: int, entry: BoldEntry) -> tuple[int, BoldEntry, dict | None, str]:
        """Issue one request.  Deliberately free of any shared mutation."""
        if breaker.tripped:
            return (index, entry, None, "aborted")
        while True:
            try:
                payload = _request_completion(
                    client,
                    model=model,
                    entry=entry,
                    use_schema=use_schema,
                    attempts=llm["retries"] + 1,
                    timeout=llm["timeout"],
                    backoff=llm["backoff"],
                    extra_body=reasoning.current(),
                    max_tokens=MAX_COMPLETION_TOKENS,
                )
            except LLMAbort as exc:
                breaker.trip(exc)
                return (index, entry, None, "fatal")
            except ReasoningUnsupported:
                # The endpoint does not know this field.  ``advance`` reports
                # whether a further candidate is left to try.
                if reasoning.advance():
                    continue
                return (index, entry, None, "reasoning_unsupported")
            except Exception as exc:  # noqa: BLE001 - one failure must not abort
                reason = _summarize_error(exc)
                if breaker.tripped:
                    return (index, entry, None, "aborted")
                breaker.record_failure()
                return (
                    index,
                    entry,
                    None,
                    "aborted" if breaker.tripped else reason,
                )
            else:
                breaker.record_success()
                reasoning.confirm()
                return (index, entry, payload, "ok")

    if workers <= 1:
        results = [_one(index, entry) for index, entry in enumerate(targets, start=1)]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(lambda pair: _one(*pair), enumerate(targets, start=1))
            )

    # Everything below runs on the calling thread, in input order.
    results.sort(key=lambda item: item[0])
    for index, entry, payload, status in results:
        if status == "ok":
            entry.entry = _to_standard_entry(entry, payload)
            entry.code = CODE_OK
            entry.detail = ""
            entry.source = SOURCE_LLM
            print(f"  [llm {index}/{total}] {entry.word!r}: ok")
        elif status == "aborted":
            print(f"  [llm {index}/{total}] {entry.word!r}: skipped (aborted)")
        elif status == "fatal":
            print(f"  [llm {index}/{total}] {entry.word!r}: skipped (fatal)")
        else:
            print(f"  [llm {index}/{total}] {entry.word!r}: failed ({status})")
            entry.detail = status

    if breaker.tripped:
        # Report once, on the calling thread, so the reason is not buried
        # between per-record lines.  The remaining records keep their pending
        # state and are picked up by the next run.
        print(f"[llm] stopped early: {breaker.reason}")

    return list(entries)


def run(
    entries: Sequence[BoldEntry],
    art: Artifacts,
    *,
    dict_slug: str = "",
) -> list[BoldEntry]:
    """Run the LLM stage and rewrite ``.words.json`` / ``.errors.json``."""
    from common.config import get_dict_slug

    resolved = complete_entries(entries)

    out_dir = Path(art.out_dir)
    write_json.write_documents(
        resolved,
        words_path=out_dir / art.words_json,
        errors_path=out_dir / art.errors_json,
        dict_slug=dict_slug or get_dict_slug(),
        llm_source=True,
    )
    return resolved