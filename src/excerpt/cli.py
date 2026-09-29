r"""The ``excerpt`` pipeline.

Five stages run in a fixed order, exactly as AGENTS.md requires:

1. ``excerpt.extractor``   parse the Markdown, write ``.index.json`` / index ``.md``
2. ``lookup``              query the dictionary, write ``.words.json`` / ``.errors.json``
3. ``excerpt.llm``         complete non-word entries, update both JSON files
4. ``excerpt.write_md``    render ``.md`` and ``.index.md`` from the final data
5. ``excerpt.latex``       render the LaTeX document set

This module deliberately contains **no** dictionary-choice logic: everything
that depends on the chosen dictionary comes from ``common.config``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from common import config
from common.config import (
    CODE_OK,
    artifacts,
    default_out_dir_name,
    normalize_title,
)

from . import llm, write_md
from .arg import build_parser
from .extractor import extract_bold_entries, write_index


def _resolve_out_dir(args, title: str, source: Path) -> Path:
    """Pick the output directory: CLI > ``OUTPUT_DIR`` > ``.{title}_out``."""
    if args.output_dir is not None:
        return args.output_dir

    configured = str(config.get_session_settings().get("env_out_dir") or "").strip()
    if configured:
        return Path(configured)

    return source.parent / default_out_dir_name(title)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config.configure(
            dict_choice=args.dict_choice,
            use_llm=args.use_llm,
            env_path=str(args.env_file) if args.env_file else None,
            timeout=args.timeout,
            retry=args.retry,
            delay=args.delay,
            proxy=args.proxy,
            cache_path=str(args.cache_path) if args.cache_path else None,
            use_cache=not args.no_cache,
        )
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    source: Path = args.source
    if not source.is_file():
        print(f"[error] Source file doesn't exist: {source}", file=sys.stderr)
        return 1

    # `{title}`: the CLI value, else the file stem, reduced to lowercase ASCII.
    title = normalize_title(args.title or source.stem)
    out_dir = _resolve_out_dir(args, title, source)
    out_dir.mkdir(parents=True, exist_ok=True)

    art = artifacts(title, out_dir=str(out_dir))
    dict_slug = art.dict_slug
    print(
        f"[config] dict={config.get_dict_choice()} ({dict_slug}), "
        f"llm={'on' if config.get_use_llm() else 'off'}"
    )
    print(f"[config] title={title}, out_dir={out_dir}")

    # ------------------------------------------------------------------
    # 1. Extraction
    # ------------------------------------------------------------------
    text = source.read_text(encoding="utf-8")
    entries = extract_bold_entries(text)
    write_index(entries, art, source.name)
    print(f"[extract] {len(entries)} bolded records from {source}")

    # ------------------------------------------------------------------
    # 2. Dictionary lookup
    # ------------------------------------------------------------------
    cache = None
    summary: dict = {
        "words": 0,
        "total": 0,
        "retriable": 0,
        "dict_api_key_problems": 0,
    }

    if args.no_lookup:
        from lookup import write_json

        for entry in entries:
            entry.dict_slug = dict_slug
        summary = write_json.write_documents(
            entries,
            words_path=out_dir / art.words_json,
            errors_path=out_dir / art.errors_json,
            dict_slug=dict_slug,
        )
        print("[lookup] skipped (--no-lookup)")
    else:
        from lookup import run as lookup_run

        cache, entries, summary = lookup_run(
            entries,
            art,
            timeout=args.timeout,
            retry=args.retry,
            delay=args.delay,
            use_cache=not args.no_cache,
            cache_path=args.cache_path,
        )
        print(
            f"[lookup] {summary['words']} entries written; "
            f"{summary['total']} errors ({summary['retriable']} retriable)"
        )

        # Base-form restoration runs inside the lookup stage, as required.
        if not args.no_lemma:
            from lookup import lemmatizer, write_json

            before = len(entries)
            entries = lemmatizer.attach_lemma_entries(
                entries,
                session=None,
                cache=cache,
                timeout=args.timeout,
                retry=args.retry,
                delay=args.delay,
            )
            if len(entries) != before:
                summary = write_json.write_documents(
                    entries,
                    words_path=out_dir / art.words_json,
                    errors_path=out_dir / art.errors_json,
                    dict_slug=dict_slug,
                )
                print(f"[lemma] {len(entries) - before} base-form entries appended")

    # ------------------------------------------------------------------
    # 3. LLM completion (opt-in)
    # ------------------------------------------------------------------
    if config.get_use_llm():
        entries = llm.run(entries, art, dict_slug=dict_slug)
        print("[llm] completion stage finished")
    else:
        print("[llm] skipped (USE_LLM is off)")

    if cache is not None:
        cache.save()

    # ------------------------------------------------------------------
    # 4. Markdown
    # ------------------------------------------------------------------
    paths = write_md.write_md(entries, art, source_name=source.name)
    print(f"[write] {paths['note_md']}")
    print(f"[write] {paths['index_md']}")

    # ------------------------------------------------------------------
    # 5. LaTeX
    # ------------------------------------------------------------------
    if not args.no_latex:
        from . import latex

        result = latex.build(
            [entry.to_dict() for entry in entries],
            title=title,
            tex_dir=out_dir,
            dict_slug=dict_slug,
        )
        print(f"[write] {result['main']}")
        print(f"[write] {result['preamble']}")
        print(f"[write] {result['unit']}")
        print(
            f"[tex] {result['entries']} entries; compile with "
            f'cd "{out_dir}" && xelatex main.tex (twice)'
        )
    else:
        print("[tex] skipped (--no-latex)")

    if summary.get("dict_api_key_problems"):
        print(
            f"[tip] {summary['dict_api_key_problems']} lookups failed on API key "
            "problems; check DICT_API_KEY."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
