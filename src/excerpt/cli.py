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
from .llm_check import check_llm_config


def _resolve_out_dir(args, title: str, source: Path) -> Path:
    """Pick the output directory: CLI > ``OUTPUT_DIR`` > ``.{title}_out``."""
    if args.output_dir is not None:
        return args.output_dir

    configured = str(config.get_session_settings().get("env_out_dir") or "").strip()
    if configured:
        return Path(configured)

    return source.parent / default_out_dir_name(title)


def _preflight_llm(args) -> int:
    """Verify the LLM configuration before the pipeline spends any work.

    Returns ``0`` when it is safe to continue, otherwise a non-zero exit code
    after printing a single ``[error]`` line, exactly as the CLI contract
    requires.  Skipped entirely when the LLM stage is off or disabled.

    A *transient* failure (the endpoint answered 429/5xx) means the
    configuration itself is fine but the provider cannot serve it right now.
    That must not abort the run: a busy free tier would otherwise block every
    invocation.  It is reported as a warning and the pipeline continues.
    """
    if not config.get_use_llm() or args.no_llm_check:
        return 0

    result = check_llm_config(use_cache=not args.no_cache)
    if result.ok:
        origin = "cached" if result.cached else "verified"
        print(f"[llm] configuration {origin}: ok")
        if result.transient:
            print(
                f"[warn] LLM endpoint is temporarily unavailable "
                f"({result.code}): {result.reason}"
            )
            print(
                "[warn] continuing anyway; the LLM stage may fall back to "
                "errors for uncompleted entries."
            )
        return 0

    print(f"[error] LLM configuration is not usable: {result.reason}", file=sys.stderr)
    print(
        "[error] check OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL, "
        "or run with --no-llm to skip the LLM stage.",
        file=sys.stderr,
    )
    return 3


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
            compile_latex=args.compile_latex,
            xelatex=args.xelatex,
            llm_timeout=args.llm_timeout,
            llm_retries=args.llm_retries,
            llm_workers=args.llm_workers,
            llm_backoff=args.llm_backoff,
        )
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    source: Path = args.source
    if not source.is_file():
        print(f"[error] Source file doesn't exist: {source}", file=sys.stderr)
        return 1

    # Validate the LLM configuration up front: failing here costs one probe
    # request, whereas failing mid-pipeline would waste the whole run.
    exit_code = _preflight_llm(args)
    if exit_code:
        return exit_code

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
        from common.config import get_openai_settings

        from . import llm_cache

        cache_path = (
            str(args.llm_cache_path) if args.llm_cache_path else None
        )
        llm_cache_store = llm_cache.LlmCache(
            get_openai_settings()[2],
            path=cache_path,
            enabled=not args.no_llm_cache,
        )
        entries = llm.run(
            entries, art, dict_slug=dict_slug, cache=llm_cache_store
        )
        if llm_cache_store.enabled:
            print(
                f"[llm] cache: {llm_cache_store.hits} hit(s), "
                f"{llm_cache_store.misses} miss(es)"
            )
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
            compile_pdf=config.get_compile_latex(),
            xelatex=args.xelatex,
        )
        print(f"[write] {result['main']}")
        print(f"[write] {result['preamble']}")
        print(f"[write] {result['unit']}")

        info = result.get("compile")
        if info and info.get("skipped"):
            print(f"[tex] .tex written; compilation skipped: {info['reason']}")
        elif info and not info.get("ok"):
            print(f"[tex] compilation failed: {info['reason']}")
        elif info:
            print(f"[pdf] {result['pdf']} ({info['passes']} xelatex passes)")
        else:
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
