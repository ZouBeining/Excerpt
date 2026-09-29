r"""Clean a raw Merriam-Webster payload into the standard entry schema.

The MW response is deeply nested and its members are optional, so a renderer
cannot rely on any of them being present.  This module flattens the parts we
care about onto the fixed shape produced by :func:`common.config.empty_entry`,
which is identical for every dictionary.

Field mapping (MW -> standard)
------------------------------
``hwi.hw``                -> ``word``            (syllable marks removed)
``meta.lang``             -> ``language``
``meta.src``              -> ``dictionary``      (e.g. ``collegiate``)
``meta.section``          -> ``section``
``meta.offensive``        -> ``offensive``
``meta.stems``            -> ``stems``
``hwi.prs``               -> ``pronunciations``  (``mw`` + audio URL)
``fl``                    -> ``pos``
``lbs``                   -> ``labels``
``sls``                   -> ``subjectLabels``
``def[].sseq[][]``        -> ``senses``          (``pos`` / ``definition`` / ``examples``)
``shortdef``              -> ``shortDefs``
``ins``                   -> ``inflections``
``syns`` (thesaurus)      -> ``synonyms``
``meta.ants`` (thesaurus) -> ``antonyms``
``et``                    -> ``etymology``
``date``                  -> ``firstUse``
``uros`` / ``dros``       -> ``relatedHeadwords`` (run-on words and phrases)
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from common.config import empty_entry, normalize_entry

from .mw_markup import clean_text, strip_syllable_dots

__all__ = ["clean_entry", "is_entry_shaped", "looks_like_suggestions"]

#: MW audio files are looked up by folder, derived from the file name.
_AUDIO_BASE = "https://media.merriam-webster.com/audio/prons/en/us/mp3"


def looks_like_suggestions(payload: Any) -> bool:
    """MW returns a bare list of *strings* when the word is not in the dictionary."""
    return (
        isinstance(payload, list)
        and bool(payload)
        and all(isinstance(item, str) for item in payload)
    )


def is_entry_shaped(item: Any) -> bool:
    """Return whether *item* looks like an MW dictionary entry object."""
    return isinstance(item, dict) and ("hwi" in item or "meta" in item)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _audio_url(sound: Any) -> str:
    """Rebuild a playable audio URL from MW's ``sound`` object."""
    if not isinstance(sound, dict):
        return ""
    name = str(sound.get("audio") or "").strip()
    if not name:
        return ""

    if name.startswith("bix"):
        folder = "bix"
    elif name.startswith("gg"):
        folder = "gg"
    elif not name[0].isalpha():
        folder = "number"
    else:
        folder = name[0]

    return f"{_AUDIO_BASE}/{folder}/{name}.mp3"


def _first_string(value: Any) -> str:
    """Return the first non-empty cleaned string found inside *value*."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _first_string(item)
            if found:
                return found
    if isinstance(value, dict):
        for item in value.values():
            found = _first_string(item)
            if found:
                return found
    return ""


def _string_list(value: Any) -> list[str]:
    """Flatten deeply nested label/list structures into a flat string list."""
    found: list[str] = []
    if isinstance(value, str):
        text = clean_text(value)
        if text:
            found.append(text)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_string_list(item))
    return found


def _dot_joiner(tokens: dict[str, Any]) -> str:
    """Return the punctuation that should separate this token from the previous."""
    return str(tokens.get("pun") or "").strip()


def _join_parts(parts: Iterable[str]) -> str:
    """Join text fragments, collapsing stray spaces and doubled commas."""
    joined = " ".join(part for part in (p.strip() for p in parts) if part)
    return joined.replace(" ,", ",").strip()


def _clean_definition(text: str) -> str:
    """Normalise a sense definition.

    A Merriam-Webster sense body starts with the ``{bc}`` token (a bold colon)
    that separates the sense number from its wording, so ``clean_text`` turns
    ``"{bc}the head of a group"`` into ``": the head of a group"``.  The
    canonical schema in ``docs/template.words.json`` stores the wording alone,
    without that leading separator.  Interior colons — as in ``"one who
    directs: such as"`` — are preserved.
    """
    return re.sub(r"^\s*:\s*", "", str(text or "")).strip()


# ---------------------------------------------------------------------------
# Pronunciations
# ---------------------------------------------------------------------------

def _clean_pronunciations(*sources: Any) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for source in sources:
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            mw = clean_text(item.get("mw"))
            ipa = clean_text(item.get("ipa"))
            if not mw and not ipa:
                continue
            found.append(
                {
                    "mw": mw,
                    "ipa": ipa,
                    "label": clean_text(item.get("l")),
                    "label2": clean_text(item.get("l2")),
                    "pun": clean_text(item.get("pun")),
                    "audio": _audio_url(item.get("sound")),
                }
            )
    return found


# ---------------------------------------------------------------------------
# Senses
# ---------------------------------------------------------------------------

def _split_vis(payload: Any) -> list[str]:
    """Turn a ``vis`` payload into a list of example sentences."""
    examples: list[str] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                text = clean_text(item.get("t"))
            else:
                text = clean_text(item)
            if text:
                examples.append(text)
    return examples


def _collect_dt(
    dt: Any,
    default_pos: str,
    seen: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Walk one ``dt`` list, returning ``{pos, definition, examples}`` senses."""
    senses: list[dict[str, Any]] = []
    if not isinstance(dt, list):
        return senses

    pending_def: list[str] = []
    pending_examples: list[str] = []

    def flush() -> None:
        definition = _clean_definition(_join_parts(pending_def))
        if not definition and not pending_examples:
            pending_def.clear()
            pending_examples.clear()
            return
        key = (default_pos, definition)
        if key not in seen:
            seen.add(key)
            senses.append(
                {
                    "pos": default_pos,
                    "definition": definition,
                    "examples": list(pending_examples),
                }
            )
        pending_def.clear()
        pending_examples.clear()

    for item in dt if isinstance(dt, list) else []:
        if not isinstance(item, (list, tuple)) or not item:
            continue
        marker = item[0]
        payload = item[1] if len(item) > 1 else None

        if marker == "text":
            text = clean_text(payload)
            if text:
                pending_def.append(text)
        elif marker == "vis":
            pending_examples.extend(_split_vis(payload))
        elif marker in {"uns", "snote", "ri", "ca", "bnw", "hint", "vi"}:
            # Supplementary prose is not a definition; skip it so the sense
            # list stays clean.  (Revisit if a future template needs it.)
            continue
        else:
            text = clean_text(payload)
            if text:
                pending_def.append(text)

    flush()
    return senses


def _collect_senses(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ``def[].sseq`` into a flat, deduplicated sense list."""
    senses: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    # The entry-level part of speech is the fallback for every sense that only
    # carries a grammatical label (``sgram``) or has none at all.
    entry_pos = clean_text(entry.get("fl"))

    for block in entry.get("def") or []:
        if not isinstance(block, dict):
            continue
        vd = clean_text(block.get("vd")) or entry_pos
        for group in block.get("sseq") or []:
            for pair in group or []:
                if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    continue
                if pair[0] not in {"sense", "sen"}:
                    continue
                sense = pair[1]
                if not isinstance(sense, dict):
                    continue
                pos = clean_text(sense.get("sgram") or sense.get("fl") or vd)
                senses.extend(_collect_dt(sense.get("dt"), pos, seen))

    for runon in entry.get("dros") or []:
        if not isinstance(runon, dict):
            continue
        phrase = strip_syllable_dots(clean_text(runon.get("drp")))
        pos = clean_text(runon.get("fl")) or entry_pos
        label = phrase or pos
        for block in runon.get("def") or []:
            if not isinstance(block, dict):
                continue
            for group in block.get("sseq") or []:
                for pair in group or []:
                    if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                        continue
                    if pair[0] not in {"sense", "sen"}:
                        continue
                    sense = pair[1]
                    if isinstance(sense, dict):
                        senses.extend(
                            _collect_dt(sense.get("dt"), label, seen)
                        )
    return senses


# ---------------------------------------------------------------------------
# Assorted members
# ---------------------------------------------------------------------------

def _clean_inflections(entry: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for item in entry.get("ins") or []:
        if not isinstance(item, dict):
            continue
        form = strip_syllable_dots(clean_text(item.get("if") or item.get("ifc")))
        if not form:
            continue
        found.append({"form": form, "label": clean_text(item.get("il"))})
    return found


def _clean_thesaurus_words(groups: Any) -> list[str]:
    """Flatten thesaurus-style ``[[{"wd": ...}]]`` groups into words."""
    words: list[str] = []
    if not isinstance(groups, list):
        return words
    for item in groups:
        words.extend(_thesaurus_words(item))
    return words


def _thesaurus_words(item: Any) -> list[str]:
    if isinstance(item, dict):
        word = clean_text(item.get("wd"))
        return [word] if word else []
    if isinstance(item, (list, tuple)):
        found: list[str] = []
        for sub in item:
            found.extend(_thesaurus_words(sub))
        return found
    return []


def _clean_etymology(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in entry.get("et") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            parts.append(clean_text(item[1]))
    return _join_parts(parts)


def _clean_first_use(entry: dict[str, Any]) -> str:
    date = entry.get("date")
    if isinstance(date, dict):
        # Some references return the date as ``{"text": ...}``.
        date = date.get("text") or ""
    return clean_text(date)


def _clean_related_headwords(entry: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for runon in entry.get("uros") or []:
        if isinstance(runon, dict):
            word = strip_syllable_dots(clean_text(runon.get("ure")))
            if word and word not in found:
                found.append(word)
    for runon in entry.get("dros") or []:
        if isinstance(runon, dict):
            phrase = strip_syllable_dots(clean_text(runon.get("drp")))
            if phrase and phrase not in found:
                found.append(phrase)
    return found


def _clean_short_defs(entry: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for item in entry.get("shortdef") or []:
        text = clean_text(item)
        if text and text not in found:
            found.append(text)
    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_entry(item: dict[str, Any], *, word: str = "") -> dict[str, Any]:
    """Map one raw MW entry object onto the standard entry schema."""
    result = empty_entry()

    meta = item.get("meta") or {}
    hwi = item.get("hwi") or {}

    headword = strip_syllable_dots(clean_text(hwi.get("hw"))) or str(word).strip()

    result["word"] = headword
    result["language"] = clean_text(meta.get("lang")) or "en"
    result["dictionary"] = clean_text(meta.get("src")) or "collegiate"
    result["section"] = clean_text(meta.get("section")) or "alpha"
    result["offensive"] = bool(meta.get("offensive", False))
    result["stems"] = _string_list(meta.get("stems"))
    result["homographs"] = int(item.get("hom") or 0)

    homograph = 0
    entry_id = str(meta.get("id") or "")
    if ":" in entry_id:
        try:
            homograph = int(entry_id.rsplit(":", 1)[1])
        except ValueError:
            homograph = 0
    result["homograph"] = homograph

    result["pronunciations"] = _clean_pronunciations(
        hwi.get("prs"), item.get("prs")
    )
    result["pos"] = clean_text(item.get("fl")) or clean_text(hwi.get("psl"))
    result["labels"] = _string_list(item.get("lbs"))
    result["subjectLabels"] = _string_list(item.get("sls"))
    result["senses"] = _collect_senses(item)
    result["shortDefs"] = _clean_short_defs(item)
    result["inflections"] = _clean_inflections(item)
    result["synonyms"] = _clean_thesaurus_words(meta.get("syns")) or _clean_thesaurus_words(item.get("syns"))
    result["antonyms"] = _clean_thesaurus_words(meta.get("ants"))
    result["etymology"] = _clean_etymology(item)
    result["firstUse"] = _clean_first_use(item)
    result["relatedHeadwords"] = _clean_related_headwords(item)

    return normalize_entry(result)
