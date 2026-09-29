r"""Standardised HTTP result classification.

The three shapes an API can return — a real payload, a not-found body, or an
auth problem — are turned into the shared :class:`LookupOutcome` here, so that
every ``{dict_slug}_api.py`` reports failures the same way and the rest of the
pipeline never has to know which dictionary produced the data.
"""

from __future__ import annotations

from typing import Any

from common.config import (
    CODE_BAD_RESPONSE,
    CODE_INVALID_API_KEY,
    CODE_INVALID_QUERY,
    CODE_LOOKUP_FAILED,
    CODE_MISSING_API_KEY,
    CODE_OK,
    CODE_WORD_NOT_FOUND,
)

from .http import HttpResult

__all__ = ["LookupOutcome", "classify_http_result"]


class LookupOutcome:
    """A classified dictionary response.

    Attributes
    ----------
    code:
        One of the ``CODE_*`` constants from :mod:`common.config`.
    detail:
        Free-form human-readable note; ends up in ``.errors.json``.
    raw:
        The untouched API payload, suitable for the on-disk cache.
    payload:
        The part of the payload that describes a single entry, still in the
        dictionary's own shape (cleaned later by ``{dict_slug}_data.py``).
    """

    __slots__ = ("code", "detail", "payload", "raw", "reached_network")

    def __init__(
        self,
        code: int,
        detail: str = "",
        *,
        raw: Any = None,
        payload: Any = None,
        reached_network: bool = True,
    ):
        self.code = code
        self.detail = detail
        self.raw = raw
        self.payload = payload
        self.reached_network = reached_network

    @property
    def ok(self) -> bool:
        return self.code == CODE_OK

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"LookupOutcome(code={self.code!r}, detail={self.detail!r})"


def classify_http_result(
    result: HttpResult,
    *,
    not_found_marker: str = "",
) -> LookupOutcome:
    """Split a raw :class:`HttpResult` into the three canonical outcomes.

    ``not_found_marker`` is a substring whose presence in a *successful* body
    means "the dictionary does not have this word"; dictionaries whose API does
    not need one pass ``""``.
    """
    if not result.ok:
        return LookupOutcome(
            result.code or CODE_LOOKUP_FAILED,
            result.detail or "request failed",
            reached_network=result.status != 0,
        )

    body = result.body

    # A plain-text body is how most dictionary APIs report auth problems.
    if isinstance(body, str):
        lowered = body.strip().lower()
        if not lowered:
            return LookupOutcome(
                CODE_BAD_RESPONSE, "empty response body", raw=body
            )
        if "key" in lowered and (
            "invalid" in lowered or "required" in lowered or "not subscribed" in lowered
        ):
            code = (
                CODE_MISSING_API_KEY
                if "required" in lowered
                else CODE_INVALID_API_KEY
            )
            return LookupOutcome(code, body.strip()[:200], raw=body)
        return LookupOutcome(CODE_BAD_RESPONSE, body.strip()[:200], raw=body)

    if result.status == 404:
        return LookupOutcome(
            CODE_WORD_NOT_FOUND,
            not_found_marker or f"HTTP {result.status}",
            raw=body,
        )

    if body is None or body == [] or body == {}:
        return LookupOutcome(CODE_WORD_NOT_FOUND, "empty result", raw=body)

    return LookupOutcome(CODE_OK, "", raw=body, payload=body)


def outcome_for_query_error(
    word: str,
    *,
    max_length: int = 64,
) -> LookupOutcome | None:
    """Return an ``INVALID_QUERY`` outcome when *word* is obviously unqueryable.

    Catches the cases the API would reject anyway, so no request is spent.
    """
    text = str(word).strip()
    if not text:
        return LookupOutcome(
            CODE_INVALID_QUERY, "empty word", reached_network=False
        )
    if len(text) > max_length:
        return LookupOutcome(
            CODE_INVALID_QUERY,
            f"word longer than {max_length} characters: {text!r}",
            reached_network=False,
        )
    return None
