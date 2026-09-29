"""Shared pytest fixtures.

No test in this suite is allowed to touch the network: dictionary clients and
the LLM are always exercised through mocks or stub sessions.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# ``src`` layout: make the packages importable without installing.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from common import config  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Give every test a clean configuration and a private cache directory."""
    for name in (
        "DICT_CHOICE",
        "DICT_API_KEY",
        "USE_LLM",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "OUTPUT_DIR",
        "EXCERPT_CACHE_PATH",
        "TIMEOUT",
        "RETRY",
        "DELAY",
        "PROXY",
        "LLM_TIMEOUT",
        "LLM_RETRIES",
        "LLM_WORKERS",
        "LLM_BACKOFF",
        "LLM_REASONING",
    ):
        monkeypatch.delenv(name, raising=False)

    # ``configure`` loads a .env file; keep the developer's real one out by
    # passing an explicit empty path, which means "no .env at all".
    monkeypatch.setenv("EXCERPT_CACHE_PATH", str(tmp_path / "cache.json"))
    config.configure(dict_choice="MW", use_llm=False, env_path="")
    # The reasoning- and dialect-probe verdicts are module-level caches, so
    # they must not leak from one test into the next.
    for module_name in ("llm_reasoning", "llm_format"):
        try:
            module = importlib.import_module(f"excerpt.{module_name}")
        except ImportError:  # pragma: no cover - module added in a later stage
            continue
        module.reset_probe_cache()
    yield tmp_path


@pytest.fixture
def test_markdown() -> str:
    """The project's own sample text."""
    return (PROJECT_ROOT / "tests" / "test.md").read_text(encoding="utf-8")


class StubResponse:
    """A minimal stand-in for ``requests.Response``."""

    def __init__(self, payload, status_code: int = 200, json_ok: bool = True):
        self._payload = payload
        self.status_code = status_code
        self._json_ok = json_ok

    def json(self):
        if not self._json_ok:
            raise ValueError("not json")
        return self._payload

    @property
    def text(self) -> str:
        return self._payload if isinstance(self._payload, str) else ""


class StubSession:
    """A ``requests.Session`` double that records calls and replays payloads."""

    def __init__(self, responses=None, *, error: Exception | None = None):
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[dict] = []
        self.closed = False

    def get(self, url, params=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "params": params, "timeout": timeout})
        if self.error is not None:
            raise self.error
        if self.responses:
            return self.responses.pop(0)
        return StubResponse([])

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if self.error is not None:
            raise self.error
        if self.responses:
            return self.responses.pop(0)
        return StubResponse({"ok": True})

    def close(self):
        self.closed = True

    def headers(self):  # pragma: no cover - only used if headers are assigned
        return {}


@pytest.fixture
def stub_session():
    return StubSession


@pytest.fixture
def stub_response():
    return StubResponse
