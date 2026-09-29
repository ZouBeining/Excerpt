r"""Resolve the base form (lemma) of an inflected word with LemmInflect.

Why
---
Bolded words in a text are usually inflected (``scolded``, ``whining``…).  Once
the original word fails to resolve against the dictionary, LemmInflect is asked
for its base form; if that differs, the base form is looked up *as well* and
appended as a separate entry marked ``is_lemma=true``.

Rules (per AGENTS.md)
---------------------
* only entries with ``type == "word"`` are lemmatized;
* only when the original lookup **failed** (``code != CODE_OK``);
* a lemma identical to the original is skipped;
* a successful lemma is appended right after its source entry;
* a failed lemma goes to ``.errors.json``; it is **not** lemmatized again.
"""

from __future__ import annotations

from typing import Any

from common.config import BoldEntry, get_dict_config, get_dict_slug

try:  # Degrade gracefully when the optional dependency is absent.
    from lemminflect import getAllLemmas
except ImportError:  # pragma: no cover
    getAllLemmas = None  # type: ignore[assignment]

__all__ = [
    "attach_lemma_entries",
    "get_lemma",
    "is_available",
    "is_lemma_entry",
    "lemma_from",
    "mark_as_lemma",
]


def is_available() -> bool:
    """Return whether LemmInflect could be imported."""
    return getAllLemmas is not None


def get_lemma(word: str) -> list[str]:
    """Return candidate base forms for *word*, excluding *word* itself.

    Candidates are lowercased and de-duplicated; when several remain the
    shortest is returned first (``better`` -> ``good`` before ``well``).
    """
    if not word or getAllLemmas is None:
        return []

    text = str(word).strip()
    if not text.isalpha():
        return []

    try:
        found = getAllLemmas(text)
    except Exception:  # pragma: no cover - never let the library break a run
        return []

    if not found:
        return []

    candidates: list[str] = []
    for lemmas in found.values():
        for lemma in lemmas or ():
            low = str(lemma).strip().lower()
            if low and low != text.lower() and low not in candidates:
                candidates.append(low)

    return sorted(candidates, key=len)


def mark_as_lemma(entry: BoldEntry, source_word: str) -> BoldEntry:
    """Flag *entry* as the base form restored from *source_word*."""
    entry.is_lemma = True
    entry.lemma_from = source_word
    return entry


def is_lemma_entry(entry: BoldEntry) -> bool:
    return bool(getattr(entry, "is_lemma", False))


def lemma_from(entry: BoldEntry) -> str:
    return str(getattr(entry, "lemma_from", ""))


def _lookup_function():
    """Import ``lookup.{dict_slug}_api`` lazily, driven by the configuration.

    The module name comes from ``config.get_dict_config()``, so adding a
    dictionary never requires editing this file.
    """
    import importlib

    module_name = f"lookup.{get_dict_config()['api_module']}"
    return importlib.import_module(module_name).lookup_word


def attach_lemma_entries(
    entries: list[BoldEntry],
    *,
    session: Any,
    cache: Any,
    delay: float | None = None,
    timeout: float | None = None,
    retry: int | None = None,
) -> list[BoldEntry]:
    """Return *entries* with base-form entries inserted after their sources.

    A base-form entry is only added when LemmInflect yields a different word
    *and* that word is not already present, so the output never contains the
    same headword twice.
    """
    if not is_available():
        print("[lemma] [warn] LemmInflect is not installed; skipping.")
        return entries

    lookup_word = _lookup_function()
    api_key = _api_key()
    dict_slug = get_dict_slug()

    present = {entry.word.strip().lower() for entry in entries}
    pending: list[tuple[BoldEntry, str]] = []

    for entry in entries:
        if entry.type != "word" or is_lemma_entry(entry):
            continue
        # Only worth restoring when the original lookup did not succeed.
        if entry.code == 0:
            continue

        for candidate in get_lemma(entry.word):
            if candidate in present:
                continue
            present.add(candidate)
            pending.append((entry, candidate))

    if not pending:
        return entries

    added: dict[int, BoldEntry] = {}
    for index, (source, candidate) in enumerate(pending, start=1):
        outcome, standard = lookup_word(
            candidate,
            session=session,
            api_key=api_key,
            cache=cache,
            timeout=timeout,
            retry=retry,
            delay=delay,
        )
        restored = BoldEntry(
            word=candidate,
            type="word",
            sentence=source.sentence,
            line=source.line,
            code=outcome.code,
            detail=outcome.detail,
            dict_slug=dict_slug,
            entry=standard,
        )
        mark_as_lemma(restored, source.word)
        added[id(source)] = restored
        flag = "ok" if outcome.code == 0 else f"err {outcome.code}"
        print(f"  [lemma {index}/{len(pending)}] {source.word!r} -> {candidate!r}: {flag}")

    result: list[BoldEntry] = []
    for entry in entries:
        result.append(entry)
        restored = added.get(id(entry))
        if restored is not None:
            result.append(restored)
    return result


def _api_key() -> str:
    from common.config import get_dict_api_key

    return get_dict_api_key()
