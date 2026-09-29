# from .env import find_env_file, load_api_key, mask
# from .fd_cli import main as main_fd
# from .mw_cli import main as main_mw

import json
import sys
from pathlib import Path

from . import lemmatizer

from .arg import build_parser
from .config import *
from .extractor import extract_bold_entries
from .lookup import main as lookup_main
from .mw_note import render, render_index, title_from_source
from .mw_errors import write_errors
from .mw_tex_cli import DEFAULT_NOTE


def main(argv: list[str] | None = None) -> int:
    # Get command-line arguments and parameters
    args = build_parser().parse_args(argv)

    config.configure(dict_choice=args.dict_choice, use_llm=args.use_llm)

    # Check if source file exists
    source: Path = args.source
    if not source.is_file():
        print(f"[error] Source file doesn't exist: {source}", file=sys.stderr)
        return 1

    # Specify and prepare output directory
    out_dir = args.output_dir or source.parent / "excerpt_output"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Use extractor.py to extract the bolded records
    text: str = source.read_text(encoding="utf-8")
    entries: list[BoldEntry] = extract_bold_entries(text)
    print(f"[extract] {len(entries)} bolded records are extracted from {source}")

    # Lookup the words
    if not args.no_lookup:
        session, cache = lookup_main(args, entries)

        # Lemmatize
        if not args.no_lemma:
            entries = lemmatizer.attach_lemma_entries(
                entries,
                session=session,
                api_key=dict_api_key,
                cache=cache,
                timeout =args.timeout,
                delay=args.delay,
            )
            if cache is not None:
                cache.save()
            lemma_total = sum(1 for e in entries if lemmatizer.is_lemma_entry(e))
            print(f"[lemma] {lemma_total} entries are added.")

    records: list[dict] = [lemmatizer.boldentry_to_dict(e) for e in entries]

    # Write data into a bunch of documents
    source_title = title_from_source(source.name)

    # .words.json
    words_title = f"{args.title or source_title}.{dict_choice}.words.json"
    words_path = out_dir / words_title
    words_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[write] {words_path}")

    #.errors.json
    errors_title: str = f"{args.title or source_title}.{dict_choice}.errors.json"
    errors_path: Path = out_dir / errors_title
    summary = write_errors(words_path, errors_path)
    print(
        f"[write] {errors_path},"
        f"{summary['total']} errors in total: {summary['byType'] or 'None'}; "
        f"among which {summary['retriable']} needs re-run."
    )

    # .md
    md_title = f"{args.title or source_title}.md"
    note_path = out_dir / md_title
    note_path.write_text(
        render(entries, source_name=source.name, title=source_title),
        encoding="utf-8",
    )
    succeed_count = sum(1 for e in entries if e.code == CODE_OK)
    print(f"[write] {note_path} ({succeed_count} entries found in the dictionary)")

    # .index.md
    index_title: str = f"{args.title}.index.md" or f"{source_title}.index.md"
    index_path: Path = out_dir / index_title
    index_path.write_text(
        f"# {args.title or source_title} Vocabulary Index\n\n" + render_index(entries),
        encoding="utf-8",
    )
    print(f"[write] Vocabulary index written in {index_path}")


    if summary["keyProblems"]:
        print(
            f"[tip] {summary['keyProblems']} failed due to API problems."
            f"Please check {env_path}"
        )

    # Write LaTeX document
    if args.latex:
        from .mw_latex import convert as to_latex

        result = to_latex(
            note_path,
            tex_dir=out_dir,
            title=title_from_source(source.name),
            note=DEFAULT_NOTE,
        )
        print(f"[write] {result['main']}")
        print(f"[write] {result['unit']}")
        print(
            f"[tex] {result['entries']} entries in total；"
            f'Compile: cd "{out_dir}" && xelatex main.tex (twice).'
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
