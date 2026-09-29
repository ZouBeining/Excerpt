from __future__ import annotations

from pathlib import Path
import argparse

from .cache import default_cache_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="excerpt",
        description=
            """Extract bold words/phrases from a Markdown file, 
            look them up in a dictionary or use a LLM to generate some explanations, 
            and finally output a LaTeX document.""",
    )
    parser.add_argument(
        "source", type=Path, 
        help="The Markdown file ready to be proceeded"
    )
    parser.add_argument(
        "-o", "--output-dir", type=Path, default=None,
        help="Output directory (default: <source directory>/excerpt_out)",
    )
    parser.add_argument(
        "--title", type = str, default = None,
        help="Specify the title of the Markdown file and the section name of LaTeX file",
    )
    parser.add_argument(
        "--env-file", type=Path, default=None,
        help="Specify a .env file path",
    )
    parser.add_argument(
        "--delay", type=float, default=0.2,
        help="Delay between each two requests (default = 0.2 s)",
    )
    parser.add_argument(
        "--timeout", type=float, default=20.0, 
        help="Time out of a single request (defualt = 20.0 s)",
    )
    parser.add_argument(
        "--retry", type=int, default=3, 
        help="If failed, the number of retries (default = 3).",
    )    
    parser.add_argument(
        "--proxy", default=None,
        help="Specify a proxy (default = No Proxy)",
    )
    parser.add_argument(
        "--no-lookup", action="store_true", 
        help="Extract the bold records only, don't look them up",
    )
    parser.add_argument(
        "--cache-path", type=Path, default=None,
        help=f"Local cache path (default: {default_cache_path()}) "
    )
    parser.add_argument(
        "--no-cache", action="store_true", 
        help="Don't use local cache (default: use local cache)",
    )
    parser.add_argument(
        "--no-latex", action="store_true", 
        help="Turn off LaTeX output (default: on)",
    )
    parser.add_argument(
        "--no-lemma", action="store_true",
        help="Turn off LemmInflect base-form lookup (default: on)",
    )
    return parser