r"""Shared HTTP plumbing for the dictionary API clients.

Everything network-related that is *not* specific to one dictionary lives
here: proxy configuration, session construction, retry/backoff, and the
translation of ``requests`` exceptions into status codes.  Each
``{dict_slug}_api.py`` owns only its own URL, parameters and response
classification.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from common.config import (
    CODE_BAD_RESPONSE,
    CODE_LOOKUP_FAILED,
    CODE_RATE_LIMITED,
    get_session_settings,
)

__all__ = ["HttpResult", "build_session", "request_json"]

#: HTTP statuses that mean "slow down", not "broken".
_RATE_LIMIT_STATUSES = frozenset({429, 503})


class HttpResult:
    """Outcome of one HTTP attempt, whether or not it succeeded."""

    __slots__ = ("ok", "status", "body", "code", "detail")

    def __init__(
        self,
        *,
        ok: bool,
        status: int = 0,
        body: Any = None,
        code: int = CODE_LOOKUP_FAILED,
        detail: str = "",
    ):
        self.ok = ok
        self.status = status
        self.body = body
        self.code = code
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"HttpResult(ok={self.ok!r}, status={self.status!r}, "
            f"code={self.code!r}, detail={self.detail!r})"
        )


def build_session(proxy: str | None = None) -> requests.Session:
    """Build a :class:`requests.Session`, applying a proxy when configured."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "excerpt/0.1 (+https://github.com/excerpt)",
            "Accept": "application/json",
        }
    )
    resolved = (proxy or get_session_settings().get("proxy") or "").strip()
    if resolved:
        session.proxies.update({"http": resolved, "https": resolved})
    return session


def request_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
    retry: int | None = None,
    delay: float | None = None,
) -> HttpResult:
    """``GET`` *url* and decode JSON, retrying transient failures.

    A network error or a 5xx/429 response is retried with linear backoff.  When
    every attempt fails the caller still receives a classified
    :class:`HttpResult` — this function never raises, so a dead network degrades
    into an error record rather than an aborted run.
    """
    settings = get_session_settings()
    resolved_timeout = float(
        timeout if timeout is not None else settings.get("timeout", 20.0)
    )
    attempts = max(1, int(retry if retry is not None else settings.get("retry", 3)) + 1)
    wait = float(delay if delay is not None else settings.get("delay", 0.2))

    last = HttpResult(ok=False, code=CODE_LOOKUP_FAILED, detail="no attempt made")

    for attempt in range(attempts):
        if attempt:
            time.sleep(max(wait, 0.0) * attempt)
        try:
            response = session.get(
                url, params=params, timeout=resolved_timeout
            )
        except requests.exceptions.Timeout as exc:
            last = HttpResult(
                ok=False,
                code=CODE_LOOKUP_FAILED,
                detail=f"timeout after {resolved_timeout}s: {exc}",
            )
            continue
        except requests.exceptions.RequestException as exc:
            last = HttpResult(
                ok=False,
                code=CODE_LOOKUP_FAILED,
                detail=f"network error: {exc}",
            )
            continue

        status = int(response.status_code)

        if status in _RATE_LIMIT_STATUSES:
            last = HttpResult(
                ok=False,
                status=status,
                code=CODE_RATE_LIMITED,
                detail=f"HTTP {status}",
            )
            continue

        if status >= 500:
            last = HttpResult(
                ok=False,
                status=status,
                code=CODE_LOOKUP_FAILED,
                detail=f"HTTP {status}",
            )
            continue

        if status >= 400 and status != 404:
            return HttpResult(
                ok=False,
                status=status,
                code=CODE_BAD_RESPONSE,
                detail=f"HTTP {status}",
            )

        try:
            body = response.json()
        except ValueError:
            # Not JSON at all: the API is probably returning a plain-text
            # error message (e.g. an invalid key).  Classified downstream.
            return HttpResult(
                ok=True,
                status=status,
                body=response.text,
                detail="non-JSON response",
            )

        return HttpResult(ok=True, status=status, body=body)

    return last
