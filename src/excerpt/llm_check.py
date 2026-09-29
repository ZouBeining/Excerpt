r"""Preflight check for the LLM configuration.

When ``USE_LLM`` is on, the pipeline will spend real quota on the completion
stage.  A typo in the API key, the base URL or the model name would otherwise
surface only after the extractor and the dictionary lookup have already run —
wasting time and dictionary quota before failing on the first completion.

This module answers one question *before* the pipeline starts: does the
configured endpoint accept a minimal Chat Completions request?

Design notes
------------
* The check uses :mod:`requests` directly, not the ``openai`` SDK, so it can
  report the raw HTTP status (401 vs 404 vs 429) instead of an SDK-specific
  exception.  That distinction is what lets the caller tell an invalid key
  apart from a momentarily unreachable endpoint.
* A **valid** verdict is cached in ``~/.cache/excerpt/llm.check.json`` and
  reused while the configuration is unchanged, so repeated runs do not burn
  request quota on the probe.  An **invalid** verdict is deliberately *not*
  cached: the fix is usually to correct the config, and a stale failure must
  never outlive the correction.
* The cache key is a fingerprint of ``base_url`` + ``model`` + a SHA-256 of the
  API key.  The key itself is never written to disk — only its hash — so the
  cache file does not become a second copy of the secret.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import requests

from common.config import get_openai_settings, get_session_settings

__all__ = [
    "CHECK_VERSION",
    "CheckResult",
    "check_llm_config",
    "config_fingerprint",
    "default_check_path",
    "read_check_cache",
    "write_check_cache",
]

#: Bump when the fingerprint inputs or the meaning of a cached verdict change.
#: Bumped to 2 when the probe payload gained its reasoning-suppression field:
#: the payload is deliberately not part of the cache key, so the version bump
#: is what forces a re-probe and keeps an old "verified" verdict from standing
#: in for a request the provider has not actually accepted.
CHECK_VERSION = 2

#: Endpoint appended to ``OPENAI_BASE_URL`` for the probe.
_COMPLETIONS_PATH = "/chat/completions"


def _completions_endpoint(base_url: str) -> str:
    """Append the completions path unless *base_url* already ends with it.

    ``get_openai_settings`` normalises the value, but this function is also
    reachable with a raw URL (tests, direct calls), so the guard is kept here
    rather than assumed.
    """
    text = str(base_url or "").strip().rstrip("/")
    if text.lower().endswith(_COMPLETIONS_PATH):
        return text
    return text + _COMPLETIONS_PATH

#: Small, cheap probe payload: one token, no schema, no streaming.
#:
#: ``reasoning`` is sent so the probe exercises the *same* shape as a real
#: completion.  Without it, a reasoning model would spend a chain of thought on
#: a request that asked for one token, which is slow, bills tokens nobody reads,
#: and — on a busy free tier — is far likelier to come back 429.  A throttled
#: probe is never cached, so each throttled run would pay for another probe.
#:
#: Only this one field is sent.  The vendor ``chat_template_kwargs`` spellings
#: are left to the completion stage's candidate fallback, because a provider
#: that does not recognise them answers 400, and a bare 400 is classified as a
#: fatal configuration error.
_PROBE_PAYLOAD: dict[str, Any] = {
    "messages": [
        {"role": "user", "content": "Reply with the single word: ok"},
    ],
    "max_tokens": 1,
    "temperature": 0,
    "reasoning": {"enabled": False},
}


def default_check_path() -> Path:
    """Return ``~/.cache/excerpt/llm.check.json``.

    The directory matches the dictionary cache so everything the tool writes
    lives in one place.
    """
    base = os.environ.get("EXCERPT_CACHE_DIR")
    if base:
        root = Path(base).expanduser()
    else:
        root = Path.home() / ".cache" / "excerpt"
    return root / "llm.check.json"


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def config_fingerprint(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> str:
    """Return a stable fingerprint of the LLM configuration.

    The API key is folded in as a SHA-256 digest, never verbatim: the
    fingerprint is persisted, and storing a plaintext key would leak it into a
    cache file that users are unlikely to think of as sensitive.
    """
    resolved_key, resolved_url, resolved_model = get_openai_settings()
    key = api_key if api_key is not None else resolved_key
    url = base_url if base_url is not None else resolved_url
    name = model if model is not None else resolved_model

    digest = hashlib.sha256(str(key or "").encode("utf-8")).hexdigest()[:16]
    material = "|".join(
        (
            str(CHECK_VERSION),
            str(url or "").strip().rstrip("/"),
            str(name or "").strip(),
            digest,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------

class CheckResult:
    """Outcome of the preflight check."""

    __slots__ = ("ok", "code", "reason", "cached", "status", "transient")

    def __init__(
        self,
        *,
        ok: bool,
        code: str = "",
        reason: str = "",
        cached: bool = False,
        status: int = 0,
        transient: bool = False,
    ):
        self.ok = ok
        self.code = code
        self.reason = reason
        self.cached = cached
        self.status = status
        #: True when the configuration is *valid* but the provider is
        #: temporarily unable to serve it (a 429 or 5xx).  Such a result is
        #: not a configuration error, so the caller must warn rather than
        #: abort — otherwise a busy free tier would block every run.
        self.transient = transient

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"CheckResult(ok={self.ok!r}, code={self.code!r}, "
            f"transient={self.transient!r}, cached={self.cached!r}, "
            f"reason={self.reason!r})"
        )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def read_check_cache(
    fingerprint: str,
    *,
    path: str | Path | None = None,
) -> CheckResult | None:
    """Return a cached *valid* verdict for *fingerprint*, or ``None``.

    Only ``ok`` verdicts are stored, so a hit always means "this exact config
    was verified working".  A missing, corrupt or mismatched file is a miss.
    """
    target = Path(path) if path is not None else default_check_path()
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("version") != CHECK_VERSION:
        return None
    record = raw.get("checks")
    if not isinstance(record, dict):
        return None
    entry = record.get(fingerprint)
    if not isinstance(entry, dict) or not entry.get("ok"):
        return None
    return CheckResult(
        ok=True,
        code="OK",
        reason="reused a cached verification",
        cached=True,
        status=int(entry.get("status") or 200),
    )


def write_check_cache(
    fingerprint: str,
    result: CheckResult,
    *,
    path: str | Path | None = None,
) -> None:
    """Record a *validated* verdict atomically; failures are not stored.

    A transient verdict (429/5xx) is deliberately **not** cached either: the
    configuration is fine, but nothing was actually verified, and caching it
    would let one throttled probe stand in for every later run.

    Failures are ignored on purpose: a cache that cannot be written must never
    turn a working configuration into a hard error.
    """
    if not result.ok or result.transient:
        return

    target = Path(path) if path is not None else default_check_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    existing: dict[str, Any] = {}
    if target.is_file():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("version") == CHECK_VERSION:
                if isinstance(loaded.get("checks"), dict):
                    existing = loaded["checks"]
        except (ValueError, OSError):
            existing = {}

    existing[fingerprint] = {
        "ok": True,
        "status": int(result.status or 200),
        "fingerprint": fingerprint,
    }
    payload = {"version": CHECK_VERSION, "checks": existing}
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, target)
    except OSError:
        Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------

def _error_detail(body: Any) -> str:
    """Pull the most useful human-readable message out of an error body.

    Aggregators such as OpenRouter nest the upstream provider's message inside
    ``error.metadata.raw`` (a JSON *string*), so the top-level message is
    usually a useless ``"Provider returned error"``.  Dig the nested one out
    when it is present.
    """
    if not isinstance(body, dict):
        return str(body).strip() if isinstance(body, str) else ""

    error = body.get("error")
    if not isinstance(error, dict):
        return str(error).strip() if error else ""

    metadata = error.get("metadata")
    if isinstance(metadata, dict):
        raw = metadata.get("raw")
        if isinstance(raw, str) and raw.strip():
            try:
                nested = json.loads(raw)
            except ValueError:
                return raw.strip()
            if isinstance(nested, dict):
                inner = nested.get("error")
                if isinstance(inner, dict) and inner.get("message"):
                    return str(inner["message"]).strip()

    return str(error.get("message") or "").strip()


def _classify(status: int, body: Any) -> CheckResult:
    """Turn an HTTP status into a verdict with an actionable reason."""
    if 200 <= status < 300:
        return CheckResult(ok=True, code="OK", reason="configuration accepted", status=status)
    if status == 401:
        return CheckResult(
            ok=False,
            code="INVALID_API_KEY",
            reason=f"HTTP 401: the API key was rejected",
            status=status,
        )
    if status == 403:
        return CheckResult(
            ok=False,
            code="FORBIDDEN",
            reason="HTTP 403: the key is valid but lacks access to this model",
            status=status,
        )
    if status == 404:
        return CheckResult(
            ok=False,
            code="BAD_ENDPOINT_OR_MODEL",
            reason=(
                "HTTP 404: check OPENAI_BASE_URL and OPENAI_MODEL "
                "(the endpoint or the model does not exist)"
            ),
            status=status,
        )
    if status == 429:
        return CheckResult(
            ok=True,
            code="RATE_LIMITED",
            reason="HTTP 429: the endpoint is reachable but rate-limited; retry later",
            status=status,
            transient=True,
        )
    if status >= 500:
        return CheckResult(
            ok=True,
            code="PROVIDER_ERROR",
            reason=f"HTTP {status}: the provider reported a server error",
            status=status,
            transient=True,
        )
    if status == 400:
        detail = _error_detail(body)
        # A 400 that carries a *provider* error means the request reached the
        # gateway and the key was accepted, but the upstream model refused to
        # serve it — a region lock, a deprecated free tier, a content policy
        # rejection.  That is not something the user can fix by editing their
        # key, so it must warn rather than abort; a bare 400 with no provider
        # context is a malformed request and stays fatal.
        lowered = detail.lower()
        provider_side = bool(detail) and (
            "provider returned error" in lowered
            or "not supported" in lowered
            or "failed_precondition" in lowered
            or "unsupported" in lowered
        )
        if provider_side:
            return CheckResult(
                ok=True,
                code="PROVIDER_REJECTED",
                reason=f"HTTP 400: the model refused the request ({detail})",
                status=status,
                transient=True,
            )
        suffix = f" ({detail})" if detail else ""
        return CheckResult(
            ok=False,
            code="BAD_REQUEST",
            reason=f"HTTP 400: the endpoint rejected the request{suffix}",
            status=status,
        )
    detail = _error_detail(body)
    suffix = f" ({detail})" if detail else ""
    return CheckResult(
        ok=False,
        code="BAD_RESPONSE",
        reason=f"HTTP {status}: unexpected response{suffix}",
        status=status,
    )


def check_llm_config(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    use_cache: bool = True,
    cache_path: str | Path | None = None,
    session: requests.Session | None = None,
) -> CheckResult:
    """Verify that the configured LLM endpoint accepts a request.

    Returns a :class:`CheckResult`; it never raises, so the caller can decide
    how loud to be.  When *use_cache* is set and the same configuration was
    already verified, the cached verdict is returned without any request.
    """
    settings = get_session_settings()
    resolved_key, resolved_url, resolved_model = get_openai_settings()
    key = api_key if api_key is not None else resolved_key
    url = (base_url if base_url is not None else resolved_url or "").strip()
    name = (model if model is not None else resolved_model or "").strip()

    if not key:
        return CheckResult(
            ok=False,
            code="MISSING_API_KEY",
            reason="OPENAI_API_KEY is not set but USE_LLM is enabled",
        )
    if not url:
        return CheckResult(
            ok=False,
            code="MISSING_BASE_URL",
            reason="OPENAI_BASE_URL is not set but USE_LLM is enabled",
        )
    if not name:
        return CheckResult(
            ok=False,
            code="MISSING_MODEL",
            reason="OPENAI_MODEL is not set but USE_LLM is enabled",
        )

    fingerprint = config_fingerprint(api_key=key, base_url=url, model=name)
    if use_cache:
        cached = read_check_cache(fingerprint, path=cache_path)
        if cached is not None:
            return cached

    resolved_timeout = float(
        timeout if timeout is not None else settings.get("timeout", 20.0)
    )
    endpoint = _completions_endpoint(url)

    owns_session = session is None
    if session is None:
        from lookup.http import build_session

        session = build_session()

    try:
        response = session.post(
            endpoint,
            json={**_PROBE_PAYLOAD, "model": name},
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=resolved_timeout,
        )
    except requests.exceptions.Timeout:
        return CheckResult(
            ok=False,
            code="TIMEOUT",
            reason=f"no reply from {endpoint} within {resolved_timeout}s",
        )
    except requests.exceptions.RequestException as exc:
        return CheckResult(
            ok=False,
            code="NETWORK_ERROR",
            reason=f"could not reach {endpoint}: {exc}",
        )
    finally:
        if owns_session:
            session.close()

    try:
        body: Any = response.json()
    except ValueError:
        body = response.text

    result = _classify(int(response.status_code), body)

    if result.ok and use_cache:
        write_check_cache(fingerprint, result, path=cache_path)

    return result
