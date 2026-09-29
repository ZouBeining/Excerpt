"""Tests for the LLM preflight check.

Everything here is offline: the probe is exercised through a stub session, so
no request ever leaves the machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from common import config
from excerpt import llm_check
from excerpt.llm_check import (
    CHECK_VERSION,
    CheckResult,
    check_llm_config,
    config_fingerprint,
    default_check_path,
    read_check_cache,
    write_check_cache,
)


def configure_llm(*, key="sk-test", url="https://api.example.com/v1", model="gpt-x"):
    config.configure(
        dict_choice="MW",
        use_llm=True,
        env_path="",
    )
    import os

    os.environ["OPENAI_API_KEY"] = key
    os.environ["OPENAI_BASE_URL"] = url
    os.environ["OPENAI_MODEL"] = model
    config.configure(dict_choice="MW", use_llm=True, env_path="")


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_is_stable_and_key_sensitive():
    a = config_fingerprint(api_key="k1", base_url="https://x/v1", model="m")
    b = config_fingerprint(api_key="k1", base_url="https://x/v1", model="m")
    c = config_fingerprint(api_key="k2", base_url="https://x/v1", model="m")
    assert a == b
    assert a != c


def test_fingerprint_never_contains_the_plaintext_key():
    fp = config_fingerprint(api_key="super-secret", base_url="https://x/v1", model="m")
    assert "super-secret" not in fp
    assert len(fp) == 32


def test_fingerprint_tolerates_trailing_slash():
    a = config_fingerprint(api_key="k", base_url="https://x/v1", model="m")
    b = config_fingerprint(api_key="k", base_url="https://x/v1/", model="m")
    assert a == b


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_cache_roundtrip(tmp_path):
    path = tmp_path / "llm.check.json"
    fp = "abc123"
    assert read_check_cache(fp, path=path) is None

    write_check_cache(fp, CheckResult(ok=True, code="OK", status=200), path=path)
    hit = read_check_cache(fp, path=path)
    assert hit is not None
    assert hit.ok is True
    assert hit.cached is True

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["version"] == CHECK_VERSION


def test_probe_payload_suppresses_reasoning():
    """The probe must carry the same reasoning hint as a real completion.

    A reasoning model spends a chain of thought even on a one-token probe,
    which is slow and — on a free tier — much more likely to be throttled.
    """
    assert llm_check._PROBE_PAYLOAD["reasoning"] == {"enabled": False}


def test_probe_sends_the_reasoning_field():
    from conftest import StubResponse, StubSession

    configure_llm(key="k", url="https://x/v1", model="m")
    session = StubSession([StubResponse({"ok": True}, status_code=200)])

    check_llm_config(use_cache=False, session=session)

    sent = session.calls[0]["json"]
    assert sent["reasoning"] == {"enabled": False}
    assert sent["model"] == "m"


def test_cache_ignores_failures(tmp_path):
    path = tmp_path / "llm.check.json"
    write_check_cache("fp", CheckResult(ok=False, code="NOPE"), path=path)
    assert not path.exists()


def test_cache_ignores_transient_verdicts(tmp_path):
    path = tmp_path / "llm.check.json"
    write_check_cache(
        "fp", CheckResult(ok=True, code="RATE_LIMITED", transient=True), path=path
    )
    assert not path.exists(), "a throttled probe must not be cached as success"


def test_cache_survives_corruption(tmp_path):
    path = tmp_path / "llm.check.json"
    path.write_text("{not json", encoding="utf-8")
    assert read_check_cache("fp", path=path) is None


def test_cache_version_mismatch_is_a_miss(tmp_path):
    path = tmp_path / "llm.check.json"
    path.write_text(
        json.dumps({"version": 999, "checks": {"fp": {"ok": True}}}), encoding="utf-8"
    )
    assert read_check_cache("fp", path=path) is None


# ---------------------------------------------------------------------------
# Missing configuration
# ---------------------------------------------------------------------------

def test_missing_key_reports_before_any_request():
    result = check_llm_config(
        api_key="", base_url="https://x/v1", model="m", use_cache=False
    )
    assert result.ok is False
    assert result.code == "MISSING_API_KEY"


def test_missing_base_url_reports():
    result = check_llm_config(api_key="k", base_url="", model="m", use_cache=False)
    assert result.code == "MISSING_BASE_URL"


def test_missing_model_reports():
    result = check_llm_config(api_key="k", base_url="https://x/v1", model="", use_cache=False)
    assert result.code == "MISSING_MODEL"


# ---------------------------------------------------------------------------
# HTTP classification
# ---------------------------------------------------------------------------

def _session(status, payload):
    from conftest import StubResponse, StubSession

    return StubSession([StubResponse(payload, status_code=status)])


def test_success_is_ok_and_cached(tmp_path, monkeypatch):
    path = tmp_path / "llm.check.json"
    session = _session(200, {"choices": [{"message": {"content": "ok"}}]})
    result = check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=True, cache_path=path, session=session,
    )
    assert result.ok is True
    assert result.transient is False
    assert path.is_file()
    assert session.calls[0]["url"] == "https://x/v1/chat/completions"


def test_401_is_invalid_key():
    session = _session(401, {"error": {"message": "bad key"}})
    result = check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=False, session=session,
    )
    assert result.ok is False
    assert result.code == "INVALID_API_KEY"


def test_404_is_bad_endpoint_or_model():
    session = _session(404, {})
    result = check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=False, session=session,
    )
    assert result.ok is False
    assert result.code == "BAD_ENDPOINT_OR_MODEL"


def test_429_is_valid_but_transient():
    session = _session(429, {"error": {"message": "slow down"}})
    result = check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=False, session=session,
    )
    assert result.ok is True, "a rate limit means the config works"
    assert result.transient is True
    assert result.code == "RATE_LIMITED"


def test_5xx_is_valid_but_transient():
    session = _session(503, {})
    result = check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=False, session=session,
    )
    assert result.ok is True
    assert result.transient is True
    assert result.code == "PROVIDER_ERROR"


def test_400_with_provider_error_is_transient():
    """OpenRouter wraps upstream faults as ``"Provider returned error"``."""
    body = {
        "error": {
            "message": "Provider returned error",
            "code": 400,
            "metadata": {
                "raw": json.dumps(
                    {
                        "error": {
                            "code": 400,
                            "message": "User location is not supported for the API use.",
                            "status": "FAILED_PRECONDITION",
                        }
                    }
                ),
                "provider_name": "Google AI Studio",
            },
        }
    }
    session = _session(400, body)
    result = check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=False, session=session,
    )
    assert result.ok is True, "a region lock is not a bad key"
    assert result.transient is True
    assert result.code == "PROVIDER_REJECTED"
    assert "location is not supported" in result.reason


def test_400_without_provider_context_is_fatal():
    session = _session(400, {"error": {"message": "missing field: messages"}})
    result = check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=False, session=session,
    )
    assert result.ok is False
    assert result.code == "BAD_REQUEST"


def test_error_detail_unwraps_nested_raw():
    body = {
        "error": {
            "message": "Provider returned error",
            "metadata": {"raw": json.dumps({"error": {"message": "inner truth"}})},
        }
    }
    assert llm_check._error_detail(body) == "inner truth"


def test_error_detail_falls_back_to_top_level():
    assert llm_check._error_detail({"error": {"message": "plain"}}) == "plain"
    assert llm_check._error_detail("oops") == "oops"


def test_network_error_is_not_ok():
    from conftest import StubSession

    session = StubSession(error=requests.exceptions.ConnectionError("boom"))
    result = check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=False, session=session,
    )
    assert result.ok is False
    assert result.code == "NETWORK_ERROR"


def test_timeout_is_not_ok():
    from conftest import StubSession

    session = StubSession(error=requests.exceptions.Timeout("slow"))
    result = check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=False, session=session,
    )
    assert result.ok is False
    assert result.code == "TIMEOUT"


# ---------------------------------------------------------------------------
# Endpoint construction
# ---------------------------------------------------------------------------

def test_endpoint_is_not_double_appended():
    assert (
        llm_check._completions_endpoint("https://x/v1/chat/completions")
        == "https://x/v1/chat/completions"
    )
    assert (
        llm_check._completions_endpoint("https://x/v1")
        == "https://x/v1/chat/completions"
    )
    assert (
        llm_check._completions_endpoint("https://x/v1/")
        == "https://x/v1/chat/completions"
    )


def test_cache_hit_skips_the_request(tmp_path):
    path = tmp_path / "llm.check.json"
    first = _session(200, {"choices": []})
    check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=True, cache_path=path, session=first,
    )
    assert len(first.calls) == 1

    second = _session(500, {})  # would fail, but should never be called
    reused = check_llm_config(
        api_key="k", base_url="https://x/v1", model="m",
        use_cache=True, cache_path=path, session=second,
    )
    assert reused.ok is True
    assert reused.cached is True
    assert second.calls == [], "a cached verdict must not spend a request"


def test_default_check_path_uses_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("EXCERPT_CACHE_DIR", str(tmp_path))
    assert default_check_path() == tmp_path / "llm.check.json"
