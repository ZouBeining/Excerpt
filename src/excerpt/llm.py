r"""Complete the non-word entries with an LLM.

Only records that the extractor could not resolve alone are sent here — that
is, records with ``status == "pending"`` and ``type != "word"``.  Words that a
dictionary could not resolve stay the lemmatizer's business; the LLM never
sees them.

The provider is described entirely by :func:`common.config.get_openai_settings`
(an OpenAI-compatible Chat Completions endpoint), so nothing here is tied to a
particular vendor.  When the provider advertises JSON-schema support the
structured-output path is used; otherwise the prompt demands strict JSON and
the reply is validated and retried.

Output is English only, and the analysis is angled per record type:

* a **phrase** is explained in terms of meaning, usage and near-synonyms;
* a **sentence** is mined for structure and verb usage, not translated.

Etymology and first-use metadata are deliberately left empty.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from common.config import (
    CODE_OK,
    LLM_DICTIONARY_NAME,
    SOURCE_EXTRACTOR,
    SOURCE_LLM,
    STATUS_PENDING,
    TYPE_PHRASE,
    TYPE_SENTENCE,
    TYPE_WORD,
    Artifacts,
    BoldEntry,
    empty_entry,
    get_openai_settings,
    normalize_entry,
)

from lookup import write_json

__all__ = ["SYSTEM_PROMPT", "complete_entries", "run", "schema_for"]


SYSTEM_PROMPT = (
    "You are a lexicographer writing concise English study notes for a "
    "language learner. Always reply with a single JSON object and nothing "
    "else. Never invent etymology, first-use dates, or metadata fields."
)

_PHRASE_INSTRUCTION = (
    "The input is a multi-word PHRASE. Focus on what the phrase means, when a "
    "speaker would use it, and any close synonymous expressions. Give the "
    "synonyms in the `synonyms` array."
)

_SENTENCE_INSTRUCTION = (
    "The input is a full SENTENCE. Do NOT translate or explain the whole "
    "sentence. Instead explain the sentence PATTERN and the vocabulary used, "
    "especially the verb: its form, its meaning here, and how the pattern is "
    "reused. Put the reusable part of the pattern in `shortDefs`."
)

#: The JSON schema the model must satisfy.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pos", "definition", "examples"],
    "properties": {
        "pos": {
            "type": "string",
            "description": "part of speech, e.g. 'phrase' or 'sentence pattern'",
        },
        "definition": {
            "type": "string",
            "description": "one-line English explanation",
        },
        "examples": {
            "type": "array",
            "items": {"type": "string"},
            "description": "one to three short English example sentences",
        },
        "synonyms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "near-synonymous expressions (phrases only)",
        },
        "antonyms": {
            "type": "array",
            "items": {"type": "string"},
        },
        "shortDefs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "compact glosses",
        },
    },
}


def schema_for() -> dict[str, Any]:
    """Return the JSON schema used for structured output."""
    return _RESPONSE_SCHEMA


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _instruction_for(record_type: str) -> str:
    if record_type == TYPE_SENTENCE:
        return _SENTENCE_INSTRUCTION
    return _PHRASE_INSTRUCTION


def _user_prompt(entry: BoldEntry) -> str:
    """Build the per-record user message."""
    return (
        f"{_instruction_for(entry.type)}\n\n"
        f"Bold text: {entry.word!r}\n"
        f"Type: {entry.type}\n"
        f"Source sentence: {entry.sentence!r}\n\n"
        "Reply with JSON using these keys: "
        "pos (string), definition (string), examples (array of strings), "
        "synonyms (array of strings), antonyms (array of strings), "
        "shortDefs (array of strings). "
        "Every string must be in English."
    )


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------

def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        found: list[str] = []
        for item in value:
            found.extend(_as_string_list(item))
        return found
    return []


def _validate(payload: Any) -> dict[str, Any]:
    """Validate a decoded reply against the schema; raise on a violation."""
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")

    definition = str(payload.get("definition") or "").strip()
    if not definition:
        raise ValueError("missing 'definition'")

    return {
        "pos": str(payload.get("pos") or "").strip(),
        "definition": definition,
        "examples": _as_string_list(payload.get("examples")),
        "synonyms": _as_string_list(payload.get("synonyms")),
        "antonyms": _as_string_list(payload.get("antonyms")),
        "shortDefs": _as_string_list(payload.get("shortDefs")),
    }


def _to_standard_entry(entry: BoldEntry, payload: dict[str, Any]) -> dict[str, Any]:
    """Map a validated LLM reply onto the standard entry schema."""
    result = empty_entry()
    result["word"] = entry.word
    result["language"] = "en"
    result["dictionary"] = LLM_DICTIONARY_NAME
    result["section"] = "alpha"
    result["offensive"] = False
    result["pos"] = payload["pos"] or entry.type
    result["senses"] = [
        {
            "pos": payload["pos"] or entry.type,
            "definition": payload["definition"],
            "examples": payload["examples"],
        }
    ]
    result["shortDefs"] = payload["shortDefs"] or [payload["definition"]]
    result["synonyms"] = payload["synonyms"]
    result["antonyms"] = payload["antonyms"]
    return normalize_entry(result)


# ---------------------------------------------------------------------------
# Provider interaction
# ---------------------------------------------------------------------------

def _create_client() -> Any:
    """Create the OpenAI-compatible client from the shared settings."""
    from openai import OpenAI

    api_key, base_url, _model = get_openai_settings()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set but USE_LLM is enabled."
        )
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _request_completion(
    client: Any,
    *,
    model: str,
    entry: BoldEntry,
    use_schema: bool,
    attempts: int = 2,
) -> dict[str, Any]:
    """Ask the provider once (with one retry) and return the validated reply."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(entry)},
    ]

    last_error: Exception | None = None

    for _ in range(max(1, attempts)):
        try:
            if use_schema:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "excerpt_entry",
                            "strict": True,
                            "schema": _RESPONSE_SCHEMA,
                        },
                    },
                )
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                    response_format={"type": "json_object"},
                )
            content = response.choices[0].message.content or ""
            return _validate(json.loads(content))
        except Exception as exc:  # noqa: BLE001 - provider errors vary widely
            last_error = exc
            # A provider that rejects json_schema should fall back once.
            if use_schema and _looks_like_schema_rejection(exc):
                use_schema = False
                continue

    raise RuntimeError(f"LLM completion failed: {last_error}")


def _looks_like_schema_rejection(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("json_schema", "response_format", "unsupported", "invalid_request")
    )


def _summarize_error(exc: Exception) -> str:
    """Reduce a verbose provider error to one actionable line.

    Aggregators wrap the upstream fault in a large nested payload; printing it
    whole buries the log.  Pull out the HTTP status and, when present, the
    innermost provider message — the part a user can act on.
    """
    text = str(exc)

    status = ""
    marker = "Error code: "
    if marker in text:
        tail = text.split(marker, 1)[1]
        digits = ""
        for char in tail:
            if char.isdigit():
                digits += char
            else:
                break
        status = digits

    detail = ""
    # OpenRouter nests the real message inside metadata.raw; the openai SDK
    # renders that dict with single quotes, other paths use double quotes, so
    # both spellings are searched.
    for key in ('"raw":', "'raw':", '"message":', "'message':"):
        if key not in text:
            continue
        chunk = text.split(key, 1)[1].lstrip()
        if chunk.startswith(('"', "'")):
            quote = chunk[0]
            try:
                detail = chunk[1 : chunk.index(quote, 1)].strip()
            except ValueError:
                detail = ""
        if detail:
            break
    # A raw blob may itself be escaped JSON; in that case take its inner
    # message rather than the braces.
    if detail.startswith("{"):
        for inner_key in ('"message":', "'message':"):
            if inner_key in detail:
                inner = detail.split(inner_key, 1)[1].lstrip()
                if inner.startswith(('"', "'")):
                    quote = inner[0]
                    try:
                        detail = inner[1 : inner.index(quote, 1)].strip()
                    except ValueError:
                        pass
                break
    if detail:
        detail = detail.replace("\\n", " ").split(". ")[0].strip().rstrip(".")

    head = f"HTTP {status}" if status else "request failed"
    return f"{head}: {detail}" if detail else head


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def complete_entries(
    entries: Sequence[BoldEntry],
    *,
    client: Any = None,
    model: str = "",
    use_schema: bool = True,
) -> list[BoldEntry]:
    """Complete every pending non-word entry in place and return the list.

    Entries already marked ``filled`` are skipped, which makes re-running the
    pipeline idempotent.
    """
    targets = [
        entry
        for entry in entries
        if entry.type != TYPE_WORD and entry.source == SOURCE_EXTRACTOR
    ]
    if not targets:
        return list(entries)

    if client is None:
        client = _create_client()
    if not model:
        model = get_openai_settings()[2]

    total = len(targets)
    for index, entry in enumerate(targets, start=1):
        try:
            payload = _request_completion(
                client, model=model, entry=entry, use_schema=use_schema
            )
        except Exception as exc:  # noqa: BLE001 - a single failure must not abort
            print(f"  [llm {index}/{total}] {entry.word!r}: failed ({_summarize_error(exc)})")
            entry.detail = _summarize_error(exc)
            continue

        entry.entry = _to_standard_entry(entry, payload)
        entry.code = CODE_OK
        entry.detail = ""
        entry.source = SOURCE_LLM
        print(f"  [llm {index}/{total}] {entry.word!r}: ok")

    return list(entries)


def run(
    entries: Sequence[BoldEntry],
    art: Artifacts,
    *,
    dict_slug: str = "",
) -> list[BoldEntry]:
    """Run the LLM stage and rewrite ``.words.json`` / ``.errors.json``."""
    from common.config import get_dict_slug

    resolved = complete_entries(entries)

    out_dir = Path(art.out_dir)
    write_json.write_documents(
        resolved,
        words_path=out_dir / art.words_json,
        errors_path=out_dir / art.errors_json,
        dict_slug=dict_slug or get_dict_slug(),
        llm_source=True,
    )
    return resolved
