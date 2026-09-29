r"""Write ``{title}.{dict_slug}.words.json`` and ``.errors.json``.

This module is the single owner of those two documents.  It receives *already
normalised* data — every entry has passed through the standard schema — and
only has to decide, per record, whether it becomes a success or an error.

Status model
------------
``.words.json``   one record per resolved word (``code == CODE_OK``).
``.errors.json``  one record per unresolved word, keyed on ``word + type``.

Both files are rewritten atomically (temp file + ``os.replace``), so a crash
mid-run can never leave a half-written JSON document behind.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from common.config import (
    API_KEY_PROBLEM_CODES,
    CODE_OK,
    RETRIABLE_CODES,
    SOURCE_LLM,
    STATUS_FILLED,
    STATUS_PENDING,
    BoldEntry,
    normalize_entry,
    reason_for,
)

__all__ = [
    "load_errors",
    "load_words",
    "now_iso",
    "write_documents",
    "write_errors",
    "write_words",
]


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """Return the current local time as an ISO-8601 string with offset."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    """Return a fresh error-record id."""
    return uuid.uuid4().hex


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Serialise *payload* to *path* atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_words(path: str | Path) -> list[dict[str, Any]]:
    """Read an existing ``.words.json``; a missing/corrupt file yields ``[]``."""
    target = Path(path)
    if not target.is_file():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return raw if isinstance(raw, list) else []


def load_errors(path: str | Path) -> list[dict[str, Any]]:
    """Read the ``errors`` array of an existing ``.errors.json``."""
    target = Path(path)
    if not target.is_file():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if isinstance(raw, dict) and isinstance(raw.get("errors"), list):
        return raw["errors"]
    return []


# ---------------------------------------------------------------------------
# Error bookkeeping
# ---------------------------------------------------------------------------

def _recount(errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild the top-level counters from scratch."""
    return {
        "total": len(errors),
        "retriable": sum(1 for e in errors if int(e.get("code", 0)) in RETRIABLE_CODES),
        "dict_api_key_problems": sum(
            1 for e in errors if int(e.get("code", 0)) in API_KEY_PROBLEM_CODES
        ),
        "errors": errors,
    }


def _upsert_error(
    errors: list[dict[str, Any]],
    *,
    word: str,
    type_: str,
    source: str,
    code: int,
    detail: str,
    status: str = STATUS_PENDING,
) -> None:
    """Insert or update the error record matching ``word + type``."""
    stamp = now_iso()
    for record in errors:
        if record.get("word") == word and record.get("type") == type_:
            record["code"] = int(code)
            record["reason"] = reason_for(code)
            record["source"] = source
            record["status"] = status
            record["time"] = stamp
            if detail:
                record["detail"] = detail
            return

    record = {
        "id": new_id(),
        "word": word,
        "type": type_,
        "source": source,
        "code": int(code),
        "reason": reason_for(code),
        "status": status,
        "time": stamp,
    }
    if detail:
        record["detail"] = detail
    errors.append(record)


def _mark_filled(
    errors: list[dict[str, Any]],
    *,
    word: str,
    type_: str,
    source: str,
) -> None:
    """Mark a still-pending record for ``word + type`` as filled."""
    for record in errors:
        if (
            record.get("word") == word
            and record.get("type") == type_
            and record.get("status") == STATUS_PENDING
        ):
            record["status"] = STATUS_FILLED
            record["source"] = source
            record["time"] = now_iso()
            return


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

def _entry_document(entry: BoldEntry, dict_slug: str) -> dict[str, Any]:
    """Serialise one :class:`BoldEntry` with the active dictionary slug."""
    payload = entry.to_dict()
    payload["entry"] = normalize_entry(entry.entry)
    payload["dict"] = dict_slug
    return payload


def write_documents(
    entries: Iterable[BoldEntry],
    *,
    words_path: str | Path,
    errors_path: str | Path,
    dict_slug: str,
    seed_errors: Iterable[dict[str, Any]] = (),
    llm_source: bool = False,
) -> dict[str, Any]:
    """Write both JSON documents for *entries* and return a small summary.

    Parameters
    ----------
    entries:
        Already-normalised records.  Entries whose ``code`` is ``CODE_OK``
        land in ``.words.json``; everything else becomes an error record.
    errors_path:
        Existing errors are read first so that records from a previous step
        (e.g. ``extractor``'s non-word records) are preserved and merged.
    seed_errors:
        Extra error records to merge in before writing.
    llm_source:
        When true, a successful entry also marks a pending record ``filled``.
    """
    entries = list(entries)
    errors = load_errors(errors_path)
    for seed in seed_errors:
        errors.append(dict(seed))

    words: list[dict[str, Any]] = []

    for entry in entries:
        if entry.code == CODE_OK:
            words.append(_entry_document(entry, dict_slug))
            _mark_filled(
                errors,
                word=entry.word,
                type_=entry.type,
                source=SOURCE_LLM if llm_source else _source_of(entry),
            )
        else:
            _upsert_error(
                errors,
                word=entry.word,
                type_=entry.type,
                source=_source_of(entry),
                code=entry.code,
                detail=entry.detail,
            )

    document = _recount(errors)
    _atomic_write_json(Path(words_path), words)
    _atomic_write_json(Path(errors_path), document)

    return {
        "words": len(words),
        "total": document["total"],
        "retriable": document["retriable"],
        "dict_api_key_problems": document["dict_api_key_problems"],
    }


def _source_of(entry: BoldEntry) -> str:
    """Return the ``source`` value to record for *entry*."""
    return str(entry.source or "lookup")


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def write_words(entries: Iterable[BoldEntry], path: str | Path, dict_slug: str) -> int:
    """Write only the ``.words.json`` document; return the entry count."""
    documents = [_entry_document(entry, dict_slug) for entry in entries]
    _atomic_write_json(Path(path), documents)
    return len(documents)


def write_errors(errors: Iterable[dict[str, Any]], path: str | Path) -> dict[str, Any]:
    """Write only the ``.errors.json`` document; return the recomputed summary."""
    document = _recount([dict(record) for record in errors])
    _atomic_write_json(Path(path), document)
    return document
