r"""Extract every bolded span from the input Markdown.

Design notes
------------
* Both ``**bold**`` and ``__bold__`` are supported.
* Code fences, inline code and escaped characters are masked first, so a
  ``*`` inside code is never mistaken for a bold marker.
* Each bolded span is classified as ``word`` / ``phrase`` / ``sentence`` (see
  :func:`classify`), and its enclosing sentence is recovered at *paragraph*
  level — Markdown paragraphs are often hard-wrapped, so joining the lines
  first keeps a sentence from being cut at a newline.
* The module writes ``{title}.index.json`` and ``{title}.{dict_slug}.index.md``
  and hands the extracted records back to the caller.  Non-word records are
  *not* written to ``.errors.json`` here: that file is owned by
  ``lookup.write_json``, which maintains both JSON documents.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from common.config import (
    CODE_NOT_A_SINGLE_WORD,
    CODE_OK,
    SOURCE_EXTRACTOR,
    TYPE_PHRASE,
    TYPE_SENTENCE,
    TYPE_WORD,
    Artifacts,
    BoldEntry,
)

from .bold import BOLD_RE, render_markdown_sentence

__all__ = [
    "classify",
    "extract_bold_entries",
    "is_single_word",
    "normalize_word_key",
    "render_index_md",
    "write_index",
]


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

#: Code fence opener/closer (``` or ~~~), optionally with an info string.
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")

#: Inline code spans.
_INLINE_CODE_RE = re.compile(r"(`+)(?:.+?)\1")

#: Bold spans: **...** or __...__, no leading/trailing whitespace inside.
#: Shared with the renderers through :mod:`excerpt.bold`, so a span is always
#: recognised the same way on the way in and on the way out.
_BOLD_RE = BOLD_RE

#: Escaped characters — swallow the backslash but keep the width.
_ESCAPE_RE = re.compile(r"\\(.)")

#: A single English word, per AGENTS.md: starts and ends with a letter,
#: length >= 2, interior may only hold letters, hyphens and apostrophes.
_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*[A-Za-z]$")

#: Any punctuation makes a bolded span a *sentence* rather than a phrase.
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)

#: Sentence terminators (ASCII plus the CJK full-width forms).
_SENTENCE_END = ".!?;\u3002\uff01\uff1f\uff1b\u2026"

#: Placeholder kept in place of masked characters so offsets stay stable.
_PLACEHOLDER = "\x00"


# ---------------------------------------------------------------------------
# Word / phrase / sentence classification
# ---------------------------------------------------------------------------

def normalize_word_key(text: str) -> str:
    """Return the merge key for *text*: lowercase, no leading/trailing ``'-``."""
    return str(text).strip().lower().strip("'-")


def is_single_word(text: str) -> bool:
    """Return whether *text* is a single English word per AGENTS.md."""
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    if not _WORD_RE.match(stripped):
        return False
    # ``_WORD_RE`` already guarantees at least two ASCII letters.
    return True


def classify(text: str) -> str:
    """Return ``word`` / ``phrase`` / ``sentence`` for a bolded span."""
    if is_single_word(text):
        return TYPE_WORD
    if _PUNCTUATION_RE.search(text):
        return TYPE_SENTENCE
    return TYPE_PHRASE


# ---------------------------------------------------------------------------
# Paragraph iteration
# ---------------------------------------------------------------------------

def _strip_noise(line: str) -> str:
    """Mask inline code and escaped characters, preserving character offsets."""
    line = _INLINE_CODE_RE.sub(lambda m: _PLACEHOLDER * len(m.group(0)), line)
    line = _ESCAPE_RE.sub(lambda m: _PLACEHOLDER + m.group(1), line)
    return line


def _iter_paragraphs(text: str) -> Iterator[tuple[str, int]]:
    """Split the body into paragraphs, skipping fenced code blocks.

    Yields
    ------
    (paragraph text with its physical lines joined by spaces, first line number)
    """
    in_fence = False
    fence_token = ""
    buffer: list[str] = []
    start_line = 0

    def flush() -> Iterator[tuple[str, int]]:
        nonlocal buffer
        if buffer:
            joined = " ".join(part.strip() for part in buffer)
            yield joined, start_line
            buffer = []

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        fence = _FENCE_RE.match(raw_line)
        if fence:
            token = fence.group(1)[0]
            if not in_fence:
                yield from flush()
                in_fence, fence_token = True, token
            elif token == fence_token:
                in_fence = False
            continue

        if in_fence:
            continue

        if not raw_line.strip():
            yield from flush()
        else:
            if not buffer:
                start_line = lineno
            buffer.append(_strip_noise(raw_line))

    yield from flush()


def find_sentence(paragraph: str, needle: str) -> str:
    """Locate *needle* inside *paragraph* and return its enclosing sentence."""
    pos = paragraph.find(needle)
    if pos < 0:
        return paragraph.replace(_PLACEHOLDER, "").strip()

    start = 0
    for i in range(pos - 1, -1, -1):
        if paragraph[i] in _SENTENCE_END:
            start = i + 1
            break

    end = len(paragraph)
    for i in range(pos + len(needle), len(paragraph)):
        if paragraph[i] in _SENTENCE_END:
            end = i + 1
            break

    sentence = paragraph[start:end].replace(_PLACEHOLDER, "")
    return re.sub(r"\s+", " ", sentence).strip()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_bold_entries(text: str) -> list[BoldEntry]:
    """Extract every bolded record from *text*, deduplicated.

    Records are deduplicated on the normalised word key; the first occurrence
    keeps its original casing, sentence and line number.
    """
    results: list[BoldEntry] = []
    seen: set[str] = set()

    for paragraph, start_line in _iter_paragraphs(text):
        for match in _BOLD_RE.finditer(paragraph):
            inner = match.group(2).strip()
            if not inner:
                continue

            raw = inner.replace(_PLACEHOLDER, "").strip()
            if not raw:
                continue

            key = normalize_word_key(raw)
            if not key or key in seen:
                continue
            seen.add(key)

            entry_type = classify(raw)
            entry = BoldEntry(
                word=raw,
                type=entry_type,
                sentence=find_sentence(paragraph, inner),
                line=start_line,
                code=CODE_OK if entry_type == TYPE_WORD else CODE_NOT_A_SINGLE_WORD,
                detail="" if entry_type == TYPE_WORD else "not a single word",
                source=SOURCE_EXTRACTOR,
            )
            results.append(entry)

    return results


# ---------------------------------------------------------------------------
# Index output
# ---------------------------------------------------------------------------

def _index_items(entries: list[BoldEntry]) -> list[dict[str, Any]]:
    return [
        {
            "word": entry.word,
            "type": entry.type,
            "sentence": entry.sentence,
            "line": entry.line,
        }
        for entry in entries
    ]


def render_index_md(
    entries: list[BoldEntry],
    art: Artifacts,
    source_name: str,
) -> str:
    """Render the human-readable Markdown index of the extracted records."""
    lines = [
        f"# {art.title} · Index",
        "",
        f"- source: {source_name}",
        "",
        "| # | word | type | sentence | line | pronunciation |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for i, entry in enumerate(entries, start=1):
        # One sentence may hold several bolds: mark only this record's own span.
        sentence = render_markdown_sentence(
            entry.sentence, (entry.word, entry.lemma_from)
        ).replace("|", "\\|")
        lines.append(
            f"| {i} | {entry.word} | {entry.type} | \"{sentence}\" | "
            f"{entry.line} | {_index_pronunciation(entry)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _index_pronunciation(entry: BoldEntry) -> str:
    """Return the first pronunciation of an already-looked-up entry, if any."""
    for pronunciation in entry.entry.get("pronunciations") or []:
        mw = str((pronunciation or {}).get("mw") or "").strip()
        ipa = str((pronunciation or {}).get("ipa") or "").strip()
        if mw or ipa:
            return f"/{mw or ipa}/"
    return "—"


def write_index(
    entries: list[BoldEntry],
    art: Artifacts,
    source_name: str,
) -> dict[str, str]:
    """Write ``{title}.index.json`` and ``{title}.{dict_slug}.index.md``."""
    out_dir = Path(art.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "title": art.title,
        "source": source_name,
        "items": _index_items(entries),
    }
    index_json = out_dir / art.index_json
    index_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    index_md = out_dir / art.index_md
    index_md.write_text(render_index_md(entries, art, source_name), encoding="utf-8")

    return {"index_json": str(index_json), "index_md": str(index_md)}
