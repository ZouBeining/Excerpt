r"""Suppress "thinking" on OpenAI-compatible reasoning models.

A reasoning model spends a large, unpredictable number of tokens on a chain of
thought before it writes the answer.  For this task — a six-field structured
gloss — that work is pure waste: the schema has nowhere to put it, so the
tokens are billed, discarded, and add a long delay to every record.

There is no portable way to switch it off.  Whether a provider thinks by
default, and which field turns it off, are both vendor extensions that live
outside the OpenAI Chat Completions specification:

* OpenRouter       — ``reasoning: {enabled: false}``
* DeepSeek / vLLM  — ``chat_template_kwargs: {thinking: false}``
* Qwen / DashScope — ``chat_template_kwargs: {enable_thinking: false}``
* OpenAI o-series  — ``reasoning_effort: "minimal"``

So this module does not try to detect the vendor.  It offers a short list of
candidate payloads, ordered by how likely each is to be understood, and lets
the caller walk that list until one is accepted.

The winning candidate is remembered **in memory only**.  Persisting it would
be a mistake: the correct field tracks the provider's current API, and a
cached verdict from last month would pin the tool to a spelling that has since
been dropped — the same reasoning that keeps ``llm_check`` from caching its
transient verdicts.

Nothing here imports the rest of ``excerpt``; the module is a leaf that only
needs ``common.config`` types, so it cannot create an import cycle.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "REASONING_CANDIDATES",
    "ReasoningState",
    "is_reasoning_rejection",
    "reset_probe_cache",
]


#: Candidate ``extra_body`` fragments, ordered by how widely they are honoured.
#: Add a new spelling here when a provider appears that needs one — this list is
#: the single place that knows about vendor dialects.
REASONING_CANDIDATES: list[dict[str, Any]] = [
    {"reasoning": {"enabled": False}},                     # OpenRouter
    {"chat_template_kwargs": {"thinking": False}},          # DeepSeek / vLLM
    {"chat_template_kwargs": {"enable_thinking": False}},   # Qwen / DashScope
    {"reasoning_effort": "minimal"},                        # OpenAI o-series
]

#: Markers that mean "this endpoint does not recognise the field you sent".
#: Kept separate from ``llm._looks_like_schema_rejection`` because the two
#: probe *different* extensions and must not be confused with each other.
_REJECTION_MARKERS = (
    "unknown parameter",
    "unrecognized",
    "unrecognised",
    "unsupported parameter",
    "extra fields not permitted",
    "extra_forbidden",
    "invalid_request_error",
)

#: Probe result for the *process*: the index of the candidate that worked, or
#: ``-1`` once every candidate has been rejected.  ``None`` means "not yet
#: determined".
_WORKING_CANDIDATE: int | None = None


def is_reasoning_rejection(exc: Exception) -> bool:
    """Return whether *exc* reads like "that reasoning field is not accepted".

    Providers answer unknown request fields inconsistently — 400 with an
    ``unknown parameter`` note, a pydantic ``extra_forbidden``, or a bare
    ``invalid_request_error`` — so this is a substring hunt, not a status-code
    check.  A 401 never matches, which is what keeps a genuine auth failure
    from being mistaken for a dialect problem.
    """
    message = str(exc).lower()
    return any(marker in message for marker in _REJECTION_MARKERS)


def reset_probe_cache() -> None:
    """Forget the remembered candidate.  Intended for tests."""
    global _WORKING_CANDIDATE
    _WORKING_CANDIDATE = None


class ReasoningState:
    """Walk the candidate list for one ``complete_entries`` run.

    The run-level object defers to the process-level ``_WORKING_CANDIDATE``:
    once any run has found a working spelling, every later run starts there
    instead of re-probing, so the cost is paid once per process rather than
    once per record.
    """

    def __init__(self, override: dict[str, Any] | None = None):
        self.candidates: list[dict[str, Any] | None] = (
            self._build_candidates(override)
        )
        self._index = 0
        if _WORKING_CANDIDATE is not None and _WORKING_CANDIDATE >= 0:
            if _WORKING_CANDIDATE < len(self.candidates):
                self._index = _WORKING_CANDIDATE

    @staticmethod
    def _build_candidates(
        override: dict[str, Any] | None,
    ) -> list[dict[str, Any] | None]:
        """Return the candidate list, with an explicit override leading it.

        An ``LLM_REASONING`` override is tried first because the user knows
        which provider they are talking to.  The built-ins stay behind it as a
        fallback, so a typo in the override degrades into a working guess
        instead of silently disabling the feature.
        """
        candidates: list[dict[str, Any] | None] = []
        if override:
            candidates.append(dict(override))
        for candidate in REASONING_CANDIDATES:
            if candidate not in candidates:
                candidates.append(candidate)
        # A final "send nothing" entry: some endpoints reject every dialect and
        # are happy only with the plainest request.  The token ceiling then has
        # to do the containment work on its own.
        candidates.append(None)
        return candidates

    def current(self) -> dict[str, Any] | None:
        """Return the ``extra_body`` fragment to send with the next request."""
        if self._index >= len(self.candidates):
            return None
        return self.candidates[self._index]

    def advance(self) -> bool:
        """Move to the next candidate; return whether another one exists."""
        self._index += 1
        if self._index >= len(self.candidates):
            # Every dialect was refused: remember that so later runs skip the
            # probing entirely and go straight to sending no field at all.
            global _WORKING_CANDIDATE
            _WORKING_CANDIDATE = -1
            return False
        return True

    def confirm(self) -> None:
        """Record the current candidate as the one this process should reuse.

        Called after a request actually succeeds — advancing past a candidate
        only proves it was *rejected*, so the cache must not be seeded with an
        index that has not yet produced a reply.
        """
        global _WORKING_CANDIDATE
        _WORKING_CANDIDATE = self._index
