r"""Free Dictionary (UApiPro) client.

Endpoint: ``GET https://uapis.cn/api/v1/dictionary/lookup``
Query: ``word`` (required), ``lang`` (optional, ``en`` only), ``engine``
(optional, ``local`` for the low-latency path).

Response shapes
---------------
* ``200`` with ``{"found": true, "entry": {...}}`` -> found;
* ``200`` with ``{"found": false, ...}``            -> not found;
* ``404`` / ``502`` with ``{"code": ..., "message": ...}`` -> not found / down;
* ``400`` for an empty or over-long word.

The endpoint needs no API key, so ``DICT_API_KEY`` is ignored for this
dictionary.
"""

from __future__ import annotations

from typing import Any

import requests

from common.config import (
    CODE_BAD_RESPONSE,
    CODE_LOOKUP_FAILED,
    CODE_RATE_LIMITED,
    CODE_WORD_NOT_FOUND,
)

from .cache import WordCache
from .fd_data import clean_entry, is_entry_shaped
from .http import build_session, request_json
from .outcome import LookupOutcome, classify_http_result, outcome_for_query_error

__all__ = ["API_URL", "lookup_raw", "lookup_word"]

API_URL = "https://uapis.cn/api/v1/dictionary/lookup"

#: The endpoint documents a 64-character cap, matching MW.
MAX_WORD_LENGTH = 64

#: Error codes the API can return in a JSON body.
_BODY_ERROR_CODES: dict[str, tuple[int, str]] = {
    "WORD_NOT_FOUND": (CODE_WORD_NOT_FOUND, "no definition found"),
    "SERVICE_UNAVAILABLE": (
        CODE_RATE_LIMITED,
        "lookup service temporarily unavailable",
    ),
    "INVALID_PARAMETER": (CODE_BAD_RESPONSE, "invalid parameter"),
}


def lookup_raw(
    word: str,
    *,
    session: requests.Session | None = None,
    api_key: str = "",
    cache: WordCache | None = None,
    engine: str = "",
    lang: str = "en",
    timeout: float | None = None,
    retry: int | None = None,
    delay: float | None = None,
) -> LookupOutcome:
    """Query UApiPro for *word* and return a classified, still-raw outcome."""
    invalid = outcome_for_query_error(word, max_length=MAX_WORD_LENGTH)
    if invalid is not None:
        return invalid

    if cache is not None:
        cached = cache.get_raw(word)
        if cached is not None:
            return _classify_payload(cached)

    own_session = session is None
    session = session or build_session()
    params: dict[str, Any] = {"word": str(word).strip(), "lang": lang}
    if engine:
        params["engine"] = engine

    try:
        result = request_json(
            session,
            API_URL,
            params=params,
            timeout=timeout,
            retry=retry,
            delay=delay,
        )
    finally:
        if own_session:
            session.close()

    outcome = classify_http_result(result)
    # ``classify_http_result`` treats a 404 as not-found, which is right here.
    if outcome.code == CODE_WORD_NOT_FOUND and result.status == 502:
        outcome = LookupOutcome(
            CODE_LOOKUP_FAILED, "HTTP 502", raw=result.body
        )

    if outcome.code == CODE_OK:
        outcome = _classify_payload(outcome.raw)

    if cache is not None:
        cache.put_raw(word, outcome.code, outcome.raw)

    return outcome


def _classify_payload(payload: Any) -> LookupOutcome:
    """Interpret a decoded UApiPro body, cached or fresh."""
    if payload is None:
        return LookupOutcome(CODE_WORD_NOT_FOUND, "empty payload")

    if isinstance(payload, str):
        return LookupOutcome(CODE_BAD_RESPONSE, payload.strip()[:200], raw=payload)

    if not isinstance(payload, dict):
        return LookupOutcome(CODE_BAD_RESPONSE, "unexpected payload type", raw=payload)

    # Explicit error envelope.
    api_code = str(payload.get("code") or "").strip().upper()
    if api_code:
        code, detail = _BODY_ERROR_CODES.get(
            api_code, (CODE_LOOKUP_FAILED, api_code.lower())
        )
        return LookupOutcome(code, detail, raw=payload)

    if payload.get("found") is False:
        return LookupOutcome(CODE_WORD_NOT_FOUND, "found=false", raw=payload)

    entry = payload.get("entry")
    if is_entry_shaped(entry):
        return LookupOutcome(CODE_OK, "", raw=payload, payload=entry)

    return LookupOutcome(CODE_WORD_NOT_FOUND, "no entry in payload", raw=payload)


def lookup_word(
    word: str,
    *,
    session: requests.Session | None = None,
    api_key: str = "",
    cache: WordCache | None = None,
    engine: str = "",
    lang: str = "en",
    timeout: float | None = None,
    retry: int | None = None,
    delay: float | None = None,
) -> tuple[LookupOutcome, dict[str, Any]]:
    """Query UApiPro for *word* and return ``(outcome, standard_entry)``."""
    outcome = lookup_raw(
        word,
        session=session,
        api_key=api_key,
        cache=cache,
        engine=engine,
        lang=lang,
        timeout=timeout,
        retry=retry,
        delay=delay,
    )

    if outcome.code != CODE_OK or not is_entry_shaped(outcome.payload):
        return outcome, clean_entry({}, word=word)

    return outcome, clean_entry(outcome.payload, word=word)
