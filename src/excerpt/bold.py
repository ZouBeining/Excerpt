r"""Bold-span helpers shared by the extractor and the renderers.

Why this module exists
----------------------
``extractor.find_sentence`` keeps the ``**`` markers of *every* bolded span in
the sentence it returns, so one sentence may hold several bolds and each record
whose word lives in that sentence carries the very same ``sentence`` string.
Rendering that string verbatim marks all of them at once, which is wrong: only
the span belonging to the record at hand may be marked.

The rule is expressed once here and reused by ``excerpt.extractor``,
``excerpt.write_md`` and ``excerpt.latex``, instead of being duplicated (and
diverging) inside each renderer.

Matching rule
-------------
A bolded span is marked when its normalised text equals the normalised ``word``
— or ``lemma_from``, because a lemma record stores the base form in ``word``
while the sentence bolds the original inflected form.  When nothing matches:

* exactly one bolded span -> mark it (it is still what the sentence bolds);
* several bolded spans    -> mark none, because guessing would reintroduce the
  very bug this module fixes.

A sentence without any bold marker is returned untouched: the Markdown renderer
must never invent a ``**`` that the source did not have.
"""

from __future__ import annotations

import re
from typing import Iterable

__all__ = [
    "BOLD_RE",
    "normalize_bold_target",
    "render_markdown_sentence",
    "split_bold",
    "target_bold_segments",
]

#: Bold spans: ``**...**`` or ``__...__``, no leading/trailing whitespace inside.
#: Kept identical to the regex the extractor parses with, so a span is always
#: recognised the same way on the way in and on the way out.
BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)


def normalize_bold_target(text: str) -> str:
    """Return the comparison key for a bolded span or an entry word."""
    return str(text or "").strip().lower().strip("'-")


def split_bold(sentence: str) -> list[tuple[str, bool]]:
    """Split *sentence* into ``(chunk, is_bold)`` pairs, markers stripped.

    The bolded frames are removed from the text; a chunk flagged ``True`` is the
    inner text of a bolded span.  Text without markers yields a single chunk.
    """
    text = str(sentence or "")
    if "**" not in text and "__" not in text:
        return [(text, False)] if text else []

    parts: list[tuple[str, bool]] = []
    position = 0
    for match in BOLD_RE.finditer(text):
        if match.start() > position:
            parts.append((text[position : match.start()], False))
        parts.append((match.group(2), True))
        position = match.end()

    if position < len(text):
        parts.append((text[position:], False))
    return parts


def target_bold_segments(
    sentence: str,
    words: Iterable[str] = (),
) -> tuple[list[tuple[str, bool]], bool]:
    """Return ``(segments, had_markers)`` with only the target span marked.

    ``words`` holds the candidate spellings of the record's headword — normally
    ``(entry.word, entry.lemma_from)``.  ``had_markers`` tells the caller whether
    the sentence carried any bold marker at all, which the Markdown renderer
    needs in order to leave unmarked sentences alone.
    """
    text = str(sentence or "")
    parts = split_bold(text)

    bold_count = sum(1 for _, is_bold in parts if is_bold)
    if bold_count == 0:
        return parts, False

    targets = {
        key for key in (normalize_bold_target(word) for word in words) if key
    }

    marked: list[bool] = []
    for chunk, is_bold in parts:
        hit = is_bold and normalize_bold_target(chunk) in targets
        marked.append(hit)

    if not any(marked) and bold_count == 1:
        marked = [is_bold for _, is_bold in parts]

    return [
        (chunk, bool(flag)) for (chunk, _), flag in zip(parts, marked)
    ], True


def render_markdown_sentence(sentence: str, words: Iterable[str] = ()) -> str:
    """Mark only the target bolded span with ``**``, unmark the rest.

    A sentence that carried no bold marker is returned verbatim.
    """
    text = str(sentence or "")
    parts, had_markers = target_bold_segments(text, words)
    if not had_markers:
        return text
    return "".join(
        f"**{chunk}**" if is_bold else chunk for chunk, is_bold in parts
    )
