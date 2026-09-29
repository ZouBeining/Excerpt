r"""Global configuration, constants, and shared data structures.

This module must not import any business modules.  It is safe to import from
both ``excerpt`` and ``lookup``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from dotenv import find_dotenv, load_dotenv

__all__ = [
    # status codes
    "CODE_OK",
    "CODE_NOT_A_SINGLE_WORD",
    "CODE_LOOKUP_FAILED",
    "CODE_WORD_NOT_FOUND",
    "CODE_BAD_RESPONSE",
    "CODE_RATE_LIMITED",
    "CODE_INVALID_QUERY",
    "CODE_MISSING_API_KEY",
    "CODE_INVALID_API_KEY",
    "RETRIABLE_CODES",
    "API_KEY_PROBLEM_CODES",
    # error reasons
    "REASON_NOT_A_SINGLE_WORD",
    "REASON_LOOKUP_FAILED",
    "REASON_WORD_NOT_FOUND",
    "REASON_BAD_RESPONSE",
    "REASON_RATE_LIMITED",
    "REASON_INVALID_QUERY",
    "REASON_MISSING_API_KEY",
    "REASON_INVALID_API_KEY",
    # status / source / type
    "STATUS_PENDING",
    "STATUS_FILLED",
    "SOURCE_PENDING",
    "SOURCE_CACHE",
    "SOURCE_API",
    "SOURCE_EXTRACTOR",
    "SOURCE_LOOKUP",
    "SOURCE_LLM",
    "TYPE_WORD",
    "TYPE_PHRASE",
    "TYPE_SENTENCE",
    # dict choice
    "DEFAULT_DICT_CHOICE",
    "DEFAULT_USE_LLM",
    "DICT_CHOICES",
    "LLM_DICTIONARY_NAME",
    "valid_dict_choice",
    "configure",
    "get_dict_choice",
    "get_dict_config",
    "get_dict_slug",
    "get_dict_api_key",
    "get_use_llm",
    "get_openai_settings",
    "get_session_settings",
    "is_dict_choice_valid",
    # standard entry schema
    "STANDARD_ENTRY_KEYS",
    "empty_entry",
    "normalize_entry",
    "empty_words_document",
    "empty_errors_document",
    # shared data classes
    "BoldEntry",
    "ErrorRecord",
    # naming helpers
    "TITLE_FALLBACK",
    "normalize_title",
    "default_out_dir_name",
    "default_cache_path",
    "artifacts",
    # Markdown labels
    "MD_LABELS",
]


# ---------------------------------------------------------------------------
# Status codes
# ---------------------------------------------------------------------------

CODE_OK = 0
CODE_NOT_A_SINGLE_WORD = 1001
CODE_LOOKUP_FAILED = 1002
CODE_WORD_NOT_FOUND = 1003
CODE_BAD_RESPONSE = 1004
CODE_RATE_LIMITED = 1005
CODE_INVALID_QUERY = 1006
CODE_MISSING_API_KEY = 1007
CODE_INVALID_API_KEY = 1008

#: Errors worth retrying on the next run (transient failures).
RETRIABLE_CODES: frozenset[int] = frozenset(
    {CODE_LOOKUP_FAILED, CODE_RATE_LIMITED, CODE_BAD_RESPONSE}
)

#: Errors caused by a missing/invalid dictionary API key.
API_KEY_PROBLEM_CODES: frozenset[int] = frozenset(
    {CODE_MISSING_API_KEY, CODE_INVALID_API_KEY}
)

_CODE_TO_REASON: dict[int, str] = {
    CODE_NOT_A_SINGLE_WORD: "NOT_A_SINGLE_WORD",
    CODE_LOOKUP_FAILED: "LOOKUP_FAILED",
    CODE_WORD_NOT_FOUND: "WORD_NOT_FOUND",
    CODE_BAD_RESPONSE: "BAD_RESPONSE",
    CODE_RATE_LIMITED: "RATE_LIMITED",
    CODE_INVALID_QUERY: "INVALID_QUERY",
    CODE_MISSING_API_KEY: "MISSING_API_KEY",
    CODE_INVALID_API_KEY: "INVALID_API_KEY",
}


# ---------------------------------------------------------------------------
# Error reasons
# ---------------------------------------------------------------------------

REASON_NOT_A_SINGLE_WORD = "NOT_A_SINGLE_WORD"
REASON_LOOKUP_FAILED = "LOOKUP_FAILED"
REASON_WORD_NOT_FOUND = "WORD_NOT_FOUND"
REASON_BAD_RESPONSE = "BAD_RESPONSE"
REASON_RATE_LIMITED = "RATE_LIMITED"
REASON_INVALID_QUERY = "INVALID_QUERY"
REASON_MISSING_API_KEY = "MISSING_API_KEY"
REASON_INVALID_API_KEY = "INVALID_API_KEY"


def reason_for(code: int) -> str:
    """Return the canonical ``reason`` string for a status *code*."""
    return _CODE_TO_REASON.get(int(code), REASON_LOOKUP_FAILED)


# ---------------------------------------------------------------------------
# Status / source / type
# ---------------------------------------------------------------------------

STATUS_PENDING = "pending"
STATUS_FILLED = "filled"

SOURCE_PENDING = "pending"
SOURCE_CACHE = "cache"
SOURCE_API = "api"
SOURCE_EXTRACTOR = "extractor"
SOURCE_LOOKUP = "lookup"
SOURCE_LLM = "llm"

TYPE_WORD = "word"
TYPE_PHRASE = "phrase"
TYPE_SENTENCE = "sentence"


# ---------------------------------------------------------------------------
# Dictionary configuration
# ---------------------------------------------------------------------------

MD_LABELS: dict[str, str] = {
    "definition": "Definition",
    "etymology": "Etymology",
    "first_use": "First Use",
    "synonyms": "Syn.",
    "antonyms": "Anton.",
    "examples": "E.g.",
}

DEFAULT_DICT_CHOICE = "MW"

#: LLM completion is opt-in; ``USE_LLM`` in ``.env`` or ``--use-llm`` turns it on.
DEFAULT_USE_LLM = False

DICT_CHOICES: dict[str, dict[str, Any]] = {
    "MW": {
        "slug": "mw",
        "api_module": "mw_api",
        "data_module": "mw_data",
        "display_name": "Merriam-Webster",
        "requires_api_key": True,
        "api_key_env": "DICT_API_KEY",
    },
    "FD": {
        "slug": "fd",
        "api_module": "fd_api",
        "data_module": "fd_data",
        "display_name": "Free Dictionary",
        "requires_api_key": False,
        "api_key_env": "",
    },
}

LLM_DICTIONARY_NAME = "ai"

valid_dict_choice: list[str] = list(DICT_CHOICES.keys())


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------

#: Used when the input Markdown file name cannot be reduced to ASCII letters.
TITLE_FALLBACK = "excerpt"

_TITLE_ALLOWED_RE = re.compile(r"[^a-z0-9]+")


def normalize_title(raw: str | None) -> str:
    """Reduce *raw* to ``[a-z0-9]`` only, as required for every output name.

    Non-ASCII input (e.g. a Chinese file name) has nothing left after the
    reduction, so it falls back to :data:`TITLE_FALLBACK`.
    """
    if not raw:
        return TITLE_FALLBACK
    reduced = _TITLE_ALLOWED_RE.sub("", str(raw).lower())
    return reduced or TITLE_FALLBACK


def default_out_dir_name(title: str) -> str:
    """Return the default output directory name for *title*."""
    return f".{title}_out"


def default_cache_path(dict_choice: str | None = None) -> str:
    """Return ``~/.cache/excerpt/{dict_slug}.cache.json``."""
    slug = get_dict_slug(dict_choice)
    return str(os.path.join("~", ".cache", "excerpt", f"{slug}.cache.json"))


@dataclass(frozen=True)
class Artifacts:
    """Canonical file names for one run.

    Centralising them keeps every module (``extractor``, ``lookup``, ``llm``,
    ``write_md``, ``latex``) writing to exactly the same paths.
    """

    title: str
    dict_slug: str
    out_dir: str

    @property
    def index_json(self) -> str:
        return f"{self.title}.index.json"

    @property
    def index_md(self) -> str:
        return f"{self.title}.{self.dict_slug}.index.md"

    @property
    def words_json(self) -> str:
        return f"{self.title}.{self.dict_slug}.words.json"

    @property
    def errors_json(self) -> str:
        return f"{self.title}.{self.dict_slug}.errors.json"

    @property
    def note_md(self) -> str:
        return f"{self.title}.{self.dict_slug}.md"

    @property
    def entries_dir(self) -> str:
        return "entries"

    @property
    def entries_tex(self) -> str:
        return f"{self.title}.tex"

    @property
    def main_tex(self) -> str:
        return "main.tex"

    @property
    def preamble_tex(self) -> str:
        return "preamble.excerpt.tex"


def artifacts(
    title: str,
    dict_slug: str | None = None,
    out_dir: str = ".",
) -> Artifacts:
    """Build the canonical :class:`Artifacts` for a run."""
    return Artifacts(
        title=normalize_title(title),
        dict_slug=get_dict_slug(dict_slug),
        out_dir=str(out_dir),
    )


# ---------------------------------------------------------------------------
# Internal runtime configuration
# ---------------------------------------------------------------------------

_configured: bool = False

_dict_choice: str | None = None
_dict_config: dict[str, Any] | None = None
_dict_api_key: str = ""

_use_llm: bool = DEFAULT_USE_LLM
_openai_api_key: str = ""
_openai_base_url: str = ""
_openai_model: str = ""

# Session tuning, seeded from the CLI (priority: CLI > .env > default).
_session_settings: dict[str, Any] = {
    "timeout": 20.0,
    "retry": 3,
    "delay": 0.2,
    "proxy": "",
    "cache_path": "",
    "use_cache": True,
}


def _parse_bool(value: str | None) -> bool:
    """Parse common truthy strings from environment variables."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configure(
    dict_choice: str | None = None,
    use_llm: bool | None = None,
    env_path: str | None = None,
    *,
    timeout: float | None = None,
    retry: int | None = None,
    delay: float | None = None,
    proxy: str | None = None,
    cache_path: str | None = None,
    use_cache: bool | None = None,
) -> None:
    """Load environment variables and apply runtime configuration.

    Priority:
        command line argument > .env > config.py default

    This function is intentionally idempotent.  Calling it again re-applies the
    configuration, which is useful in tests and when CLI options override .env.
    """
    global _configured
    global _dict_choice, _dict_config, _dict_api_key
    global _use_llm, _openai_api_key, _openai_base_url, _openai_model

    # ``override=True`` so that ``.env`` beats a pre-existing shell variable:
    # the documented priority is CLI > .env > config.py.
    #
    # ``find_dotenv()`` is only consulted when no path was supplied; an empty
    # string means "there is no .env", which also lets tests disable loading.
    if env_path is not None:
        dotenv_path = env_path
    else:
        dotenv_path = find_dotenv()

    if dotenv_path:
        load_dotenv(dotenv_path, override=True)

    raw_choice = (
        dict_choice
        if dict_choice is not None
        else os.getenv("DICT_CHOICE", DEFAULT_DICT_CHOICE)
    )
    raw_choice = str(raw_choice).strip().upper()

    if raw_choice not in DICT_CHOICES:
        valid = ", ".join(valid_dict_choice)
        raise ValueError(
            f"Invalid DICT_CHOICE: {raw_choice!r}. Valid values: {valid}."
        )

    _dict_choice = raw_choice
    _dict_config = DICT_CHOICES[raw_choice]

    api_key_env = str(_dict_config.get("api_key_env") or "")
    if _dict_config.get("requires_api_key") and api_key_env:
        _dict_api_key = os.environ.get(api_key_env, "")
    else:
        _dict_api_key = ""

    if use_llm is not None:
        _use_llm = bool(use_llm)
    else:
        env_use_llm = os.getenv("USE_LLM")
        _use_llm = (
            _parse_bool(env_use_llm)
            if env_use_llm not in (None, "")
            else DEFAULT_USE_LLM
        )

    if _use_llm:
        _openai_api_key = os.environ.get("OPENAI_API_KEY", "")
        _openai_base_url = os.environ.get("OPENAI_BASE_URL", "")
        _openai_model = os.environ.get("OPENAI_MODEL", "")
    else:
        _openai_api_key = ""
        _openai_base_url = ""
        _openai_model = ""

    # Session tuning: CLI value when given, otherwise .env, otherwise default.
    env_timeout = os.getenv("TIMEOUT")
    env_retry = os.getenv("RETRY")
    env_delay = os.getenv("DELAY")
    env_proxy = os.getenv("PROXY")
    env_cache_path = os.getenv("EXCERPT_CACHE_PATH")
    env_out_dir = os.getenv("OUTPUT_DIR")

    _session_settings["timeout"] = float(
        timeout
        if timeout is not None
        else (env_timeout if env_timeout else 20.0)
    )
    _session_settings["retry"] = int(
        retry if retry is not None else (env_retry if env_retry else 3)
    )
    _session_settings["delay"] = float(
        delay if delay is not None else (env_delay if env_delay else 0.2)
    )
    _session_settings["proxy"] = str(
        proxy if proxy is not None else (env_proxy or "")
    ).strip()
    _session_settings["cache_path"] = str(
        cache_path if cache_path is not None else (env_cache_path or "")
    ).strip()
    _session_settings["use_cache"] = (
        bool(use_cache) if use_cache is not None else True
    )
    _session_settings["env_out_dir"] = str(env_out_dir or "").strip()

    _configured = True


def _ensure_configured() -> None:
    if not _configured:
        configure()


def get_dict_choice() -> str:
    """Return the active dictionary choice, e.g. ``"MW"``."""
    _ensure_configured()
    assert _dict_choice is not None
    return _dict_choice


def get_dict_config(dict_choice: str | None = None) -> dict[str, Any]:
    """Return the configuration dict for *dict_choice*.

    If *dict_choice* is omitted, the active dictionary is used.
    """
    if dict_choice is None:
        _ensure_configured()
        assert _dict_config is not None
        return _dict_config

    choice = str(dict_choice).strip().upper()
    try:
        return DICT_CHOICES[choice]
    except KeyError as exc:
        valid = ", ".join(valid_dict_choice)
        raise KeyError(
            f"Invalid dict choice: {dict_choice!r}. Valid values: {valid}."
        ) from exc


def get_dict_slug(dict_choice: str | None = None) -> str:
    """Return the lowercase slug used in filenames and directories."""
    return str(get_dict_config(dict_choice)["slug"])


def get_dict_api_key() -> str:
    """Return the active dictionary API key, or ``""`` if not required/missing."""
    _ensure_configured()
    return _dict_api_key


def get_use_llm() -> bool:
    """Return whether LLM completion is enabled."""
    _ensure_configured()
    return _use_llm


def get_openai_settings() -> tuple[str, str, str]:
    """Return ``(api_key, base_url, model)`` for the LLM provider."""
    _ensure_configured()
    return _openai_api_key, _openai_base_url, _openai_model


def get_session_settings() -> dict[str, Any]:
    """Return HTTP/session tuning: timeout, retry, delay, proxy, cache path."""
    _ensure_configured()
    return dict(_session_settings)


def is_dict_choice_valid(choice: str) -> bool:
    """Return whether *choice* is a known dictionary option."""
    return str(choice).strip().upper() in DICT_CHOICES


# ---------------------------------------------------------------------------
# Standard entry schema
# ---------------------------------------------------------------------------

def empty_entry() -> dict[str, Any]:
    """Return a standard entry dict with every field present but empty.

    Every dictionary data module and the LLM module must normalise its output
    through this schema before the data reaches ``lookup.write_json``.
    """
    return {
        "word": "",
        "language": "en",
        "dictionary": "",
        "homograph": 0,
        "section": "alpha",
        "offensive": False,
        "pos": "",
        "labels": [],
        "subjectLabels": [],
        "pronunciations": [],
        "senses": [],
        "shortDefs": [],
        "inflections": [],
        "synonyms": [],
        "antonyms": [],
        "etymology": "",
        "firstUse": "",
        "stems": [],
        "homographs": 0,
        "relatedHeadwords": [],
    }


STANDARD_ENTRY_KEYS: list[str] = list(empty_entry().keys())


def normalize_entry(entry: dict[str, Any] | None) -> dict[str, Any]:
    """Return *entry* restricted to the standard schema.

    Unknown keys are dropped.  Missing keys are filled with empty values.
    """
    base = empty_entry()
    if not entry:
        return base

    for key in base:
        if key in entry:
            base[key] = entry[key]
    return base


def empty_words_document() -> list[dict[str, Any]]:
    """Return the top-level value of a ``.words.json`` file (a list)."""
    return []


def empty_errors_document() -> dict[str, Any]:
    """Return the skeleton of an ``.errors.json`` file."""
    return {
        "total": 0,
        "retriable": 0,
        "dict_api_key_problems": 0,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Shared data classes
# ---------------------------------------------------------------------------

@dataclass
class BoldEntry:
    """A bolded record extracted from the input Markdown.

    ``to_dict`` emits exactly the key order of ``docs/template.words.json``.
    """

    word: str
    type: str = TYPE_WORD
    sentence: str = ""
    line: int = 0
    code: int = CODE_OK
    detail: str = ""
    source: str = SOURCE_PENDING
    dict_slug: str = ""
    entry: dict[str, Any] = field(default_factory=empty_entry)
    is_lemma: bool = False
    lemma_from: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "type": self.type,
            "sentence": self.sentence,
            "line": self.line,
            "code": self.code,
            "detail": self.detail,
            "source": self.source,
            "dict": self.dict_slug,
            "entry": normalize_entry(self.entry),
            "is_lemma": self.is_lemma,
            "lemma_from": self.lemma_from,
        }


@dataclass
class ErrorRecord:
    """A record in ``{title}.{dict_slug}.errors.json``."""

    word: str
    id: str
    type: str
    source: str
    code: int
    reason: str
    status: str = STATUS_PENDING
    time: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "word": self.word,
            "type": self.type,
            "source": self.source,
            "code": self.code,
            "reason": self.reason,
            "status": self.status,
            "time": self.time,
        }
