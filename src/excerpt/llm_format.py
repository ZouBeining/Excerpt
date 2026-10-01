r"""Structured-output dialects for OpenAI-compatible endpoints.

Advertised support for ``response_format`` varies far more than the OpenAI
documentation suggests.  Some endpoints honour the full JSON Schema
contract, others accept the object but reject the ``strict`` key that a later
spec revision added, others know only ``json_object``, and a few refuse the
field outright.  There is no capability endpoint to ask, so the shape has to
be discovered by trying.

This module therefore owns three things and nothing else:

* an ordered list of candidate ``response_format`` values;
* a predicate for "the format field was refused";
* a state machine that remembers the winner for the process.

The terminal candidate sends **no** ``response_format`` at all.  Because that
candidate cannot itself be refused on format grounds, a schema disagreement
can never abort a run — the prompt already spells out the required keys and
``llm._validate`` enforces them locally.

Nothing here imports the rest of ``excerpt``; the module is a leaf that only
needs the standard library, so it cannot create an import cycle.
"""

from __future__ import annotations

import copy
import re
import threading
from typing import Any

__all__ = [
    "DialectUnsupported",
    "PROMPT_VERSION",
    "SCHEMA_CANDIDATES",
    "SchemaState",
    "candidate_for",
    "is_format_rejection",
    "reset_probe_cache",
]


#: Bump whenever the prompt or the expected reply shape changes.  It is
#: folded into the LLM cache key (see ``llm_cache.cache_key``), so a single
#: bump invalidates every stored completion at once without touching the
#: cache file layout.
#:
#: 2 — sentences are analysed as patterns and their ``pos`` is pinned to
#:     ``sentence``; the sentence prompt and the ``pos``/``definition`` schema
#:     descriptions changed with it, so every stored sentence reply is stale.
PROMPT_VERSION = 2


class DialectUnsupported(Exception):
    """A ``response_format`` dialect was refused with a 400.

    Raised only *inside* the request loop, which treats it as "try the next
    candidate" rather than as a failure.  It therefore never leaves
    ``llm._request_completion``: having it never surface is what makes the
    stage impossible to abort over a schema disagreement.
    """


#: Stands in for the JSON schema until :func:`candidate_for` injects the real
#: one.  Using a placeholder keeps ``SCHEMA_CANDIDATES`` a plain, literal,
#: importable list instead of something that must be built at call time.
_SCHEMA_PLACEHOLDER = "__SCHEMA__"

#: Candidate ``response_format`` values, ordered by "most likely to be
#: honoured, and most useful when it is".
#:
#: The list is deliberately short.  Every extra candidate costs one failed
#: round trip the first time a new endpoint is met, and the terminal ``None``
#: already provides a floor that cannot be refused — so the walk is bounded
#: by usefulness, not by completeness.  Shapes that were considered and
#: rejected on purpose:
#:
#: * ``{"type": "json_schema"}`` with no ``json_schema`` sub-object — not a
#:   legal payload for any known endpoint, so it would only waste a request;
#: * ``guided_json`` / ``guided_choice`` — these are *separate top-level*
#:   fields (a vLLM extension), not alternative ``response_format`` values.
#:   Cramming them in here answers 400.  Supporting them would be a
#:   "field candidate" problem like :mod:`excerpt.llm_reasoning`, which is a
#:   different axis and out of scope;
#: * ``{"type": "text"}`` — equivalent to sending nothing, no extra signal;
#: * the early-beta top-level ``schema`` + ``strict`` pair — deprecated, and
#:   candidate 3 already covers the same ground.
SCHEMA_CANDIDATES: list[dict[str, Any] | None] = [
    # 1. The modern contract: the provider validates the reply against the
    #    schema server-side, so it cannot drift from the required keys.
    {
        "type": "json_schema",
        "json_schema": {
            "name": "excerpt_entry",
            "strict": True,
            "schema": _SCHEMA_PLACEHOLDER,
        },
    },
    # 2. The same, with strict off.  Several aggregators implement only the
    #    older draft and reject the extra "strict" key; one request reveals a
    #    difference that would otherwise cost every record.
    {
        "type": "json_schema",
        "json_schema": {
            "name": "excerpt_entry",
            "strict": False,
            "schema": _SCHEMA_PLACEHOLDER,
        },
    },
    # 3. Off-spec but seen in the wild: json_object carrying an inline
    #    schema.  Cheap to try, because an endpoint that ignores the extra
    #    key simply answers as if it were candidate 4.
    {"type": "json_object", "schema": _SCHEMA_PLACEHOLDER},
    # 4. Plain JSON-object mode.  It promises valid JSON, not the keys — the
    #    prompt still lists them and _validate enforces them locally.
    {"type": "json_object"},
    # 5. The terminal fallback: no response_format at all, prompt-only.
    #    This entry can never be refused on format grounds, which is what
    #    makes the stage impossible to abort over a schema disagreement.
    None,
]

#: Field and feature names that identify the ``response_format`` axis.
#:
#: This is the *necessary* condition of a format rejection: the error must
#: name a field this module actually sends, or an equivalent spelling of the
#: feature.  Anchoring on the name (rather than on the refusal verb) is what
#: keeps a generic "unsupported" from being mistaken for a format problem —
#: ``unsupported model`` and ``not supported in your region`` contain no field
#: name and so cannot match.
#:
#: ``structured[-_ ]?outputs?`` covers every separator and plural seen in the
#: wild at once.  The spellings that actually appear include
#: ``structured-outputs`` (OpenRouter's feature validator), ``structured_outputs``,
#: ``structured outputs``, ``structured output`` and ``structuredoutput``.
_FORMAT_FIELD_PATTERN = re.compile(
    r"(response_format"
    r"|json_schema"
    r"|json_object"
    r"|guided_json"
    r"|guided_choice"
    r"|structured[-_ ]?outputs?)",
    re.IGNORECASE,
)

#: The verb phrase a provider uses to refuse a feature.  Never used on its own
#: — it is always paired with a field name, either to claim a format rejection
#: or (in :data:`_REASONING_AXIS_PATTERN`) to yield one.
_FEATURE_PATTERN = (
    r"(does not (support|recognize|recognise|implement|accept)"
    r"|not (supported|recognised|recognized)"
    r"|unsupported|unrecognised|unrecognized"
    r"|no support for)"
)

#: A refusal verb pointed at the *reasoning* axis.  When this fires, the format
#: predicate must step aside: the two axes have separate candidate lists, and
#: following the wrong one costs a request per candidate.  This backs up the
#: ordering in ``llm._request_completion``, which already checks reasoning
#: first.
_REASONING_AXIS_PATTERN = re.compile(
    _FEATURE_PATTERN
    + r".{0,40}?(reasoning|thinking|chat_template_kwargs|reasoning_effort)",
    re.IGNORECASE | re.DOTALL,
)

#: Index of the dialect this process settled on.  ``None`` until a request
#: has actually succeeded — advancing past a candidate only proves it was
#: *refused*, which must never be mistaken for proof that the next one works.
_WORKING_INDEX: int | None = None

#: Guards ``_WORKING_INDEX`` and the per-run ``_index``.  Parallel workers
#: share one :class:`SchemaState`, so an unguarded counter would let two
#: threads disagree about which dialect is current.
_PROBE_LOCK = threading.Lock()


def candidate_for(index: int, schema: dict[str, Any]) -> dict[str, Any] | None:
    """Return candidate *index* with *schema* injected, or ``None``.

    A deep copy is returned because the module-level template stores a
    placeholder where the schema belongs; handing out the template itself
    would let one caller's schema leak into the next call.  An index outside
    the list yields ``None``, which is also the shape of the terminal
    candidate, so an over-run walk degrades into the safe fallback rather
    than raising.
    """
    if index < 0 or index >= len(SCHEMA_CANDIDATES):
        return None
    template = SCHEMA_CANDIDATES[index]
    if template is None:
        return None
    payload = copy.deepcopy(template)
    _inject_schema(payload, schema)
    return payload


def _inject_schema(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    """Replace every placeholder inside *payload* with *schema*, in place."""
    for key, value in payload.items():
        if value == _SCHEMA_PLACEHOLDER:
            payload[key] = copy.deepcopy(schema)
        elif isinstance(value, dict):
            _inject_schema(value, schema)


def is_format_rejection(exc: Exception) -> bool:
    """Return whether *exc* reads like "the format field is not supported".

    Two conditions, in order:

    1. the message must name a field this module sends — ``response_format``,
       ``json_schema``, ``json_object``, ``guided_json`` / ``guided_choice``,
       or "structured output(s)" in any separator or number
       (:data:`_FORMAT_FIELD_PATTERN`);
    2. the refusal must not be aimed at the reasoning axis
       (:data:`_REASONING_AXIS_PATTERN`), which has its own candidate list.

    Naming a field is a *necessary* condition, and that is the whole trick.  A
    provider may say ``unsupported``, ``403 forbidden``, ``does not support
    feature`` or nothing at all, but as long as the sentence carries the field
    name the walk should move on.  Conversely, a bare ``unsupported`` with no
    field name (``unsupported model``, ``not supported in your region``) is
    left alone, so the predicate stays narrower than "any 400".

    Note that the *spelling* of the refusal verb is deliberately not matched:
    OpenRouter's validator says ``does not support feature: structured-outputs``
    while another endpoint says ``unsupported_response_format``, and pinning
    the predicate to either wording is exactly the brittleness that let a
    hyphen slip past an earlier, marker-based version of this function.
    """
    message = str(exc)
    if not _FORMAT_FIELD_PATTERN.search(message):
        return False
    # A refusal aimed at the reasoning axis belongs to the other candidate
    # list, even when a format word also appears in the payload.
    if _REASONING_AXIS_PATTERN.search(message):
        return False
    return True


def reset_probe_cache() -> None:
    """Forget the remembered dialect.  Intended for tests."""
    global _WORKING_INDEX
    with _PROBE_LOCK:
        _WORKING_INDEX = None


class SchemaState:
    """Walk the candidate list, remembering the winner for the process.

    Mirrors :class:`excerpt.llm_reasoning.ReasoningState`, with one addition:
    every mutation is guarded by a lock, because ``complete_entries`` may
    drive several workers at once and an unguarded index would let two
    threads disagree about which dialect is current.

    The process-wide memory is not persisted to disk.  The correct spelling
    tracks the provider's current API, and a cached verdict from last month
    would pin the tool to a shape that may since have been dropped — the same
    reasoning that keeps ``llm_check`` from caching transient verdicts.
    """

    def __init__(self, *, start_index: int = 0) -> None:
        self._index = start_index
        with _PROBE_LOCK:
            known = _WORKING_INDEX
        if known is not None:
            self._index = known

    def index(self) -> int:
        """Return the index of the candidate to try next."""
        with _PROBE_LOCK:
            return self._index

    def current(self) -> dict[str, Any] | None:
        """Return the candidate template at the current index, or ``None``.

        The raw template is returned rather than a materialised payload, so
        the caller always goes through :func:`candidate_for` and the
        placeholder can never escape.
        """
        index = self.index()
        if index < 0 or index >= len(SCHEMA_CANDIDATES):
            return None
        return SCHEMA_CANDIDATES[index]

    def advance(self) -> bool:
        """Move to the next candidate; return whether the list is exhausted.

        ``False`` means every dialect was refused.  The index stays pinned on
        the terminal candidate so the next attempt sends no ``response_format``
        at all — the walk never runs off the end.
        """
        global _WORKING_INDEX
        with _PROBE_LOCK:
            if self._index < len(SCHEMA_CANDIDATES) - 1:
                self._index += 1
                return True
            # Every dialect was refused: remember that so later runs skip the
            # probing entirely and go straight to sending nothing.
            _WORKING_INDEX = len(SCHEMA_CANDIDATES) - 1
            return False

    def confirm(self) -> None:
        """Record the current candidate as the one this process should reuse.

        Called only after a request actually succeeded.  Advancing past a
        candidate proves it was *refused*, so the cache must not be seeded
        with an index that has not yet produced a reply.
        """
        global _WORKING_INDEX
        with _PROBE_LOCK:
            _WORKING_INDEX = self._index
