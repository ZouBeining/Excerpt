"""Command-line interface definition for ``excerpt``.

Only argument *parsing* lives here.  Values are turned into runtime
configuration by :func:`common.config.configure`, which implements the
documented priority: CLI > ``.env`` > ``common/config.py`` defaults.

The valid dictionary choices are never hard-coded here — they come from
``common.config``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from common.config import default_cache_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="excerpt",
        description=(
            "Extract bold words/phrases from a Markdown file, look them up in "
            "a dictionary or use a LLM to generate some explanations, "
            "and finally output a LaTeX document."
        ),
    )
    parser.add_argument(
        "source",
        type=Path,
        help="The Markdown file ready to be proceeded",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: .<title>_out, next to the source)",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Title shared by every output file (default: the source file stem)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Specify a .env file path",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Delay between each two requests (default = 0.2 s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Time out of a single request (default = 20.0 s)",
    )
    parser.add_argument(
        "--retry",
        type=int,
        default=None,
        help="If failed, the number of retries (default = 3).",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="Specify a proxy (default = No Proxy)",
    )
    parser.add_argument(
        "--no-lookup",
        action="store_true",
        help="Extract the bold records only, don't look them up",
    )
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help=f"Local cache path (default: {default_cache_path()})",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Don't use local cache (default: use local cache)",
    )
    parser.add_argument(
        "--no-latex",
        action="store_true",
        help="Turn off LaTeX output (default: on)",
    )
    compile_group = parser.add_mutually_exclusive_group()
    compile_group.add_argument(
        "--compile",
        dest="compile_latex",
        action="store_true",
        default=None,
        help="Compile the generated .tex to PDF with xelatex (default: off)",
    )
    compile_group.add_argument(
        "--no-compile",
        dest="compile_latex",
        action="store_false",
        default=None,
        help="Never compile the LaTeX output (overrides COMPILE_LATEX)",
    )
    parser.add_argument(
        "--xelatex",
        type=str,
        default=None,
        help="Path to the xelatex executable (default: found on PATH)",
    )
    parser.add_argument(
        "--no-lemma",
        action="store_true",
        help="Turn off LemmInflect base-form lookup (default: on)",
    )
    parser.add_argument(
        "--dict-choice",
        type=str,
        default=None,
        help=(
            "Dictionary to use; overrides DICT_CHOICE from the environment. "
            "Valid values are defined in common/config.py."
        ),
    )
    llm_group = parser.add_mutually_exclusive_group()
    llm_group.add_argument(
        "--use-llm",
        dest="use_llm",
        action="store_true",
        default=None,
        help="Complete non-word entries with a LLM",
    )
    llm_group.add_argument(
        "--no-llm",
        dest="use_llm",
        action="store_false",
        default=None,
        help="Never call the LLM (overrides USE_LLM from the environment)",
    )
    parser.add_argument(
        "--no-llm-check",
        action="store_true",
        help=(
            "Skip the preflight validation of the LLM configuration "
            "(default: validate before the pipeline runs)"
        ),
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=None,
        help=(
            "Time out of a single LLM request (default = 60.0 s). "
            "Independent of --timeout, which only covers the dictionary."
        ),
    )
    parser.add_argument(
        "--llm-retries",
        type=int,
        default=None,
        help=(
            "How many times a failed LLM request is retried "
            "(default = 3). Independent of --retry."
        ),
    )
    parser.add_argument(
        "--llm-workers",
        type=int,
        default=None,
        help=(
            "Number of concurrent LLM requests (default = 1, i.e. strictly "
            "sequential, which is the historical behaviour). Raising it speeds "
            "up the completion stage but may hit rate limits on free tiers."
        ),
    )
    parser.add_argument(
        "--llm-backoff",
        type=float,
        default=None,
        help="Base backoff (seconds) between LLM retries (default = 0.5)",
    )
    return parser
