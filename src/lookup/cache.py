"""单词查询结果的本地缓存。

为什么需要缓存
--------------
UApiPro 对匿名访客的配额是 **1500 credits / 30 天**，且**每次查询扣 1 credit**
（命中其服务端缓存时减半）。重复查同一个词完全是浪费配额，因此：

* 查询前先查本地缓存，命中则直接复用，不发请求、不扣配额；
* 未命中才调 API，并把结果落盘；
* 重跑同一份文档时几乎零成本。

缓存按「词（小写）」存储，落在一个 JSON 文件里，便于查看与手改。
只缓存**确定性的结果**（查到 / 未收录 / 参数非法）；限流与网络错误属瞬时故障，
不写入缓存，以免把偶发失败固化下来。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .config import CODE_INVALID_QUERY, CODE_OK, CODE_WORD_NOT_FOUND

__all__ = ["WordCache", "default_cache_path"]

CACHE_VERSION = 1

# 只缓存这些状态码的结果，避免把瞬时错误固化
CACHEABLE_CODES = frozenset({CODE_OK, CODE_WORD_NOT_FOUND, CODE_INVALID_QUERY})

# Determine the cache path based on dict choice
_dict_choice_lower = os.getenv("DICT_CHOICE", "").strip().lower()

def default_cache_path() -> Path:
    """Default cache path: either specified by an environment variable, 
    or put in the user's home directory."""
    env = os.environ.get("EXCERPT_CACHE_PATH")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "excerpt" / f"words.{_dict_choice_lower}.json"


class WordCache:
    """A simple JSON on-disk cache."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_cache_path()
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self.hits = 0
        self.misses = 0
        self._load()

    # 
    def _load(self) -> None:
        """Load and read the cached data."""
        if not self.path.is_file():
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
        """Atomic disk writes, preventing cache file corruption 
        caused by mid-process crashes."""
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": CACHE_VERSION, "words": self._data}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, self.path)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._dirty = False

    # Check if a word has been cached
    def get_cached(self, word: str) -> tuple[int, str, dict[str, Any]] | None:
        """Return ``(code, detail, entry)`` if cache hit, otherwise ``None``。"""
        item = self._data.get(word.strip().lower())
        if not item:
            self.misses += 1
            return None
        self.hits += 1
        return (
            int(item.get("code", CODE_OK)),
            str(item.get("detail", "")),
            item.get("entry") or {},
        )

    def put(self, word: str, code: int, detail: str, entry: dict[str, Any]) -> None:
        """Write to cache. Only deterministic results will be cached."""
        if code not in CACHEABLE_CODES:
            return
        key = word.strip().lower()
        if not key:
            return
        record = {"code": code, "detail": detail, "entry": entry}
        if self._data.get(key) == record:
            return
        self._data[key] = record
        self._dirty = True

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, word: str) -> bool:
        return word.strip().lower() in self._data
