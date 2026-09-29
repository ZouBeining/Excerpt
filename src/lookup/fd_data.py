r"""Clean a raw Free Dictionary (UApiPro) payload into the standard schema.

The UApiPro response is deliberately "dynamic": any section without content is
simply omitted.  This module therefore treats every member as optional and
always emits the fixed key set from :func:`common.config.empty_entry`.

Field mapping (UApiPro -> standard)
-----------------------------------
``word``                    -> ``word``
``language``                -> ``language``  (``dictionary`` is fixed to ``uapis``)
``phonetics``               -> ``pronunciations``
``english_definitions[]``   -> ``senses``     (definition + examples)
``definitions[]``           -> ``shortDefs``  (Chinese glosses)
``word_forms[]``            -> ``inflections``
``phrases[].phrase``        -> ``relatedHeadwords`` (collocations)
``synonyms[].words[]``      -> ``synonyms``
``examples[]``              -> example sentences appended to the first sense
``exam_tags``               -> ``labels``
"""

from __future__ import annotations

from typing import Any

from common.config import empty_entry, normalize_entry

__all__ = ["clean_entry", "is_entry_shaped"]

#: Value written to ``entry.dictionary`` for this source.
DICTIONARY_NAME = "uapis"


def is_entry_shaped(item: Any) -> bool:
    """Return whether *item* looks like a UApiPro ``entry`` object."""
    return isinstance(item, dict) and isinstance(item.get("word"), str)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = _text(value)
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        found: list[str] = []
        for item in value:
            found.extend(_string_list(item))
        return found
    return []


def _clean_pronunciations(raw: Any) -> list[dict[str, str]]:
    """Flatten ``phonetics`` (a dict keyed by accent) into a list."""
    if not isinstance(raw, dict):
        return []

    entries: list[dict[str, str]] = []
    for accent, payload in raw.items():
        if isinstance(payload, dict):
            text = _text(payload.get("text"))
            audio = _text(payload.get("audio"))
            label = _text(payload.get("label")) or _text(accent)
        else:
            text, audio, label = _text(payload), "", _text(accent)
        if not (text or audio):
            continue
        entries.append(
            {
                "mw": "",
                "ipa": text,
                # The accent (uk/us) doubles as the pronunciation label.
                "label": label.upper(),
                "label2": "",
                "pun": "",
                "audio": audio,
            }
        )
    return entries


def _clean_senses(raw: Any) -> list[dict[str, Any]]:
    """Map ``english_definitions`` onto the standard sense list."""
    senses: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return senses

    for item in raw:
        if not isinstance(item, dict):
            continue
        definition = _text(item.get("definition"))
        examples = [
            example
            for example in _string_list(item.get("examples"))
            if example
        ]
        if not definition and not examples:
            continue
        senses.append(
            {
                "pos": _text(item.get("part_of_speech")),
                "definition": definition,
                "examples": examples,
            }
        )

    if senses and isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            for example in _string_list(item.get("examples")):
                if example and example not in senses[0]["examples"]:
                    senses[0]["examples"].append(example)
    return senses


def _clean_examples(raw: Any) -> list[str]:
    """Collect bilingual example sentences, keeping the English side."""
    found: list[str] = []
    if not isinstance(raw, list):
        return found
    for item in raw:
        if isinstance(item, dict):
            source = _text(item.get("source"))
            if source and source not in found:
                found.append(source)
    return found


def _clean_inflections(raw: Any) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return found
    for item in raw:
        if not isinstance(item, dict):
            continue
        form = _text(item.get("value"))
        if not form:
            continue
        found.append({"form": form, "label": _text(item.get("name"))})
    return found


def _clean_related(raw: Any) -> list[str]:
    found: list[str] = []
    if not isinstance(raw, list):
        return found
    for item in raw:
        if isinstance(item, dict):
            phrase = _text(item.get("phrase"))
            if phrase and phrase not in found:
                found.append(phrase)
    return found


def _clean_synonyms(raw: Any) -> list[str]:
    found: list[str] = []
    if not isinstance(raw, list):
        return found
    for item in raw:
        if isinstance(item, dict):
            for word in _string_list(item.get("words")):
                if word and word not in found:
                    found.append(word)
    return found


def _clean_short_defs(raw: Any) -> list[str]:
    """Use the Chinese glosses as compact definitions."""
    found: list[str] = []
    if not isinstance(raw, list):
        return found
    for item in raw:
        if isinstance(item, dict):
            meaning = _text(item.get("meaning"))
            if meaning and meaning not in found:
                found.append(meaning)
    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_entry(item: dict[str, Any], *, word: str = "") -> dict[str, Any]:
    """Map one raw UApiPro entry onto the standard entry schema."""
    result = empty_entry()

    if not isinstance(item, dict):
        result["word"] = str(word).strip()
        return normalize_entry(result)

    result["word"] = _text(item.get("word")) or str(word).strip()
    result["language"] = _text(item.get("language")) or "en"
    result["dictionary"] = DICTIONARY_NAME
    result["section"] = "alpha"
    result["offensive"] = False

    result["pronunciations"] = _clean_pronunciations(item.get("phonetics"))
    result["labels"] = _string_list(item.get("exam_tags"))
    result["subjectLabels"] = []

    senses = _clean_senses(item.get("english_definitions"))
    extra_examples = _clean_examples(item.get("examples"))
    if senses and extra_examples:
        for example in extra_examples:
            if example not in senses[0]["examples"]:
                senses[0]["examples"].append(example)

    result["senses"] = senses
    result["shortDefs"] = _clean_short_defs(item.get("definitions"))
    result["inflections"] = _clean_inflections(item.get("word_forms"))
    result["synonyms"] = _clean_synonyms(item.get("synonyms"))
    result["antonyms"] = []
    result["etymology"] = ""
    result["firstUse"] = ""
    result["relatedHeadwords"] = _clean_related(item.get("phrases"))
    result["pos"] = senses[0]["pos"] if senses else ""
    result["homographs"] = 0
    result["homograph"] = 0
    result["stems"] = []

    return normalize_entry(result)
