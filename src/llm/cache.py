r"""Local cache of successful LLM completions.

Why a cache
-----------
A completion is the most expensive thing this tool does: it costs money,
latency, and — on a free tier — rate-limit budget.  Re-running the pipeline
over the same document produces the same records with the same prompt, so the
answer is already known; asking again is pure waste.

The cache lives at ``~/.cache/excerpt/{model}.cache.json`` — one file per
model, because the same record answered by a different model is a different
answer, not a stale one.

What is cached, and what is not
-------------------------------
Only a reply that already passed :func:`llm.__main__._validate` is stored.  A
failure is never frozen: the next run must be free to succeed, so timeouts,
rate limits and malformed JSON are deliberately left out — the same rule
``lookup.cache`` applies to the dictionary.

The key is a hash of the record's text, its type and
:data:`llm.format.PROMPT_VERSION`.  The prompt version is part of the
key so that improving the prompt invalidates every stored completion at once,
without a migration and without a cache-file layout change.  The model is
*not* in the key: it is already the filename.

The cached value is the validated payload, not the finished
:class:`~common.schema` entry.  The entry also carries bookkeeping (``code``,
``source``, ``dict``, line numbers) that belongs to the run, not to the reply;
storing only the payload keeps the cache reusable if that bookkeeping changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from .format import PROMPT_VERSION

__all__ = [
    "CACHE_VERSION",
    "LlmCache",
    "cache_key",
    "cache_path_for",
    "default_cache_dir",
    "sanitize_model",
]

CACHE_VERSION = 1

#: Characters Windows forbids in a filename, plus the C0 control range.
#: ``:`` matters most in practice: model names from aggregators routinely look
#: like ``vendor/model:free``, and on Windows a colon opens an alternate data
#: stream rather than naming a file, so the write would fail outright.
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_model(model: str) -> str:
    """Return *model* reduced to something safe for a filename.

    Aggregator model ids are routinely ``vendor/model:tag``.  Both separators
    are replaced with ``_`` so the result is a single, portable path segment.
    """
    cleaned = _UNSAFE_NAME.sub("_", str(model or "").strip())
    # A fully blank name would otherwise produce no filename at all; fall back
    # to a fixed stem so the cache path always names something.
    return cleaned or "default"


def default_cache_dir() -> Path:
    """Return the directory shared with the other excerpt caches.

    ``EXCERPT_CACHE_DIR`` overrides it (used by the test-suite to keep every
    write inside a temporary directory).
    """
    base = os.environ.get("EXCERPT_CACHE_DIR")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".cache" / "excerpt"


def cache_path_for(
    model: str,
    explicit: str | Path | None = None,
) -> Path:
    """Return the cache path for *model*.

    *explicit* (from ``--llm-cache-path``) wins when given; otherwise the
    default ``~/.cache/excerpt/{model}.cache.json`` is used.
    """
    if explicit:
        return Path(explicit).expanduser()
    return default_cache_dir() / f"{sanitize_model(model)}.cache.json"


def cache_key(
    word: str,
    type_: str,
    *,
    prompt_version: int = PROMPT_VERSION,
) -> str:
    """Return the deterministic cache key for one record.

    The record text is normalised only by stripping surrounding whitespace:
    two records differing in case or internal punctuation are genuinely
    different inputs, and folding them together would serve the wrong answer.
    """
    material = "\x1f".join(
        (str(prompt_version), str(type_ or ""), str(word or "").strip())
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class LlmCache:
    """A JSON on-disk cache of validated LLM payloads, one file per model.

    Reads are lock-guarded because ``complete_entries`` may probe the cache
    from several worker threads at once; writes happen only via
    :meth:`put`/:meth:`save`, which the calling thread drives.
    """

    def __init__(
        self,
        model: str,
        path: str | Path | None = None,
        *,
        enabled: bool = True,
    ):
        self.model = str(model or "")
        self.enabled = enabled
        self.path = cache_path_for(self.model, path)
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        """Read the cache file; a corrupt file is ignored, not fatal."""
        if not self.enabled or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return
        if isinstance(raw, dict) and raw.get("version") == CACHE_VERSION:
            entries = raw.get("entries")
            if isinstance(entries, dict):
                self._data = entries

    def save(self) -> None:
        """Atomically persist the cache (write temp file, then ``os.replace``)."""
        if not self.enabled or not self._dirty:
            return
        with self._lock:
            data = dict(self._data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "model": self.model,
            "entries": data,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, self.path)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._dirty = False

    # -- access ------------------------------------------------------------

    def get(self, key: str) -> dict[str, Any] | None:
        """Return the cached payload for *key*, or ``None`` on a miss."""
        if not self.enabled:
            self.misses += 1
            return None
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            self.hits += 1
            payload = item.get("payload")
        # A deep-ish copy keeps a caller from mutating the cached object in
        # place and silently rewriting what the next record will read.
        return json.loads(json.dumps(payload))

    def put(self, key: str, payload: dict[str, Any]) -> None:
        """Cache a validated *payload* under *key*."""
        if not self.enabled:
            return
        record = {"payload": payload}
        with self._lock:
            if self._data.get(key) == record:
                return
            self._data[key] = record
        self._dirty = True

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data
