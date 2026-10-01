r"""Complete the non-word entries with an LLM.

Only records that the extractor could not resolve alone are sent here — that
is, records with ``status == "pending"`` and ``type != "word"``.  Words that a
dictionary could not resolve stay the lemmatizer's business; the LLM never
sees them.

The provider is described entirely by :func:`common.config.get_openai_settings`
(an OpenAI-compatible Chat Completions endpoint), so nothing here is tied to a
particular vendor.  Structured output is attempted through the candidate list
in :mod:`llm.format`, which walks from full JSON Schema down to "send nothing
and rely on the prompt"; a provider that understands none of the dialects
therefore still completes every record instead of failing the stage.

Output is English only, and the analysis is angled per record type:

* a **phrase** is explained in terms of meaning, usage and near-synonyms;
* a **sentence** is mined for its pattern, never for one word inside it.

Etymology and first-use metadata are deliberately left empty.

Orchestration lives here; the concerns it walks through are separate siblings
(:mod:`llm.cache`, :mod:`llm.check`, :mod:`llm.format`, :mod:`llm.reasoning`).
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Aliased to the old ``llm_*`` names so the body below reads unchanged from
# when these were ``excerpt.llm_*`` modules.  ``format`` is aliased for a second
# reason: as a bare name it would shadow the builtin.
from . import cache as llm_cache
from . import format as llm_format
from . import reasoning as llm_reasoning

__all__ = ["SYSTEM_PROMPT", "complete_entries", "run", "schema_for"]


#: Upper bound on the tokens a completion may spend.  A six-field structured
#: reply needs a few hundred at most; the ceiling exists to stop a model that
#: ignores the reasoning-suppression hints from running away on a runaway
#: chain of thought.  It sits at 500 rather than 400 to leave room for the two
#: full-length example sentences the prompt asks for: a model that runs out of
#: budget mid-string emits truncated JSON, and a truncated reply costs a retry
#: rather than a shorter answer.
MAX_COMPLETION_TOKENS = 500

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
    "language learner. Reply with one JSON object only. Never invent "
    "etymology, first-use dates, or metadata."
)

_PHRASE_INSTRUCTION = (
    "The input is a PHRASE, not a sentence. Explain what it means and when a "
    "speaker would use it."
)

#: The sentence instruction states what the record is *not*.  An earlier
#: version asked for "the vocabulary used, especially the verb: its form, its
#: meaning here", and models obeyed literally: they picked one conspicuous
#: word out of the sentence and wrote that word's dictionary sense into
#: ``definition``.  Forbidding the word-level reading explicitly — and naming
#: the pattern as the only legitimate subject — is what keeps the reply about
#: the sentence rather than about a word inside it.
_SENTENCE_INSTRUCTION = (
    "The input is a whole SENTENCE, not a word: do not translate it and "
    "never define a single word from it. Explain the PATTERN: what it "
    "conveys, when it is used, and the grammar worth reusing; put that "
    "pattern in `shortDefs`. Set `pos` to \"sentence\"."
)

#: What every record's examples and synonym sets must look like.
#:
#: Kept separate from the two type instructions because it applies to both, and
#: shared by them rather than repeated: the alternative was the same sentence
#: twice, which costs the tokens the split saves.  The length requirement is
#: deliberately blunt — models default to textbook sentences ("The car zoomed
#: past us.") unless told the register they are writing for, and a study note
#: whose examples are easier than the text being studied teaches nothing.
_QUALITY_INSTRUCTION = (
    "Write about two examples: full natural sentences an educated adult would "
    "write, reusing the phrase or pattern, never school-textbook ones. Make "
    "`synonyms` and `antonyms` idiomatic, not flat words."
)

#: The reply contract, stated in the prompt itself.
#:
#: This is not redundant with the schema.  ``response_format`` is walked from
#: full JSON Schema down to "send nothing at all" (see :mod:`llm.format`), and
#: on the last two rungs of that walk the prompt is the *only* statement of the
#: required keys.  It is kept terse because the schema carries the types
#: whenever the endpoint accepts one, and because the system prompt above
#: already establishes that the answer is a JSON object.
_REPLY_TAIL = (
    "Keys: pos, definition, examples, synonyms, antonyms, shortDefs. "
    "All strings in English."
)

#: The JSON schema the model must satisfy.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pos", "definition", "examples"],
    "properties": {
        "pos": {
            "type": "string",
            "description": (
                "part of speech; for a whole sentence use exactly 'sentence'"
            ),
        },
        "definition": {
            "type": "string",
            "description": (
                "one-line English explanation; for a sentence, the pattern's "
                "meaning and use, never a single word"
            ),
        },
        "examples": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "about two full, sophisticated English example sentences that "
                "reuse the phrase or pattern"
            ),
        },
        "synonyms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "idiomatic near-synonymous expressions",
        },
        "antonyms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "idiomatic near-opposite expressions",
        },
        "shortDefs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "compact glosses; for a sentence, the reusable pattern",
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


def _record_lines(entry: BoldEntry) -> list[str]:
    """Return the record's own lines, with no duplicated text.

    A sentence record bolds the whole sentence, and ``extractor.find_sentence``
    keeps the ``**`` markers of the span in the sentence it returns — so
    ``word`` is always a substring of ``sentence`` for a sentence record, and
    printing both would state the same text twice.  Repeating it was another
    nudge towards the word-level reading this stage is trying to avoid, so a
    sentence is described by its one sentence line only.
    """
    if entry.type == TYPE_SENTENCE and entry.word and entry.word in entry.sentence:
        return [f"Sentence: {entry.sentence!r}"]

    lines = [f"Bold text: {entry.word!r}"]
    if entry.sentence:
        lines.append(f"Source sentence: {entry.sentence!r}")
    return lines


def _user_prompt(entry: BoldEntry) -> str:
    """Build the per-record user message.

    The record comes first and the rules last on purpose: the rules that decide
    what the reply must look like are the last thing the model reads, which is
    where the type-specific one has to win over the generic reply-format line
    that follows it.  The record's own ``type`` is not printed separately —
    :func:`_instruction_for` already names it, and a bare ``Type: sentence``
    line invites the model to echo it back as the part of speech.
    """
    return (
        "\n".join(_record_lines(entry))
        + "\n\n"
        + _instruction_for(entry.type)
        + "\n\n"
        + _QUALITY_INSTRUCTION
        + "\n\n"
        + _REPLY_TAIL
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
    # A sentence has no part of speech, and a model asked for one answers with
    # whatever it can justify — "verb", "idiomatic expression", a tense.  The
    # label is therefore pinned here rather than merely requested in the
    # prompt, so nothing the model writes for ``pos`` can reach the disk.  One
    # value feeds both the entry and its single sense, keeping the note body,
    # the index table and the handout in agreement.
    pos = (
        TYPE_SENTENCE
        if entry.type == TYPE_SENTENCE
        else (payload["pos"] or entry.type)
    )
    result["pos"] = pos
    result["senses"] = [
        {
            "pos": pos,
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
    schema_state: llm_format.SchemaState | None = None,
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
    * a refused structured-output dialect advances to the next candidate and
      does **not** consume a retry: the request itself was fine, only the
      format field was not understood.  After the last candidate there is
      nothing left to refuse, so a schema disagreement can never abort the
      stage;
    * a refused reasoning-suppression field is raised as
      :class:`ReasoningUnsupported` so the caller can try the next candidate;
    * everything else — timeouts, rate limits, 5xx, malformed JSON — is
      retried up to *attempts* times with a linearly growing pause.

    *schema_state* carries the dialect chosen so far.  Passing the same
    instance for every record is what stops a second record from re-probing a
    dialect the first one already proved unsupported; omitting it makes this
    call self-contained.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(entry)},
    ]

    state = schema_state if schema_state is not None else llm_format.SchemaState()
    last_error: Exception | None = None
    announced_unsupported = False

    attempt = 0
    while attempt < max(1, attempts):
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
            format_payload = llm_format.candidate_for(
                state.index(), _RESPONSE_SCHEMA
            )
            if format_payload is not None:
                kwargs["response_format"] = format_payload

            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            payload = _validate(json.loads(content))
            state.confirm()
            return payload
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
            # that a format test would also match, and following the wrong
            # candidate list wastes a request per candidate.  ``llm_format``
            # additionally yields on its own when the refusal names the
            # reasoning axis, but the ordering here is the primary guarantee —
            # do not reorder these two blocks.
            if extra_body and llm_reasoning.is_reasoning_rejection(exc):
                raise ReasoningUnsupported(str(exc)) from exc
            # A provider that refuses a dialect should fall back to the next.
            if llm_format.is_format_rejection(exc):
                if state.advance():
                    # The dialect was refused, not the request: this does not
                    # consume the retry budget.
                    continue
                # Every dialect was refused.  Report it once and keep going
                # with the prompt plus local validation rather than failing.
                if not announced_unsupported:
                    announced_unsupported = True
                    print(
                        "[llm] structured output unsupported; relying on the "
                        "prompt and local validation"
                    )
            elif _classify_llm_error(exc) == "bad_request" and state.advance():
                # A 400/422 that names no format field and no reasoning field
                # is still, almost always, a complaint about the shape of the
                # request — and ``response_format`` is the part of that shape
                # this module varies.  Advance one candidate (again without
                # spending retry budget) so a provider whose wording we do not
                # recognise degrades instead of aborting.  Once the walk is on
                # its last candidate there is nothing left to give up, so
                # ``advance`` returns False and the normal retry path resumes.
                continue
            attempt += 1

    raise RuntimeError(f"LLM completion failed: {last_error}")


def _looks_like_schema_rejection(exc: Exception) -> bool:
    """Deprecated alias of :func:`llm.format.is_format_rejection`.

    Kept because the narrower predicate now lives with the candidate list it
    belongs to, while callers (and older tests) may still reach for this name.
    """
    return llm_format.is_format_rejection(exc)


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
    ``network``, ``bad_reply``, ``bad_request``, or ``other``.  Only ``auth``
    is treated as fatal; the rest are worth another attempt.

    ``bad_request`` marks a 400/422 that no other rule claimed.  It exists so
    that :func:`_request_completion` can treat "the endpoint rejected this
    request shape for a reason we do not recognise" as one more reason to try
    the next structured-output candidate, rather than letting it burn the
    retry budget re-sending a request that is already known to be refused.
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
    if status in (400, 422):
        return "bad_request"
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
    exactly the mistake ``llm.check`` avoids by never caching transient
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
    cache: llm_cache.LlmCache | None = None,
) -> list[BoldEntry]:
    """Complete every pending non-word entry in place and return the list.

    Entries already marked ``filled`` are skipped, which makes re-running the
    pipeline idempotent.

    ``workers`` controls concurrency.  The default of 1 (or ``None``, which
    resolves to the configured value) keeps the historical strictly sequential
    behaviour.  Above 1 the requests are issued by a thread pool — but only the
    *request and parse* half moves off the main thread: entries are updated and
    messages are printed afterwards, on the calling thread.  No worker touches
    a file, because ``lookup.write_json`` rewrites
    ``.words.json``/``.errors.json`` with a read-merge-write that would race if
    several threads ran it at once.

    Every record is reported the moment its request settles, so a long run
    shows progress instead of a silent pause followed by one burst of lines.
    With several workers the completions arrive out of order; the lines are
    still ordered by input position, because the ``ready`` buffer holds a
    finished record until every earlier one has been reported.

    *cache*, when given, is consulted before every request and written after
    every fresh success.  A hit costs no request at all and is reported as
    ``ok (cache)`` so the source of the reply stays visible.

    ``use_schema`` is retained for backward compatibility only.  Setting it to
    ``False`` starts the dialect walk at its terminal entry, i.e. sends no
    ``response_format``; the default lets :mod:`llm.format` discover
    what the endpoint actually understands.
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
    # One state shared by every record, so a dialect that the first record
    # proves unsupported is never re-probed by the second.  Combined with the
    # process-wide memory inside llm_format, the probing cost is paid once per
    # process rather than once per record or once per run.
    schema_state = llm_format.SchemaState(
        start_index=0 if use_schema else len(llm_format.SCHEMA_CANDIDATES) - 1
    )

    def _one(
        index: int, entry: BoldEntry
    ) -> tuple[int, BoldEntry, dict | None, str, bool]:
        """Issue one request.  Deliberately free of any shared mutation.

        The trailing flag marks a payload that came from the cache rather than
        from a request, so the caller can say so and skip a pointless write.
        """
        if breaker.tripped:
            return (index, entry, None, "aborted", False)
        key = llm_cache.cache_key(entry.word, entry.type) if cache else ""
        if cache is not None:
            cached = cache.get(key)
            if cached is not None:
                return (index, entry, cached, "ok", True)
        while True:
            try:
                payload = _request_completion(
                    client,
                    model=model,
                    entry=entry,
                    schema_state=schema_state,
                    attempts=llm["retries"] + 1,
                    timeout=llm["timeout"],
                    backoff=llm["backoff"],
                    extra_body=reasoning.current(),
                    max_tokens=MAX_COMPLETION_TOKENS,
                )
            except LLMAbort as exc:
                breaker.trip(exc)
                return (index, entry, None, "fatal", False)
            except ReasoningUnsupported:
                # The endpoint does not know this field.  ``advance`` reports
                # whether a further candidate is left to try.
                if reasoning.advance():
                    continue
                return (index, entry, None, "reasoning_unsupported", False)
            except Exception as exc:  # noqa: BLE001 - one failure must not abort
                reason = _summarize_error(exc)
                if breaker.tripped:
                    return (index, entry, None, "aborted", False)
                breaker.record_failure()
                return (
                    index,
                    entry,
                    None,
                    "aborted" if breaker.tripped else reason,
                    False,
                )
            else:
                breaker.record_success()
                reasoning.confirm()
                return (index, entry, payload, "ok", False)

    def _apply_result(
        index: int,
        entry: BoldEntry,
        payload: dict | None,
        status: str,
        from_cache: bool = False,
    ) -> None:
        """Fold one settled result into its entry and report it.

        Called on the calling thread only, never from a worker: it is the one
        place that mutates ``entry`` and the one place that prints, so the two
        cannot interleave with another record's.  It is also the only place
        that writes the cache, for the same reason.
        """
        if status == "ok":
            entry.entry = _to_standard_entry(entry, payload)
            entry.code = CODE_OK
            entry.detail = ""
            entry.source = SOURCE_LLM
            suffix = " (cache)" if from_cache else ""
            print(f"  [llm {index}/{total}] {entry.word!r}: ok{suffix}")
            if cache is not None and not from_cache:
                cache.put(llm_cache.cache_key(entry.word, entry.type), payload)
        elif status == "aborted":
            print(f"  [llm {index}/{total}] {entry.word!r}: skipped (aborted)")
        elif status == "fatal":
            print(f"  [llm {index}/{total}] {entry.word!r}: skipped (fatal)")
        else:
            print(f"  [llm {index}/{total}] {entry.word!r}: failed ({status})")
            entry.detail = status

    if workers <= 1:
        # Sequential: report each record as soon as it settles.  This is the
        # path that makes a single-worker run feel responsive.
        for index, entry in enumerate(targets, start=1):
            _apply_result(index, *_one(index, entry)[1:])
    else:
        # Concurrent: ``as_completed`` yields results in completion order,
        # but the printed lines must stay in input order.  ``ready`` holds
        # finished results until every earlier position has been reported;
        # ``next_index`` is the position the next line must carry.
        ready: dict[int, tuple[BoldEntry, dict | None, str, bool]] = {}
        next_index = 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_one, index, entry): index
                for index, entry in enumerate(targets, start=1)
            }
            for future in as_completed(futures):
                index, entry, payload, status, from_cache = future.result()
                if index == next_index:
                    # In order: emit immediately, then drain whatever the
                    # buffer can now unblock.
                    _apply_result(index, entry, payload, status, from_cache)
                    next_index += 1
                    while next_index in ready:
                        (
                            buffered_entry,
                            buffered_payload,
                            buffered_status,
                            buffered_cache,
                        ) = ready.pop(next_index)
                        _apply_result(
                            next_index,
                            buffered_entry,
                            buffered_payload,
                            buffered_status,
                            buffered_cache,
                        )
                        next_index += 1
                else:
                    ready[index] = (entry, payload, status, from_cache)

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
    cache: llm_cache.LlmCache | None = None,
) -> list[BoldEntry]:
    """Run the LLM stage and rewrite ``.words.json`` / ``.errors.json``.

    *cache* is supplied by the CLI, which owns the ``--no-llm-cache`` and
    ``--llm-cache-path`` switches.  Omitting it means "do not cache" — this
    function never opens ``~/.cache`` on its own, so a library caller gets no
    hidden file side effect.

    The cache is saved before the JSON documents are rewritten: the
    completions are the part that cost money, and one late failure in the
    bookkeeping must not discard them.
    """
    from common.config import get_dict_slug

    resolved = complete_entries(entries, cache=cache)
    if cache is not None:
        cache.save()

    out_dir = Path(art.out_dir)
    write_json.write_documents(
        resolved,
        words_path=out_dir / art.words_json,
        errors_path=out_dir / art.errors_json,
        dict_slug=dict_slug or get_dict_slug(),
        llm_source=True,
    )
    return resolved