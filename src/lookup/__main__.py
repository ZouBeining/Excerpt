r"""Entry point of the ``lookup`` package.

Pipeline
--------
1. query the original word through ``lookup.{dict_slug}_api``;
2. restore its base form with ``lookup.lemmatizer`` when the lookup failed;
3. query the base form too, when it differs;
4. hand the merged, normalised records to ``lookup.write_json``.

The dictionary-specific modules are resolved *by name* from
``common.config.get_dict_config()``, so supporting a new dictionary means
adding ``my_dict_api.py`` / ``my_dict_data.py`` and one entry in
``DICT_CHOICES`` — no code in this file changes.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from common.config import (
    CODE_OK,
    SOURCE_API,
    SOURCE_CACHE,
    SOURCE_LOOKUP,
    Artifacts,
    BoldEntry,
    get_dict_config,
)

from . import write_json
from .cache import WordCache
from .http import build_session
from .outcome import LookupOutcome

__all__ = ["lookup_entries", "run", "word_lookup_function"]


def word_lookup_function() -> Callable[..., tuple[LookupOutcome, dict[str, Any]]]:
    """Return the ``lookup_word`` callable of the active dictionary.

    Resolved through ``config.get_dict_config()["api_module"]``; the dictionary
    choice is never hard-coded here.
    """
    module_name = f"lookup.{get_dict_config()['api_module']}"
    return importlib.import_module(module_name).lookup_word


def lookup_entries(
    entries: Sequence[BoldEntry],
    *,
    session: Any,
    cache: WordCache | None,
    api_key: str,
    timeout: float | None = None,
    retry: int | None = None,
    delay: float | None = None,
) -> list[BoldEntry]:
    """Resolve every word entry in place and return the list.

    Non-word entries are left untouched: they are handled by ``excerpt.llm``.
    ``source`` is recomputed for every entry on every run, so ``cache`` versus
    ``api`` always reflects *this* run.

    Every returned entry carries the active ``dict_slug``, so downstream
    consumers (``excerpt.write_md``, ``excerpt.latex``) no longer depend on
    :func:`lookup.write_json.write_documents` having stamped the field first.
    """
    lookup_word = word_lookup_function()
    slug = get_dict_config()["slug"]
    result: list[BoldEntry] = []

    word_entries = [e for e in entries if e.type == "word"]
    total = len(word_entries)
    index = 0

    for entry in entries:
        entry.dict_slug = slug
        if entry.type != "word":
            result.append(entry)
            continue

        index += 1
        cached_before = cache.hits if cache is not None else 0
        outcome, standard = lookup_word(
            entry.word,
            session=session,
            api_key=api_key,
            cache=cache,
            timeout=timeout,
            retry=retry,
            delay=delay,
        )

        entry.code = outcome.code
        entry.detail = outcome.detail
        entry.entry = standard
        if outcome.code == CODE_OK:
            entry.source = (
                SOURCE_CACHE
                if cache is not None and cache.hits > cached_before
                else SOURCE_API
            )
        else:
            entry.source = SOURCE_LOOKUP

        flag = "ok" if outcome.code == CODE_OK else f"err {outcome.code}"
        print(f"  [lookup {index}/{total}] {entry.word!r}: {flag}")
        result.append(entry)

    return result


def run(
    entries: Sequence[BoldEntry],
    art: Artifacts,
    *,
    timeout: float | None = None,
    retry: int | None = None,
    delay: float | None = None,
    use_cache: bool = True,
    cache_path: str | Path | None = None,
) -> tuple[WordCache, list[BoldEntry], dict[str, Any]]:
    """Run the whole lookup stage and write ``.words.json`` / ``.errors.json``.

    Returns ``(cache, entries, summary)``.  The cache is returned so that the
    caller can add base-form lookups to it before saving once at the end.
    """
    cfg = get_dict_config()
    api_key = _api_key()

    cache = WordCache(cache_path, enabled=use_cache)
    session = build_session()

    try:
        resolved = lookup_entries(
            entries,
            session=session,
            cache=cache,
            api_key=api_key,
            timeout=timeout,
            retry=retry,
            delay=delay,
        )
    finally:
        session.close()

    out_dir = Path(art.out_dir)
    summary = write_json.write_documents(
        resolved,
        words_path=out_dir / art.words_json,
        errors_path=out_dir / art.errors_json,
        dict_slug=cfg["slug"],
    )

    return cache, resolved, summary


def _api_key() -> str:
    from common.config import get_dict_api_key

    return get_dict_api_key()


def main(*args: Any, **kwargs: Any):  # pragma: no cover - convenience alias
    """Alias of :func:`run`, kept so ``excerpt.cli`` can import a stable name."""
    return run(*args, **kwargs)
