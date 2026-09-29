r"""Global configuration, constants, and shared data structures.

This module must not import any business modules.  It is safe to import from
both ``excerpt`` and ``lookup``.
"""

from __future__ import annotations

import os
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
    "is_dict_choice_valid",
    # standard entry schema
    "STANDARD_ENTRY_KEYS",
    "empty_entry",
    "normalize_entry",
    # shared data classes
    "BoldEntry",
    "ErrorRecord",
    # Markdown labels
    "MD_LABELS"
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
# Internal runtime configuration
# ---------------------------------------------------------------------------

_configured: bool = False

_dict_choice: str | None = None
_dict_config: dict[str, Any] | None = None
_dict_api_key: str = ""

_use_llm: bool = False
_openai_api_key: str = ""
_openai_base_url: str = ""
_openai_model: str = ""


def _parse_bool(value: str | None) -> bool:
    """Parse common truthy strings from environment variables."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configure(
    dict_choice: str | None = None,
    use_llm: bool | None = None,
    env_path: str | None = None,
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

    dotenv_path = env_path or find_dotenv()
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)
    else:
        load_dotenv(override=False)

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
        _use_llm = use_llm
    else:
        _use_llm = _parse_bool(os.getenv("USE_LLM"))

    if _use_llm:
        _openai_api_key = os.environ.get("OPENAI_API_KEY", "")
        _openai_base_url = os.environ.get("OPENAI_BASE_URL", "")
        _openai_model = os.environ.get("OPENAI_MODEL", "")
    else:
        _openai_api_key = ""
        _openai_base_url = ""
        _openai_model = ""

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


def is_dict_choice_valid(choice: str) -> bool:
    """Return whether *choice* is a known dictionary option."""
    return str(choice).strip().upper() in DICT_CHOICES


# ---------------------------------------------------------------------------
# Standard entry schema
# ---------------------------------------------------------------------------

def empty_entry() -> dict[str, Any]:
    """Return a standard entry dict with every field present but empty.

    Every dictionary data module and the LLM module must normalise its output
    through this schema before the data reaches ``write.py``.
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


# ---------------------------------------------------------------------------
# Shared data classes
# ---------------------------------------------------------------------------

@dataclass
class BoldEntry:
    """A bolded record extracted from the input Markdown."""

    word: str
    sentence: str = ""
    line: int = 0
    code: int = CODE_OK
    detail: str = ""
    source: str = SOURCE_PENDING
    dict_slug: str = ""
    type: str = TYPE_WORD
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
            "entry": self.entry,
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
            "word": self.word,
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "code": self.code,
            "reason": self.reason,
            "status": self.status,
            "time": self.time,
        }