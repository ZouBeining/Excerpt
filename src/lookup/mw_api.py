r"""Merriam-Webster dictionary API client.

Responsibilities
----------------
* build the request URL and parameters from the configured API key;
* consult :mod:`lookup.cache` before spending a request;
* retry transient network failures (via :mod:`lookup.http`);
* classify the response into a :class:`LookupOutcome`;
* hand a raw payload to :mod:`lookup.mw_data` for cleaning.

Nothing here writes to ``{out_dir}`` — the client only returns data.

The three response shapes are documented in ``docs/api_response.template.json``
and are distinguished by inspecting the body, not just the HTTP status:

* a JSON array of entry *objects*  -> the word was found;
* a JSON array of *strings*        -> spelling suggestions (word not found);
* a plain-text string              -> an API-key problem.
"""

from __future__ import annotations

from typing import Any

import requests

from common.config import (
    CODE_BAD_RESPONSE,
    CODE_INVALID_API_KEY,
    CODE_MISSING_API_KEY,
    CODE_OK,
    CODE_WORD_NOT_FOUND,
)

from .cache import WordCache
from .http import build_session, request_json
from .mw_data import clean_entry, is_entry_shaped, looks_like_suggestions
from .outcome import LookupOutcome, classify_http_result, outcome_for_query_error

__all__ = ["API_URL", "lookup_raw", "lookup_word"]

#: Endpoint template; ``{reference}`` is the dataset, ``{word}`` the query.
API_URL = "https://dictionaryapi.com/api/v3/references/{reference}/json/{word}"

#: Default dataset (the Collegiate dictionary).
DEFAULT_REFERENCE = "collegiate"

#: MW rejects queries longer than this.
MAX_WORD_LENGTH = 64


def _build_url(word: str, reference: str) -> str:
    from urllib.parse import quote

    return API_URL.format(reference=reference, word=quote(str(word).strip(), safe=""))


def lookup_raw(
    word: str,
    *,
    session: requests.Session | None = None,
    api_key: str = "",
    cache: WordCache | None = None,
    reference: str = DEFAULT_REFERENCE,
    timeout: float | None = None,
    retry: int | None = None,
    delay: float | None = None,
) -> LookupOutcome:
    """Query MW for *word* and return a classified, still-raw outcome."""
    invalid = outcome_for_query_error(word, max_length=MAX_WORD_LENGTH)
    if invalid is not None:
        return invalid

    if not api_key:
        return LookupOutcome(
            CODE_MISSING_API_KEY,
            "DICT_API_KEY is not set",
            reached_network=False,
        )

    # 1. Cache first — a hit costs no request and no quota.
    if cache is not None:
        cached = cache.get_raw(word)
        if cached is not None:
            return _classify_raw(cached)

    # 2. Network.
    own_session = session is None
    session = session or build_session()
    try:
        result = request_json(
            session,
            _build_url(word, reference),
            params={"key": api_key},
            timeout=timeout,
            retry=retry,
            delay=delay,
        )
    finally:
        if own_session:
            session.close()

    # A successful HTTP response still needs shape inspection: MW answers 200
    # with a *string* for an auth problem and with an array of *strings* for
    # spelling suggestions.  Both are handled by ``_classify_raw``, which is
    # also the cache path, so the two stay consistent.
    outcome = _classify_raw(result.body if result.ok else None)
    if not result.ok:
        outcome = classify_http_result(result)

    if cache is not None:
        cache.put_raw(word, outcome.code, outcome.raw)

    return outcome


def _classify_raw(raw: Any) -> LookupOutcome:
    """Classify an already-fetched (possibly cached) raw payload."""
    if raw is None:
        return LookupOutcome(CODE_WORD_NOT_FOUND, "empty cached payload")
    if isinstance(raw, str):
        return classify_http_result(_FakeResult(raw))
    if looks_like_suggestions(raw):
        return LookupOutcome(
            CODE_WORD_NOT_FOUND,
            f"not found; suggestions: {', '.join(raw[:5])}",
            raw=raw,
        )
    if isinstance(raw, list) and any(is_entry_shaped(item) for item in raw):
        return LookupOutcome(CODE_OK, "", raw=raw, payload=raw)
    return LookupOutcome(CODE_BAD_RESPONSE, "unrecognised payload shape", raw=raw)


class _FakeResult:
    """Adapter so cached plain-text bodies reuse :func:`classify_http_result`."""

    __slots__ = ("ok", "status", "body", "code", "detail")

    def __init__(self, body: str):
        self.ok = True
        self.status = 200
        self.body = body
        self.code = CODE_BAD_RESPONSE
        self.detail = ""


def lookup_word(
    word: str,
    *,
    session: requests.Session | None = None,
    api_key: str = "",
    cache: WordCache | None = None,
    reference: str = DEFAULT_REFERENCE,
    timeout: float | None = None,
    retry: int | None = None,
    delay: float | None = None,
) -> tuple[LookupOutcome, dict[str, Any]]:
    """Query MW for *word* and return ``(outcome, standard_entry)``.

    The standard entry is always present: on failure it is the empty entry from
    :func:`common.config.empty_entry`.
    """
    outcome = lookup_raw(
        word,
        session=session,
        api_key=api_key,
        cache=cache,
        reference=reference,
        timeout=timeout,
        retry=retry,
        delay=delay,
    )

    if outcome.code != CODE_OK:
        return outcome, clean_entry({}, word=word)

    payload = outcome.payload
    chosen: dict[str, Any] = {}
    if isinstance(payload, list):
        for item in payload:
            if is_entry_shaped(item):
                chosen = item
                break

    return outcome, clean_entry(chosen, word=word)
