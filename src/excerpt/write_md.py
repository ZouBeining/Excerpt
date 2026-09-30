r"""Render ``{title}.{dict_slug}.md`` and ``{title}.{dict_slug}.index.md``.

Both files are derived from the same two sources — the extracted index and the
final ``.words.json`` — so no lookup happens here and the renderer stays purely
presentational.

The layout follows ``docs/template.md``:

* a header with the source, the valid-entry count, the dictionary display name,
  the lemmatized pairs and the cache-hit count;
* one ``###`` section per entry, with the source sentence as a block quote,
  the lookup metadata, the pronunciation, and then definitions, etymology,
  first use and synonyms.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from common.config import (
    CODE_OK,
    MD_LABELS,
    SOURCE_CACHE,
    Artifacts,
    BoldEntry,
    abbreviate_pos,
    get_dict_config,
)

from .bold import render_markdown_sentence

__all__ = ["render_index_md", "render_note_md", "run", "write_md"]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def load_index(art: Artifacts) -> dict[str, Any]:
    """Read ``{title}.index.json``."""
    payload = _load_json(Path(art.out_dir) / art.index_json, {})
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# Header statistics
# ---------------------------------------------------------------------------

def _lemmatized_pairs(entries: Sequence[BoldEntry]) -> list[str]:
    return [
        f"{entry.lemma_from}\u2192{entry.word}"
        for entry in entries
        if entry.is_lemma and entry.lemma_from
    ]


def _render_header(
    entries: Sequence[BoldEntry],
    index: dict[str, Any],
    art: Artifacts,
    *,
    source_name: str = "",
) -> list[str]:
    valid = sum(1 for entry in entries if entry.code == CODE_OK)
    records = len(index.get("items") or [])
    display = get_dict_config()["display_name"]
    pairs = _lemmatized_pairs(entries)
    cache_hits = sum(1 for entry in entries if entry.source == SOURCE_CACHE)

    source = source_name or str(index.get("source") or "")
    lemmatized = f"{len(pairs)} （{', '.join(pairs)}）" if pairs else "0"

    return [
        f"# {art.title}",
        "",
        f"- source：`{Path(source).name if source else art.title}`",
        f"- valid_entries：{valid}  /  {records} bolded records",
        f"- dict：{display}",
        f"- lemmatized：{lemmatized}",
        f"- cache_hit：{cache_hits}",
        "",
        "---",
        "",
    ]


# ---------------------------------------------------------------------------
# Entry bodies
# ---------------------------------------------------------------------------

def _pronunciation_line(entry_payload: dict[str, Any]) -> str:
    """Render ``US /…/`` style pronunciation, or ``""`` when absent."""
    pieces: list[str] = []
    for pronunciation in entry_payload.get("pronunciations") or []:
        if not isinstance(pronunciation, dict):
            continue
        text = str(pronunciation.get("mw") or pronunciation.get("ipa") or "").strip()
        if not text:
            continue
        label = str(pronunciation.get("label") or "").strip()
        rendered = f"/{text}/"
        pieces.append(f"`{label}` {rendered}" if label else rendered)
    return " ".join(pieces)


def _render_senses(entry_payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    senses = entry_payload.get("senses") or []
    if not senses:
        return lines

    lines.append(f"**{MD_LABELS['definition']}**")
    for sense in senses:
        if not isinstance(sense, dict):
            continue
        pos = str(sense.get("pos") or "").strip()
        definition = str(sense.get("definition") or "").strip()
        if not definition and not sense.get("examples"):
            continue
        prefix = f"- *{abbreviate_pos(pos)}* " if pos else "- "
        lines.append(f"{prefix}{definition}".rstrip())
        for example in sense.get("examples") or []:
            text = str(example).strip()
            if text:
                lines.append(f"  - {MD_LABELS['examples']}：{text}")
    return lines


def _render_tag(label: str, values: Iterable[str]) -> list[str]:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        return []
    return [f"**{label}**", f"- {', '.join(cleaned)}"]


def _render_entry_md(entry: BoldEntry, index: int) -> list[str]:
    payload = entry.entry or {}
    # A sentence can bold several records; quote it with only this one marked.
    sentence = render_markdown_sentence(
        entry.sentence, (entry.word, entry.lemma_from)
    )
    lines: list[str] = [
        f"### {index}. {entry.word}",
        "",
        f"> {sentence}",
        "",
        f"- type: {entry.type}",
        f"- line: {entry.line}",
        f"- source: {entry.source}",
        f"- dict: {entry.dict_slug}",
    ]

    pronunciation = _pronunciation_line(payload)
    if pronunciation:
        lines.extend(["", pronunciation])

    lines.append("")
    lines.extend(_render_senses(payload))

    etymology = str(payload.get("etymology") or "").strip()
    if etymology:
        lines.extend(["", f"**{MD_LABELS['etymology']}**", f"- {etymology}"])

    first_use = str(payload.get("firstUse") or "").strip()
    if first_use:
        lines.extend(["", f"**{MD_LABELS['first_use']}**", f"- {first_use}"])

    lines.extend(["", *_render_tag(MD_LABELS["synonyms"], payload.get("synonyms") or [])])
    lines.extend(["", *_render_tag(MD_LABELS["antonyms"], payload.get("antonyms") or [])])
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Document renderers
# ---------------------------------------------------------------------------

def render_note_md(
    entries: Sequence[BoldEntry],
    index: dict[str, Any],
    art: Artifacts,
    *,
    source_name: str = "",
) -> str:
    """Render the full vocabulary note."""
    lines = _render_header(entries, index, art, source_name=source_name)
    for i, entry in enumerate(entries, start=1):
        lines.extend(_render_entry_md(entry, i))
    return "\n".join(lines).rstrip() + "\n"


def render_index_md(
    entries: Sequence[BoldEntry],
    index: dict[str, Any],
    art: Artifacts,
    *,
    source_name: str = "",
) -> str:
    """Render the Markdown index, linking each record to its note section."""
    source = source_name or str(index.get("source") or "")
    lines = [
        f"# {art.title} \u00b7 Index",
        "",
        f"- source: {source}",
        f"- valid_entries: {sum(1 for e in entries if e.code == CODE_OK)}"
        f"  /  {len(index.get('items') or [])} bolded records",
        "",
        "| # | word | type | sentence | line | sense | pronunciation |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for i, entry in enumerate(entries, start=1):
        payload = entry.entry or {}
        sense = str(payload.get("pos") or "").strip() or "\u2014"
        pronunciation = _pronunciation_line(payload) or "\u2014"
        sentence = render_markdown_sentence(
            str(entry.sentence), (entry.word, entry.lemma_from)
        ).replace("|", "\\|")
        lines.append(
            f"| {i} | {entry.word} | {entry.type} | \"{sentence}\" | "
            f"{entry.line} | {sense} | {pronunciation} |"
        )

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_md(
    entries: Sequence[BoldEntry],
    art: Artifacts,
    *,
    source_name: str = "",
) -> dict[str, str]:
    """Write both Markdown documents and return their paths."""
    out_dir = Path(art.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = load_index(art)

    note_path = out_dir / art.note_md
    note_path.write_text(
        render_note_md(entries, index, art, source_name=source_name),
        encoding="utf-8",
    )

    index_path = out_dir / art.index_md
    index_path.write_text(
        render_index_md(entries, index, art, source_name=source_name),
        encoding="utf-8",
    )

    return {"note_md": str(note_path), "index_md": str(index_path)}


def run(entries: Sequence[BoldEntry], art: Artifacts, *, source_name: str = ""):
    """Alias of :func:`write_md` so the pipeline has a uniform ``run``."""
    return write_md(entries, art, source_name=source_name)
