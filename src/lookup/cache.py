r"""Local cache for raw dictionary API responses.

Why a cache
-----------
Dictionary APIs are rate-limited and metered.  Looking the same word up twice
is pure waste, so:

* a word found in the cache is reused without any HTTP request;
* only a miss triggers a request, whose **raw** payload is then stored;
* re-running the same document is nearly free.

The cache lives at ``~/.cache/excerpt/{dict_slug}.cache.json`` — one file per
dictionary — and holds the dictionary API's *raw* response, not the cleaned
form, so a future change to the cleaning rules can be replayed offline.

Only deterministic outcomes are cached (found / not found / invalid query).
Rate limits and network errors are transient and deliberately left out, so a
temporary failure is never frozen into the cache.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from common.config import (
    CODE_INVALID_QUERY,
    CODE_OK,
    CODE_WORD_NOT_FOUND,
    default_cache_path,
    get_dict_slug,
)

__all__ = ["CACHE_VERSION", "WordCache", "cache_path_for"]

CACHE_VERSION = 1

#: Only these status codes are worth persisting.
CACHEABLE_CODES = frozenset({CODE_OK, CODE_WORD_NOT_FOUND, CODE_INVALID_QUERY})


def cache_path_for(
    dict_choice: str | None = None,
    explicit: str | Path | None = None,
) -> Path:
    """Return the cache path for *dict_choice*.

    *explicit* (from ``--cache-path``) wins when given; otherwise the default
    ``~/.cache/excerpt/{dict_slug}.cache.json`` is used.  Resolved lazily so
    that the dictionary choice is read at call time, never at import time.
    """
    if explicit:
        return Path(explicit).expanduser()
    return Path(default_cache_path(dict_choice)).expanduser()


class WordCache:
    """A simple JSON on-disk cache of raw dictionary payloads."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        dict_choice: str | None = None,
        enabled: bool = True,
    ):
        self.dict_slug = get_dict_slug(dict_choice)
        self.enabled = enabled
        self.path = cache_path_for(dict_choice, path)
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
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
            entries = raw.get("words")
            if isinstance(entries, dict):
                self._data = entries

    def save(self) -> None:
        """Atomically persist the cache (write temp file, then ``os.replace``)."""
        if not self.enabled or not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "dict": self.dict_slug,
            "words": self._data,
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

    def get_raw(self, word: str) -> Any | None:
        """Return the cached *raw* payload for *word*, or ``None`` on a miss."""
        if not self.enabled:
            self.misses += 1
            return None
        item = self._data.get(word.strip().lower())
        if item is None:
            self.misses += 1
            return None
        self.hits += 1
        return item.get("raw")

    def put_raw(self, word: str, code: int, raw: Any) -> None:
        """Cache a deterministic outcome; transient failures are dropped."""
        if not self.enabled or code not in CACHEABLE_CODES:
            return
        key = word.strip().lower()
        if not key:
            return
        record = {"code": int(code), "raw": raw}
        if self._data.get(key) == record:
            return
        self._data[key] = record
        self._dirty = True

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, word: str) -> bool:
        return word.strip().lower() in self._data
