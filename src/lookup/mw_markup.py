r"""Turn Merriam-Webster markup into plain text.

MW strings are peppered with inline tokens: formatting pairs such as
``{it}...{/it}``, punctuation shorthands such as ``{bc}``, and cross-reference
tokens such as ``{a_link|backward}``.  They must all be resolved *before* the
text can be shown or escaped for LaTeX, otherwise the braces leak into the
output.

The token tables below are transcribed from the "Running text tokens" section
of ``docs/api_response.template.json``.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["clean_text", "has_unresolved_token", "strip_syllable_dots"]

# -- formatting -------------------------------------------------------------

_PAIRS: tuple[tuple[str, str], ...] = (
    ("{b}", "{/b}", ""),
    ("{inf}", "{/inf}", ""),
    ("{it}", "{/it}", ""),
    ("{sc}", "{/sc}", ""),
    ("{sup}", "{/sup}", ""),
    ("{sub}", "{/sub}", ""),
    ("{bit}", "{/bit}", ""),
    ("{itsc}", "{/itsc}", ""),
    ("{rom}", "{/rom}", ""),
    ("{gloss}", "{/gloss}", "["),
    ("{parahw}", "{/parahw}", ""),
    ("{phrase}", "{/phrase}", ""),
    ("{qword}", "{/qword}", ""),
    ("{wi}", "{/wi}", ""),
    ("{dx}", "{/dx}", "— "),
    ("{dx_def}", "{/dx_def}", "("),
    ("{dx_ety}", "{/dx_ety}", "— "),
    ("{ma}", "{/ma}", ""),
)

# ``{dx_def}x{/dx_def}`` closes with a parenthesis instead of its opener.
_PAIR_CLOSERS: dict[str, str] = {
    "{dx_def}": ")",
    "{dx}": "",
    "{dx_ety}": "",
    "{ma}": "",
}

_SINGLES: tuple[tuple[str, str], ...] = (
    ("{bc}", " : "),
    ("{ldquo}", "\u201c"),
    ("{rdquo}", "\u201d"),
    ("{p_br}", " "),
    ("{ds}", ""),
    ("{ds|||}", ""),
)

# ``{ds|t|1|a|1}`` and friends: drop the whole date-sense token.
_DS_RE = re.compile(r"\{ds\|[^}]*\}")

# Cross-reference tokens: ``{a_link|x}``, ``{d_link|x|y}``, ``{sx|x|y|1a}`` …
_XREF_RE = re.compile(
    r"\{(a_link|d_link|i_link|et_link|mat|sx|dxt|dx_def|dx_ety)\|([^}]*)\}"
)

#: ``{ma}{mat|fly|}{/ma}`` renders as "more at fly".
_MA_RE = re.compile(r"\{ma\}(?:\{mat\|([^|}]*)(?:\|[^}]*)?\})?\{/ma\}")

#: Any token left over, used for the sanity check.
_LEFTOVER_RE = re.compile(r"\{/?(?:[a-z_]+)(?:\|[^}]*)?\}")

#: MW marks stressed syllables with ``*`` (an optional ``-`` follows it).
_SYLLABLE_RE = re.compile(r"\*|-")


def _xrepl(match: re.Match[str]) -> str:
    """Resolve one cross-reference token into its display text."""
    kind, payload = match.group(1), match.group(2)
    fields = payload.split("|")
    text = fields[0].strip()

    if kind == "sx" and len(fields) >= 3:
        number = fields[2].strip()
        if number:
            return f"{text} {number}"
    return text


def clean_text(value: Any) -> str:
    """Return *value* as plain text with every MW token resolved."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        # Join list fragments with a space so ``["a ", "b"]`` keeps its
        # word boundary instead of fusing into ``ab``.
        joined = " ".join(clean_text(part) for part in value)
        return re.sub(r"\s+", " ", joined).strip()
    if not isinstance(value, str):
        return str(value)

    text = value

    text = _MA_RE.sub(lambda m: f"more at {m.group(1)}" if m.group(1) else "", text)
    text = _DS_RE.sub("", text)

    for opener, closer, prefix in _PAIRS:
        text = text.replace(opener, prefix)
        text = text.replace(closer, _PAIR_CLOSERS.get(opener, ""))

    for token, replacement in _SINGLES:
        text = text.replace(token, replacement)

    text = _XREF_RE.sub(_xrepl, text)

    # Stray braces from tokens we do not model: drop the braces, keep the text.
    text = _LEFTOVER_RE.sub(lambda m: m.group(0).strip("{}").split("|")[-1], text)

    # ``{bc}`` lands between two words with no surrounding space
    # (``"one who directs{bc}such as"``); normalise the spacing it produced.
    text = re.sub(r"\s*:\s*", ": ", text)

    return re.sub(r"\s+", " ", text).strip()


def strip_syllable_dots(value: str) -> str:
    """Remove MW syllable separators from a headword or inflected form."""
    return _SYLLABLE_RE.sub("", str(value or "")).strip()


def has_unresolved_token(value: str) -> bool:
    """Return whether a token survived :func:`clean_text` (a safety net)."""
    return bool(_LEFTOVER_RE.search(str(value or "")))
