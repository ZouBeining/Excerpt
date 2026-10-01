"""Tests for the structured-output dialect walker.

Offline by construction: the module is pure data plus a small, locked state
machine, and every candidate is inspected as a plain dict.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from llm import format as llm_format


@pytest.fixture(autouse=True)
def clean_probe_cache():
    llm_format.reset_probe_cache()
    yield
    llm_format.reset_probe_cache()


def test_candidates_are_ordered_from_strictest_to_none():
    assert llm_format.SCHEMA_CANDIDATES[0]["json_schema"]["strict"] is True
    assert llm_format.SCHEMA_CANDIDATES[-1] is None


def test_candidate_for_injects_the_schema():
    schema = {"type": "object", "required": ["definition"]}
    payload = llm_format.candidate_for(0, schema)
    assert payload["json_schema"]["schema"] == schema


def test_candidate_for_does_not_mutate_the_template():
    """The template must survive two calls with different schemas."""
    first = {"a": 1}
    second = {"b": 2}
    llm_format.candidate_for(0, first)
    llm_format.candidate_for(0, second)

    template_schema = llm_format.SCHEMA_CANDIDATES[0]["json_schema"]["schema"]
    assert template_schema == "__SCHEMA__"


def test_candidate_for_returns_independent_copies():
    payload = llm_format.candidate_for(0, {"a": 1})
    payload["json_schema"]["schema"]["a"] = 999
    again = llm_format.candidate_for(0, {"a": 1})
    assert again["json_schema"]["schema"] == {"a": 1}


def test_candidate_for_out_of_range_returns_none():
    assert llm_format.candidate_for(999, {}) is None
    assert llm_format.candidate_for(-1, {}) is None


def test_candidate_for_the_terminal_entry_is_none():
    last = len(llm_format.SCHEMA_CANDIDATES) - 1
    assert llm_format.candidate_for(last, {}) is None


def test_advance_walks_towards_none():
    state = llm_format.SchemaState()
    steps = 0
    while state.advance():
        steps += 1
        assert steps < len(llm_format.SCHEMA_CANDIDATES)
    assert state.index() == len(llm_format.SCHEMA_CANDIDATES) - 1
    # A second advance must not run off the end.
    assert state.advance() is False
    assert state.index() == len(llm_format.SCHEMA_CANDIDATES) - 1


def test_confirm_pins_the_process_wide_candidate():
    first = llm_format.SchemaState()
    first.advance()
    first.confirm()

    second = llm_format.SchemaState()
    assert second.index() == first.index()


def test_a_rejection_alone_does_not_seed_the_cache():
    """Advancing past a candidate only proves it was refused."""
    state = llm_format.SchemaState()
    state.advance()
    assert llm_format._WORKING_INDEX is None


def test_reset_probe_cache_forgets_the_verdict():
    state = llm_format.SchemaState()
    state.advance()
    state.confirm()
    llm_format.reset_probe_cache()
    assert llm_format.SchemaState().index() == 0


def test_start_index_can_skip_probing():
    """``use_schema=False`` starts at the terminal candidate."""
    last = len(llm_format.SCHEMA_CANDIDATES) - 1
    state = llm_format.SchemaState(start_index=last)
    assert state.current() is None


def test_is_format_rejection_matches_format_wording():
    assert llm_format.is_format_rejection(
        RuntimeError(
            "Error code: 400 - Unsupported response_format type json_schema"
        )
    )
    assert llm_format.is_format_rejection(
        RuntimeError("Error code: 400 - json_object is not supported")
    )


def test_is_format_rejection_ignores_reasoning_wording():
    """The two candidate lists must not swallow each other's rejections."""
    assert not llm_format.is_format_rejection(
        RuntimeError("Error code: 400 - unknown parameter: reasoning")
    )


def test_is_format_rejection_ignores_auth():
    assert not llm_format.is_format_rejection(
        RuntimeError("Error code: 401 - invalid api key")
    )


def test_is_format_rejection_ignores_a_bare_rate_limit():
    assert not llm_format.is_format_rejection(
        RuntimeError("Error code: 429 - rate limited")
    )


def test_is_format_rejection_matches_the_hyphenated_feature_wording():
    """The exact wording OpenRouter returned for an unsupported model.

    A hyphenated ``structured-outputs`` slipped past an earlier, marker-list
    version of this predicate, so the whole fallback silently became a retry
    loop.  This is the regression guard for that.
    """
    assert llm_format.is_format_rejection(
        RuntimeError(
            "HTTP 400: model: inclusionai/ling-3.0-flash-sante does not "
            "support feature: structured-outputs"
        )
    )


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 400 - does not support structured outputs",
        "Error code: 400 - structured_outputs unsupported",
        "Error code: 400 - unsupported guided_json",
        "Error code: 400 - guided_choice is not supported",
        "Error code: 400 - does not support response_format",
        "Error code: 400 - json_object is not supported",
        "Error code: 400 - model does not accept json_schema",
    ],
)
def test_is_format_rejection_matches_provider_variants(message):
    assert llm_format.is_format_rejection(RuntimeError(message))


def test_is_format_rejection_ignores_an_unrelated_unsupported_feature():
    """A refusal with no format field name must not move the walk."""
    assert not llm_format.is_format_rejection(
        RuntimeError("Error code: 400 - model gpt-x does not support feature: tools")
    )


def test_is_format_rejection_ignores_a_model_not_found_400():
    assert not llm_format.is_format_rejection(
        RuntimeError(
            "Error code: 400 - deepseek/deepseek-r1:free is not a valid model ID"
        )
    )


def test_is_format_rejection_ignores_a_mixed_reasoning_payload():
    """A refusal aimed at the reasoning axis stays with reasoning.

    The payload mentions "structured output" too, but the refusal verb points
    at ``reasoning_effort``; claiming it here would send the walk down the
    wrong candidate list.
    """
    assert not llm_format.is_format_rejection(
        RuntimeError(
            "Error code: 400 - unsupported reasoning_effort with structured output"
        )
    )


def test_advance_is_thread_safe():
    """Parallel workers share one state; the index must never over-run."""
    state = llm_format.SchemaState()
    last = len(llm_format.SCHEMA_CANDIDATES) - 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: state.advance(), range(20)))

    assert 0 <= state.index() <= last
