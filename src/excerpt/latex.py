r"""Render the LaTeX handout from the final ``.words.json``.

The output directory receives three things:

* ``preamble.excerpt.tex`` and ``main.tex``, copied verbatim from the package
  (``main.tex``'s ``\subfile{entries/__ENTRIES__}`` placeholder is rewritten);
* ``entries/{title}.tex`` holding one block per record;
* nothing else — the caller compiles with ``xelatex``.

Two jobs dominate this module's complexity:

1. **Escaping.** English definitions contain ``_``, ``%``, ``&``, ``$``, ``#``
   and curly quotes; all of them must become LaTeX-safe before they are
   written, or the document will not compile.
2. **Unicode.** The preamble loads ``xelatex`` with Times New Roman and SimSun,
   so IPA characters and CJK text pass through untouched — they must *not* be
   stripped.

The console entry point ``excerpt-latex`` accepts either a ``.words.json`` or a
rendered ``.md`` file, so a note that was already produced can be re-typeset
without re-running the lookup.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from common.config import (
    Artifacts,
    artifacts,
    get_dict_config,
    get_session_settings,
    normalize_title,
)

from .bold import target_bold_segments

__all__ = [
    "build",
    "compile_tex",
    "convert",
    "escape_latex",
    "find_xelatex",
    "main",
    "run",
    "write_tex",
]

#: Package directory holding the static TeX files.
_PACKAGE_DIR = Path(__file__).resolve().parent

#: Placeholder inside ``main.tex``.
_ENTRIES_PLACEHOLDER = "__ENTRIES__"

#: Characters with a special meaning in LaTeX.  ``\`` is handled separately
#: because its replacement itself contains braces.
_CHAR_MAP = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

#: Typographic characters mapped to their LaTeX equivalents.
_SMART_MAP = {
    "\u2018": "`",
    "\u2019": "'",
    "\u201c": "``",
    "\u201d": "''",
    "\u2013": "--",
    "\u2014": "---",
    "\u2026": r"\ldots{}",
    "\u00a0": "~",
}

# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------

def escape_latex(value: Any) -> str:
    r"""Return *value* safe to drop into a LaTeX document.

    Backslashes are handled in a separate pass, by swapping them for a private
    sentinel first.  Escaping ``\`` with the rest of the characters would let
    the braces in ``\textbackslash{}`` be escaped again into ``\{\}``.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(escape_latex(item) for item in value if item is not None)

    text = str(value)
    for source, replacement in _SMART_MAP.items():
        text = text.replace(source, replacement)

    sentinel = "\x01"
    text = text.replace("\\", sentinel)
    for source, replacement in _CHAR_MAP.items():
        text = text.replace(source, replacement)

    return text.replace(sentinel, r"\textbackslash{}")


# ---------------------------------------------------------------------------
# Sentence rendering
# ---------------------------------------------------------------------------

def render_sentence(sentence: str, word: str, lemma_from: str = "") -> str:
    r"""Escape *sentence* and underline *this* record's span with ``\wmark``.

    A sentence may bold several records; only the span matching ``word`` — or
    ``lemma_from``, since a lemma record stores the base form while the sentence
    bolds the inflected one — is underlined.  The other bolds stay plain.
    """
    text = str(sentence or "")
    if not text:
        return escape_latex(word)

    # The extractor keeps the ``**`` markers in ``sentence``; rely on them.
    parts, had_markers = target_bold_segments(text, (word, lemma_from))
    if had_markers:
        return "".join(
            f"\\wmark{{{escape_latex(chunk)}}}" if is_bold else escape_latex(chunk)
            for chunk, is_bold in parts
        )

    escaped = escape_latex(text)
    escaped_word = escape_latex(word)
    if escaped_word and escaped_word in escaped:
        return escaped.replace(escaped_word, f"\\wmark{{{escaped_word}}}", 1)

    return escaped


# ---------------------------------------------------------------------------
# Entry blocks
# ---------------------------------------------------------------------------

def _render_entry(entry: dict[str, Any], index: int) -> list[str]:
    """Render one record as LaTeX commands from the preamble."""
    payload = entry.get("entry") or {}
    word = str(entry.get("word") or "").strip()
    lemma_from = str(entry.get("lemma_from") or "").strip()
    sentence = render_sentence(entry.get("sentence") or "", word, lemma_from)

    lines = [f"\\whead{{{escape_latex(word)}}}", f"\\wsent{{{sentence}}}"]

    senses = payload.get("senses") or []
    if not senses:
        # No definition available: leave a blank for hand annotation, which is
        # exactly what \wblank exists for.
        lines.append("\\wblank")
    else:
        for sense in senses:
            if not isinstance(sense, dict):
                continue
            definition = str(sense.get("definition") or "").strip()
            pos = str(sense.get("pos") or "").strip() or "—"
            if not definition:
                continue
            lines.append(
                f"\\wsense{{{escape_latex(pos)}}}{{{escape_latex(definition)}}}"
            )
            for example in sense.get("examples") or []:
                text = str(example).strip()
                if text:
                    lines.append(f"\\wex{{{escape_latex(text)}}}")

    for label, key in (
        ("Inflections", "inflections"),
        ("Syn.", "synonyms"),
        ("Anton.", "antonyms"),
        ("Etymology", "etymology"),
        ("First Use", "firstUse"),
    ):
        lines.extend(_render_tag(label, payload.get(key)))

    return lines


def _render_tag(label: str, value: Any) -> list[str]:
    r"""Render one ``\wtag{label}{content}`` line, if there is content."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        pieces: list[str] = []
        for item in value:
            if isinstance(item, dict):
                form = str(item.get("form") or "").strip()
                item_label = str(item.get("label") or "").strip()
                if form:
                    pieces.append(f"{item_label} {form}".strip() if item_label else form)
            else:
                text = str(item).strip()
                if text:
                    pieces.append(text)
        content = ", ".join(pieces)
    else:
        content = str(value).strip()

    if not content:
        return []
    return [f"\\wtag{{{escape_latex(label)}}}{{{escape_latex(content)}}}"]


def _render_entries_tex(entries: Sequence[dict[str, Any]], art: Artifacts) -> str:
    """Render the full ``entries/{title}.tex`` subfile."""
    lines = [
        "% Generated by excerpt — do not edit by hand.",
        r"\documentclass[../main.tex]{subfiles}",
        r"\begin{document}",
        "",
    ]
    for i, entry in enumerate(entries, start=1):
        lines.extend(_render_entry(entry, i))
        lines.append("")
    lines.extend([r"\end{document}", ""])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

def _copy_static(art: Artifacts, tex_dir: Path, title: str) -> tuple[Path, Path]:
    """Copy ``main.tex`` and the preamble, rewriting the subfile placeholder."""
    main_source = _PACKAGE_DIR / "main.tex"
    preamble_source = _PACKAGE_DIR / "preamble.excerpt.tex"

    main_text = main_source.read_text(encoding="utf-8")
    main_text = main_text.replace(_ENTRIES_PLACEHOLDER, title)
    # The copy sits next to the preamble, so the relative \input must be fixed.
    main_text = main_text.replace(
        r"\input{../preamble.excerpt}", r"\input{preamble.excerpt}"
    )

    main_target = tex_dir / art.main_tex
    main_target.write_text(main_text, encoding="utf-8")

    preamble_target = tex_dir / art.preamble_tex
    shutil.copyfile(preamble_source, preamble_target)

    return main_target, preamble_target


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

#: MiKTeX / TeX Live install locations probed when ``xelatex`` is not on PATH.
_XELATEX_FALLBACK_DIRS = (
    r"E:\MiKTeX\miktex\bin\x64",
    r"C:\Program Files\MiKTeX\miktex\bin\x64",
    r"C:\Program Files (x86)\MiKTeX\miktex\bin\x64",
    r"C:\texlive\2024\bin\windows",
    r"C:\texlive\2025\bin\windows",
)


def find_xelatex(explicit: str | Path | None = None) -> str | None:
    """Return a usable ``xelatex`` command, or ``None`` when unavailable.

    Resolution order: an explicit path/name, then ``PATH``, then the common
    MiKTeX and TeX Live install directories.  Returning ``None`` instead of
    raising lets callers degrade to "tex only" without failing the pipeline.
    """
    if explicit:
        candidate = Path(str(explicit))
        if candidate.is_file():
            return str(candidate)
        # A bare name may still be resolvable through PATH.
        resolved = shutil.which(str(explicit))
        if resolved:
            return resolved
        return None

    resolved = shutil.which("xelatex")
    if resolved:
        return resolved

    env_dir = str(get_session_settings().get("xelatex") or "").strip()
    if env_dir:
        candidate = Path(env_dir)
        if candidate.is_file():
            return str(candidate)
        candidate = candidate / "xelatex.exe"
        if candidate.is_file():
            return str(candidate)

    for directory in _XELATEX_FALLBACK_DIRS:
        for name in ("xelatex.exe", "xelatex"):
            candidate = Path(directory) / name
            if candidate.is_file():
                return str(candidate)

    return None


def compile_tex(
    tex_dir: str | Path,
    *,
    main_tex: str = "main.tex",
    xelatex: str | Path | None = None,
    passes: int = 2,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Compile ``main.tex`` to PDF and return a summary dict.

    Two passes are the default because the cross-references and the table of
    contents are only stable after the second run.  The program is invoked with
    ``-interaction=nonstopmode`` so a missing glyph cannot block on stdin and
    hang a non-interactive caller.

    Never raises on a LaTeX error: the returned ``ok`` flag and ``log`` carry
    the diagnosis, and ``pdf`` is ``None`` unless a PDF was actually produced.
    """
    directory = Path(tex_dir)
    command = find_xelatex(xelatex)

    if command is None:
        return {
            "ok": False,
            "pdf": None,
            "command": None,
            "passes": 0,
            "skipped": True,
            "log": "",
            "reason": "xelatex not found; install MiKTeX or TeX Live, or pass --xelatex",
        }

    if not (directory / main_tex).is_file():
        return {
            "ok": False,
            "pdf": None,
            "command": command,
            "passes": 0,
            "skipped": False,
            "log": "",
            "reason": f"{main_tex} not found in {directory}",
        }

    argv = [
        command,
        "-interaction=nonstopmode",
        "-halt-on-error",
        main_tex,
    ]

    combined: list[str] = []
    ok = True
    used = 0
    for _ in range(max(1, passes)):
        used += 1
        try:
            completed = subprocess.run(
                argv,
                cwd=str(directory),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "ok": False,
                "pdf": None,
                "command": command,
                "passes": used,
                "skipped": False,
                "log": "".join(combined),
                "reason": f"could not run xelatex: {exc}",
            }

        output = completed.stdout.decode("utf-8", errors="replace")
        combined.append(output)
        if completed.returncode != 0:
            ok = False
            break

    pdf_path = directory / f"{Path(main_tex).stem}.pdf"
    produced = pdf_path if pdf_path.is_file() else None

    return {
        "ok": ok and produced is not None,
        "pdf": str(produced) if produced else None,
        "command": command,
        "passes": used,
        "skipped": False,
        "log": "\n".join(combined),
        "reason": "" if (ok and produced) else _first_error("".join(combined)),
    }


def _first_error(log: str) -> str:
    """Pull the first LaTeX error line out of a compile log."""
    for line in log.splitlines():
        if line.startswith("!"):
            return line.strip()
        if "not found" in line and "font" in line.lower():
            return line.strip()
    return "compilation failed; see the log for details"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build(
    entries: Sequence[dict[str, Any]],
    *,
    title: str,
    tex_dir: str | Path,
    dict_slug: str = "",
    compile_pdf: bool = False,
    xelatex: str | Path | None = None,
) -> dict[str, Any]:
    """Write every LaTeX file for *entries* and return their paths.

    When *compile_pdf* is set, ``xelatex`` runs twice and the resulting PDF
    path is added under the ``pdf`` key.  A missing ``xelatex`` is not an
    error: the ``.tex`` files are still written and ``compile`` reports why the
    PDF was skipped.
    """
    resolved_dir = Path(tex_dir)
    resolved_dir.mkdir(parents=True, exist_ok=True)

    art = artifacts(title, dict_slug, str(resolved_dir))
    entries_dir = resolved_dir / art.entries_dir
    entries_dir.mkdir(parents=True, exist_ok=True)

    unit_path = entries_dir / art.entries_tex
    unit_path.write_text(_render_entries_tex(entries, art), encoding="utf-8")

    main_path, preamble_path = _copy_static(art, resolved_dir, art.title)

    result: dict[str, Any] = {
        "main": str(main_path),
        "preamble": str(preamble_path),
        "unit": str(unit_path),
        "entries": len(entries),
        "title": art.title,
        "pdf": None,
        "compile": None,
    }

    if compile_pdf:
        result["compile"] = compile_tex(
            resolved_dir, main_tex=main_path.name, xelatex=xelatex
        )
        result["pdf"] = result["compile"]["pdf"]

    return result


def write_tex(
    entries: Sequence[dict[str, Any]],
    art: Artifacts,
    *,
    dict_slug: str = "",
    compile_pdf: bool = False,
    xelatex: str | Path | None = None,
) -> dict[str, Any]:
    """Pipeline entry: build the LaTeX documents into ``art.out_dir``."""
    return build(
        entries,
        title=art.title,
        tex_dir=art.out_dir,
        dict_slug=dict_slug or art.dict_slug,
        compile_pdf=compile_pdf,
        xelatex=xelatex,
    )


def run(
    entries: Sequence[dict[str, Any]],
    art: Artifacts,
    *,
    dict_slug: str = "",
    compile_pdf: bool = False,
    xelatex: str | Path | None = None,
):
    """Alias of :func:`write_tex` for pipeline uniformity."""
    return write_tex(
        entries,
        art,
        dict_slug=dict_slug,
        compile_pdf=compile_pdf,
        xelatex=xelatex,
    )


def load_entries(path: str | Path) -> list[dict[str, Any]]:
    """Read records from either a ``.words.json`` or a rendered ``.md`` file."""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"input file not found: {target}")

    if target.suffix.lower() == ".json":
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"expected a JSON array in {target}")
        return [item for item in raw if isinstance(item, dict)]

    return parse_note_md(target.read_text(encoding="utf-8"))


def parse_note_md(text: str) -> list[dict[str, Any]]:
    """Best-effort reconstruction of records from a rendered Markdown note."""
    records: list[dict[str, Any]] = []
    blocks = re.split(r"^###\s+\d+\.\s+", text, flags=re.MULTILINE)[1:]

    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        word = lines[0].strip()
        sentence = ""
        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith(">"):
                sentence = stripped.lstrip("> ").strip()
                break

        definition = ""
        examples: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- *"):
                definition = stripped.split("* ", 1)[-1].strip()
            elif stripped.startswith(f"  - {MD_EXAMPLE_PREFIX}"):
                examples.append(stripped.split("：", 1)[-1].strip())

        records.append(
            {
                "word": word,
                "type": "word",
                "sentence": sentence,
                "line": 0,
                "code": 0,
                "entry": {
                    "word": word,
                    "senses": [
                        {
                            "pos": "",
                            "definition": definition,
                            "examples": examples,
                        }
                    ]
                    if definition
                    else [],
                },
            }
        )
    return records


#: Prefix used for example bullets by :mod:`excerpt.write_md`.
MD_EXAMPLE_PREFIX = "E.g."


def convert(
    source: str | Path,
    *,
    tex_dir: str | Path | None = None,
    title: str | None = None,
    dict_slug: str = "",
    compile_pdf: bool = False,
    xelatex: str | Path | None = None,
) -> dict[str, Any]:
    """Convert a ``.words.json`` or ``.md`` file into LaTeX documents."""
    source_path = Path(source)
    entries = load_entries(source_path)
    resolved_title = normalize_title(title or source_path.stem)
    target_dir = Path(tex_dir) if tex_dir else source_path.parent
    return build(
        entries,
        title=resolved_title,
        tex_dir=target_dir,
        dict_slug=dict_slug or get_dict_config()["slug"],
        compile_pdf=compile_pdf,
        xelatex=xelatex,
    )


# ---------------------------------------------------------------------------
# Console entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """``excerpt-latex``: turn a ``.words.json`` or ``.md`` file into LaTeX."""
    parser = argparse.ArgumentParser(
        prog="excerpt-latex",
        description=(
            "Convert a standard .words.json (or a rendered .md note) into a "
            "LaTeX document set."
        ),
    )
    parser.add_argument("source", type=Path, help="A .words.json or .md file")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the generated .tex files (default: the source's)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Title used for entries/<title>.tex (default: the source stem)",
    )
    parser.add_argument(
        "--dict-choice",
        default=None,
        help="Dictionary slug used in file names (default: the active one)",
    )
    parser.add_argument(
        "--compile",
        dest="compile_latex",
        action="store_true",
        default=False,
        help="Additionally compile the .tex into a PDF with xelatex",
    )
    parser.add_argument(
        "--xelatex",
        default=None,
        help="Path to the xelatex executable (default: found on PATH)",
    )
    args = parser.parse_args(argv)

    try:
        result = convert(
            args.source,
            tex_dir=args.output_dir,
            title=args.title,
            dict_slug=args.dict_choice or "",
            compile_pdf=args.compile_latex,
            xelatex=args.xelatex,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"[error] {exc}")
        return 1

    print(f"[write] {result['main']}")
    print(f"[write] {result['preamble']}")
    print(f"[write] {result['unit']}")

    if args.compile_latex:
        return _report_compile(result)

    print(
        f"[tex] {result['entries']} entries in total; compile with: "
        f'cd "{Path(args.output_dir) if args.output_dir else args.source.parent}" '
        "&& xelatex main.tex (twice)"
    )
    return 0


def _report_compile(result: dict[str, Any]) -> int:
    """Print the outcome of a compile request and return the exit code."""
    info = result.get("compile") or {}
    if info.get("skipped"):
        print(f"[tex] skipped compilation: {info.get('reason')}")
        return 0
    if not info.get("ok"):
        print(f"[tex] compilation failed: {info.get('reason')}")
        return 1

    print(f"[pdf] {result['pdf']}")
    print(f"[tex] compiled with {info.get('passes')} xelatex pass(es)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
