"""Tests for the reasoning-suppression candidate walker.

Offline by construction: the module is pure data plus a tiny state machine.
"""

from __future__ import annotations

import pytest

from excerpt import llm_reasoning


@pytest.fixture(autouse=True)
def clean_probe_cache():
    llm_reasoning.reset_probe_cache()
    yield
    llm_reasoning.reset_probe_cache()


def test_a_plain_run_starts_at_the_first_candidate():
    state = llm_reasoning.ReasoningState()
    assert state.current() == llm_reasoning.REASONING_CANDIDATES[0]


def test_an_override_leads_the_candidates():
    override = {"reasoning": {"effort": "low"}}
    state = llm_reasoning.ReasoningState(override=override)
    assert state.current() == override
    # The built-ins stay behind it as a fallback.
    assert llm_reasoning.REASONING_CANDIDATES[0] in state.candidates


def test_the_last_resort_sends_nothing():
    state = llm_reasoning.ReasoningState()
    while state.advance():
        pass
    assert state.current() is None


def test_advance_reports_when_the_list_is_exhausted():
    state = llm_reasoning.ReasoningState()
    assert state.advance() is True
    for _ in range(len(state.candidates)):
        if not state.advance():
            break
    assert state.advance() is False
    assert llm_reasoning._WORKING_CANDIDATE == -1


def test_a_confirmed_candidate_is_reused_without_re_probing():
    first = llm_reasoning.ReasoningState()
    first.advance()
    first.confirm()

    second = llm_reasoning.ReasoningState()
    assert second.current() == first.current()


def test_a_rejection_alone_does_not_seed_the_cache():
    """Advancing past a candidate only proves it failed."""
    state = llm_reasoning.ReasoningState()
    state.advance()
    assert llm_reasoning._WORKING_CANDIDATE is None


def test_rejection_markers_are_recognised():
    assert llm_reasoning.is_reasoning_rejection(
        RuntimeError("unknown parameter: reasoning")
    )
    assert llm_reasoning.is_reasoning_rejection(
        RuntimeError("extra fields not permitted")
    )


def test_unrelated_errors_are_not_reasoning_rejections():
    assert not llm_reasoning.is_reasoning_rejection(
        RuntimeError("Error code: 401 - invalid api key")
    )
    assert not llm_reasoning.is_reasoning_rejection(
        RuntimeError("Error code: 429 - rate limited")
    )


def test_index_reports_the_current_position():
    state = llm_reasoning.ReasoningState()
    assert state.index() == 0
    state.advance()
    assert state.index() == 1


def test_advance_is_thread_safe():
    """Many workers sharing one state must not skip or repeat a candidate.

    ``complete_entries`` hands the same instance to every parallel worker, so
    a lost update here would make two records send different dialects than the
    one the process agreed on.
    """
    import threading

    state = llm_reasoning.ReasoningState()
    total = len(state.candidates) - 1  # candidate count minus the terminal one
    results: list[bool] = []
    start = threading.Barrier(total)

    def walk():
        start.wait()
        results.append(state.advance())

    threads = [threading.Thread(target=walk) for _ in range(total)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Every step forward succeeded exactly once, and the walk landed on the
    # terminal candidate rather than overshooting it.
    assert results == [True] * total
    assert state.index() == total
    assert state.current() is None
