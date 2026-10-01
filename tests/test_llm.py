"""Tests for the LLM completion stage.

Every provider interaction is mocked: no test in this file may reach the
network.
"""

from __future__ import annotations

import json
import threading

import pytest

from common import config
from common.config import (
    CODE_NOT_A_SINGLE_WORD,
    CODE_OK,
    SOURCE_EXTRACTOR,
    SOURCE_LLM,
    BoldEntry,
)
from llm import __main__ as llm
from llm import cache as llm_cache
from llm import format as llm_format
from llm import reasoning as llm_reasoning


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    """Keep the retry backoff from costing the suite real wall-clock time.

    The backoff itself is asserted in the tests that care; everywhere else it
    is stubbed out so a failing-record test does not sleep for seconds.
    """
    monkeypatch.setattr(llm.time, "sleep", lambda _seconds: None)


def make_entry(word="returning your call", type_="phrase", code=CODE_NOT_A_SINGLE_WORD):
    return BoldEntry(
        word=word,
        type=type_,
        sentence=f"here is {word} inside a sentence",
        line=3,
        code=code,
        detail="not a single word",
        source=SOURCE_EXTRACTOR,
    )


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeCompletion:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeChat:
    def __init__(self, payloads, recorder):
        self._payloads = list(payloads)
        self._recorder = recorder
        # Worker threads call ``create`` concurrently, so the pop-and-append
        # pair below must not interleave: without this guard a run with
        # several workers could hand the same canned reply to two records.
        self._lock = threading.Lock()

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        with self._lock:
            self._recorder.append(kwargs)
            if not self._payloads:
                raise RuntimeError("no more canned responses")
            payload = self._payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return FakeCompletion(payload)


class FakeClient:
    def __init__(self, payloads):
        self.recorder: list[dict] = []
        self.chat = FakeChat(payloads, self.recorder)


GOOD_REPLY = json.dumps(
    {
        "pos": "phrase",
        "definition": "calling someone back after they called you",
        "examples": ["This is Amanda, returning your call."],
        "synonyms": ["calling back"],
        "antonyms": [],
        "shortDefs": ["calling back"],
    }
)

#: A reply that reads the record as one word in one tense.  This is what models
#: actually returned for a whole sentence before the label was pinned, and it is
#: exactly what must never reach the disk.
TENSE_REPLY = json.dumps(
    {
        "pos": "past tense verb",
        "definition": "to move very quickly; to speed away",
        "examples": ["The car zoomed past us."],
        "synonyms": ["dash"],
        "antonyms": [],
        "shortDefs": ["zoom away = move quickly away"],
    }
)


class TestTargetSelection:
    def test_only_pending_non_word_entries_are_sent(self):
        phrase = make_entry("give up", "phrase")
        word = make_entry("obfuscate", "word", code=1003)
        already = make_entry("done deal", "phrase")
        already.source = SOURCE_LLM

        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([phrase, word, already], client=client, model="m")

        # Exactly one request: the pending phrase.
        assert len(client.recorder) == 1
        prompt = client.recorder[0]["messages"][1]["content"]
        assert "give up" in prompt

    def test_words_are_never_sent_to_the_llm(self):
        word = make_entry("obfuscate", "word", code=1003)
        client = FakeClient([])
        llm.complete_entries([word], client=client, model="m")
        assert client.recorder == []

    def test_no_targets_returns_immediately(self):
        entries = [make_entry("obfuscate", "word", code=1003)]
        result = llm.complete_entries(entries, client=FakeClient([]), model="m")
        assert result == entries


class TestCompletion:
    def test_successful_completion_updates_the_entry(self):
        entry = make_entry()
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")

        assert entry.code == CODE_OK
        assert entry.source == SOURCE_LLM
        assert entry.entry["senses"][0]["definition"].startswith("calling someone back")
        assert entry.entry["synonyms"] == ["calling back"]

    def test_completed_entry_has_the_standard_key_set(self):
        entry = make_entry()
        llm.complete_entries([entry], client=FakeClient([GOOD_REPLY]), model="m")
        assert set(entry.entry.keys()) == set(config.STANDARD_ENTRY_KEYS)

    def test_etymology_and_first_use_stay_empty(self):
        entry = make_entry()
        llm.complete_entries([entry], client=FakeClient([GOOD_REPLY]), model="m")
        assert entry.entry["etymology"] == ""
        assert entry.entry["firstUse"] == ""

    def test_idempotent_on_rerun(self):
        entry = make_entry()
        first = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=first, model="m")
        assert len(first.recorder) == 1

        second = FakeClient([])
        llm.complete_entries([entry], client=second, model="m")
        assert second.recorder == []

    def test_bad_json_is_retried_then_survives(self):
        entry = make_entry()
        client = FakeClient(["not json at all", GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        assert entry.code == CODE_OK
        assert len(client.recorder) == 2

    def test_missing_definition_is_rejected(self):
        entry = make_entry()
        client = FakeClient([json.dumps({"pos": "phrase"}), GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        assert entry.code == CODE_OK

    def test_persistent_failure_leaves_the_entry_alone(self):
        entry = make_entry()
        # Enough failures to exhaust the default retry budget (3 retries + 1).
        client = FakeClient(["junk"] * 4)
        llm.complete_entries([entry], client=client, model="m")
        # A failure must leave ``code``/``source`` untouched: that is what keeps
        # the record pending and retriable on the next run.
        assert entry.code == CODE_NOT_A_SINGLE_WORD
        assert entry.source == SOURCE_EXTRACTOR
        assert entry.detail

    def test_sentence_pos_is_pinned_to_the_literal_label(self):
        """A sentence has no part of speech, so its label is structural.

        The prompt asks for ``sentence``, but a model that answers "past tense
        verb" regardless must still not be able to write that to disk.  Both
        the entry and its single sense carry the pinned label, because the note
        body reads the sense while the index table reads the entry.
        """
        entry = make_entry("he zoomed away", "sentence")
        llm.complete_entries([entry], client=FakeClient([TENSE_REPLY]), model="m")

        assert entry.entry["pos"] == "sentence"
        assert entry.entry["senses"][0]["pos"] == "sentence"

    def test_phrase_pos_keeps_the_model_value(self):
        """Only sentences are pinned; a phrase keeps the model's own label."""
        entry = make_entry("give up", "phrase")
        llm.complete_entries([entry], client=FakeClient([GOOD_REPLY]), model="m")

        assert entry.entry["pos"] == "phrase"
        assert entry.entry["senses"][0]["pos"] == "phrase"


class TestPrompts:
    def test_sentence_prompt_focuses_on_pattern_not_meaning(self):
        entry = make_entry("what's up, what did he say?", "sentence")
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        prompt = client.recorder[0]["messages"][1]["content"].lower()
        assert "sentence" in prompt
        assert "do not translate" in prompt

    def test_sentence_prompt_forbids_the_word_level_reading(self):
        """The old wording asked for the verb's meaning and got exactly that."""
        entry = make_entry("what's up, what did he say?", "sentence")
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        prompt = client.recorder[0]["messages"][1]["content"].lower()
        assert "not a word" in prompt
        assert "single word" in prompt

    def test_sentence_record_is_not_printed_twice(self):
        """A sentence record bolds the whole sentence, so one line is enough.

        ``find_sentence`` keeps the ``**`` markers, so ``word`` is a substring
        of ``sentence`` and the old prompt stated the same text twice — which
        read as an invitation to pick a word out of it.
        """
        entry = make_entry("what's up, what did he say?", "sentence")
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        prompt = client.recorder[0]["messages"][1]["content"]
        assert "Bold text:" not in prompt
        assert prompt.count("what's up") == 1

    def test_phrase_record_keeps_its_context_line(self):
        """A phrase's enclosing sentence is the only context it has."""
        entry = make_entry("returning your call", "phrase")
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        prompt = client.recorder[0]["messages"][1]["content"]
        assert "Bold text: 'returning your call'" in prompt
        assert "Source sentence:" in prompt

    def test_phrase_prompt_mentions_meaning_and_synonyms(self):
        entry = make_entry("give up", "phrase")
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        prompt = client.recorder[0]["messages"][1]["content"].lower()
        assert "phrase" in prompt
        assert "synonym" in prompt

    def test_requests_english_output(self):
        entry = make_entry()
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        prompt = client.recorder[0]["messages"][1]["content"].lower()
        assert "english" in prompt

    def test_example_and_antonym_quality_guidance_is_present(self):
        """Examples must be adult prose, not textbook sentences.

        The distinction is the whole point of the instruction, so the wording
        that carries it is asserted rather than left to drift.
        """
        entry = make_entry()
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        prompt = client.recorder[0]["messages"][1]["content"].lower()
        assert "about two" in prompt
        assert "school-textbook" in prompt
        assert "idiomatic" in prompt
        assert "antonym" in prompt

    def test_quality_guidance_reaches_both_record_types(self):
        """One instruction covers phrases and sentences alike."""
        for type_ in ("phrase", "sentence"):
            entry = make_entry("give up", type_)
            client = FakeClient([GOOD_REPLY])
            llm.complete_entries([entry], client=client, model="m")
            prompt = client.recorder[0]["messages"][1]["content"].lower()
            assert "school-textbook" in prompt

    def test_reply_contract_is_stated_in_the_prompt(self):
        """The schema is not always sent, so the keys must survive without it.

        ``response_format`` walks down to "send nothing", which makes the
        prompt the only statement of the required keys on the last rungs.
        """
        entry = make_entry()
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m", use_schema=False)
        assert "response_format" not in client.recorder[0]
        prompt = client.recorder[0]["messages"][1]["content"].lower()
        for key in ("pos", "definition", "examples", "synonyms", "antonyms", "shortdefs"):
            assert key in prompt

    def test_structured_output_is_requested_by_default(self):
        entry = make_entry()
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m", use_schema=True)
        assert client.recorder[0]["response_format"]["type"] == "json_schema"


class TestSettings:
    def test_missing_client_requires_an_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config.configure(dict_choice="MW", use_llm=True, env_path="")
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            llm.complete_entries([make_entry()])

    def test_schema_is_exposed(self):
        schema = llm.schema_for()
        assert schema["type"] == "object"
        assert "definition" in schema["required"]


class TestRequestParameters:
    """The request must carry the LLM's own timeout and a token ceiling."""

    def test_completion_sends_timeout_and_token_ceiling(self):
        entry = make_entry()
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")

        sent = client.recorder[0]
        assert sent["timeout"] == 60.0
        assert sent["max_tokens"] == llm.MAX_COMPLETION_TOKENS
        # Pinned as a literal as well: the budget has to leave room for the
        # full-length examples the prompt asks for, so shrinking it back to the
        # old 400 is a real regression, not a free tweak.
        assert sent["max_tokens"] == 500

    def test_timeout_follows_the_configured_value(self):
        config.configure(
            dict_choice="MW", use_llm=False, env_path="", llm_timeout=7.5
        )
        entry = make_entry()
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        assert client.recorder[0]["timeout"] == 7.5

    def test_retry_budget_follows_the_configured_value(self):
        config.configure(
            dict_choice="MW", use_llm=False, env_path="", llm_retries=1
        )
        entry = make_entry()
        # 1 retry means 2 attempts; both fail.
        client = FakeClient(["junk", "junk"])
        llm.complete_entries([entry], client=client, model="m")
        assert len(client.recorder) == 2

    def test_client_is_built_with_a_timeout_and_no_sdk_retries(self, monkeypatch):
        """The SDK's own retrying would multiply with this module's loop."""
        captured: dict = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        config.configure(dict_choice="MW", use_llm=True, env_path="")

        llm._create_client()

        assert captured["timeout"] == 60.0
        assert captured["max_retries"] == 0


class TestBackoff:
    """Retries must pause, and never pause before the first attempt."""

    def test_no_sleep_before_the_first_attempt(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(llm.time, "sleep", sleeps.append)

        llm.complete_entries([make_entry()], client=FakeClient([GOOD_REPLY]), model="m")

        assert sleeps == []

    def test_backoff_grows_linearly(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(llm.time, "sleep", sleeps.append)
        config.configure(
            dict_choice="MW", use_llm=False, env_path="", llm_backoff=0.25
        )

        client = FakeClient(["junk", "junk", GOOD_REPLY])
        llm.complete_entries([make_entry()], client=client, model="m")

        assert sleeps == [0.25, 0.5]


class TestErrorClassification:
    def test_auth_statuses_are_fatal(self):
        for status in (401, 403):
            exc = RuntimeError(f"Error code: {status} - nope")
            assert llm._classify_llm_error(exc) == "auth"

    def test_transient_statuses_are_not_fatal(self):
        assert llm._classify_llm_error(RuntimeError("Error code: 429 - slow")) == "rate_limit"
        assert llm._classify_llm_error(RuntimeError("Error code: 503 - down")) == "server"
        assert llm._classify_llm_error(TimeoutError("timeout")) == "network"
        assert llm._classify_llm_error(ValueError("bad json")) == "bad_reply"

    def test_extract_status_reads_the_sdk_marker(self):
        assert llm._extract_status("Error code: 429 - x") == 429
        assert llm._extract_status("no marker here") == 0


class TestCircuitBreaker:
    def test_auth_failure_aborts_the_batch_at_once(self):
        first = make_entry("give up", "phrase")
        second = make_entry("hold on", "phrase")
        # Only one canned response: a second request would raise "no more
        # canned responses", which is not the failure under test.
        client = FakeClient([RuntimeError("Error code: 401 - bad key")])

        llm.complete_entries([first, second], client=client, model="m")

        assert len(client.recorder) == 1
        # The untouched record must stay pending so a later run retries it.
        assert second.code == CODE_NOT_A_SINGLE_WORD
        assert second.source == SOURCE_EXTRACTOR

    def test_consecutive_failures_abort_a_concurrent_run(self):
        entries = [make_entry(f"phrase {i}", "phrase") for i in range(6)]
        client = FakeClient([RuntimeError("Error code: 503 - down")] * 10)
        config.configure(
            dict_choice="MW", use_llm=False, env_path="", llm_retries=0
        )

        llm.complete_entries(entries, client=client, model="m", workers=4)

        # The breaker trips after five consecutive failures, so the sixth
        # record is never attempted.
        assert len(client.recorder) == 5

    def test_serial_run_does_not_trip_on_ordinary_failures(self):
        """A few flaky records must not cost the rest of a serial run."""
        entries = [make_entry(f"phrase {i}", "phrase") for i in range(6)]
        client = FakeClient(
            [RuntimeError("Error code: 503 - down")] * 5 + [GOOD_REPLY]
        )
        config.configure(
            dict_choice="MW", use_llm=False, env_path="", llm_retries=0
        )

        llm.complete_entries(entries, client=client, model="m")

        assert len(client.recorder) == 6
        assert entries[-1].code == CODE_OK


class TestConcurrency:
    def test_workers_above_one_completes_every_record(self):
        entries = [make_entry(f"phrase {i}", "phrase") for i in range(3)]
        client = FakeClient([GOOD_REPLY] * 3)

        llm.complete_entries(entries, client=client, model="m", workers=3)

        assert len(client.recorder) == 3
        assert all(entry.code == CODE_OK for entry in entries)
        assert all(entry.source == SOURCE_LLM for entry in entries)

    def test_logs_stay_in_input_order(self, capsys):
        entries = [make_entry(f"phrase {i}", "phrase") for i in range(3)]
        client = FakeClient([GOOD_REPLY] * 3)

        llm.complete_entries(entries, client=client, model="m", workers=3)
        out = capsys.readouterr().out

        assert out.index("[llm 1/3]") < out.index("[llm 2/3]") < out.index("[llm 3/3]")

    def test_workers_never_write_files(self, monkeypatch):
        """Only the calling thread may touch .words.json / .errors.json."""
        import lookup.write_json as write_json

        def explode(*args, **kwargs):
            raise AssertionError("a worker thread must not write files")

        monkeypatch.setattr(write_json, "write_documents", explode)

        entries = [make_entry(f"phrase {i}", "phrase") for i in range(3)]
        client = FakeClient([GOOD_REPLY] * 3)
        llm.complete_entries(entries, client=client, model="m", workers=3)

    def test_workers_one_keeps_the_serial_path(self, monkeypatch):
        class ExplodingPool:
            def __init__(self, *args, **kwargs):
                raise AssertionError("workers=1 must not spawn a thread pool")

        monkeypatch.setattr(llm, "ThreadPoolExecutor", ExplodingPool)

        llm.complete_entries(
            [make_entry()], client=FakeClient([GOOD_REPLY]), model="m", workers=1
        )


class TestStreaming:
    """Each record is reported as soon as it settles, not in one burst."""

    def test_serial_run_prints_before_the_next_request_starts(self):
        """With workers=1 the first line must land before request two."""
        order: list[str] = []

        class OrderingClient:
            def __init__(self):
                self.chat = self
                self._count = 0

            @property
            def completions(self):
                return self

            def create(self, **kwargs):
                order.append(f"request {self._count + 1}")
                self._count += 1
                return FakeCompletion(GOOD_REPLY)

        entries = [make_entry(f"phrase {i}", "phrase") for i in range(3)]
        client = OrderingClient()

        # Patch print just for this test's window so the interleaving of
        # "request N" and the per-record line is observable.
        import builtins

        original = builtins.print

        def spy(*args, **kwargs):
            text = " ".join(str(arg) for arg in args)
            if "[llm " in text:
                order.append(f"print {text.split('/')[0].split()[-1]}")
            original(*args, **kwargs)

        builtins.print = spy
        try:
            llm.complete_entries(entries, client=client, model="m", workers=1)
        finally:
            builtins.print = original

        # request 1, print 1, request 2, print 2, request 3, print 3
        assert order == [
            "request 1",
            "print 1",
            "request 2",
            "print 2",
            "request 3",
            "print 3",
        ]

    def test_concurrent_completions_are_still_ordered_and_complete(self, capsys):
        entries = [make_entry(f"phrase {i}", "phrase") for i in range(5)]
        client = FakeClient([GOOD_REPLY] * 5)

        llm.complete_entries(entries, client=client, model="m", workers=4)
        out = capsys.readouterr().out

        positions = [out.index(f"[llm {i}/5]") for i in range(1, 6)]
        assert positions == sorted(positions)
        assert all(entry.code == CODE_OK for entry in entries)

    def test_an_out_of_order_completion_does_not_skip_the_queue(self, capsys):
        """A fast record behind a slow one must wait, then flush in order."""
        import time as time_module

        class SlowFirstClient:
            def __init__(self):
                self.chat = self
                self._lock = threading.Lock()
                self._count = 0

            @property
            def completions(self):
                return self

            def create(self, **kwargs):
                with self._lock:
                    self._count += 1
                    nth = self._count
                if nth == 1:
                    # Hold the first request longer than the rest.
                    time_module.sleep(0.15)
                return FakeCompletion(GOOD_REPLY)

        entries = [make_entry(f"phrase {i}", "phrase") for i in range(4)]
        llm.complete_entries(
            entries, client=SlowFirstClient(), model="m", workers=4
        )
        out = capsys.readouterr().out
        positions = [out.index(f"[llm {i}/4]") for i in range(1, 5)]
        assert positions == sorted(positions)
        assert out.count(": ok") == 4


class TestReasoningSuppression:
    def test_a_rejected_dialect_falls_back_to_the_next_candidate(self):
        entry = make_entry()
        client = FakeClient(
            [
                RuntimeError("Error code: 400 - unknown parameter: reasoning"),
                GOOD_REPLY,
            ]
        )

        llm.complete_entries([entry], client=client, model="m")

        assert entry.code == CODE_OK
        # The first request carried candidate 0, the second a later candidate.
        assert client.recorder[0]["extra_body"] == llm_reasoning.REASONING_CANDIDATES[0]
        assert client.recorder[1]["extra_body"] != client.recorder[0]["extra_body"]

    def test_an_explicit_override_is_sent_first(self):
        config.configure(
            dict_choice="MW",
            use_llm=False,
            env_path="",
        )
        # ``configure`` reads LLM_REASONING from the environment.
        import os

        os.environ["LLM_REASONING"] = '{"reasoning": {"effort": "low"}}'
        config.configure(dict_choice="MW", use_llm=False, env_path="")
        try:
            entry = make_entry()
            client = FakeClient([GOOD_REPLY])
            llm.complete_entries([entry], client=client, model="m")
            assert client.recorder[0]["extra_body"] == {
                "reasoning": {"effort": "low"}
            }
        finally:
            os.environ.pop("LLM_REASONING", None)

    def test_none_is_sent_when_every_candidate_is_rejected(self):
        config.configure(
            dict_choice="MW", use_llm=False, env_path="", llm_retries=0
        )
        entry = make_entry()
        rejects = [
            RuntimeError(f"Error code: 400 - unknown parameter {i}")
            for i in range(len(llm_reasoning.REASONING_CANDIDATES))
        ]
        client = FakeClient(rejects + [GOOD_REPLY])

        llm.complete_entries([entry], client=client, model="m")

        assert entry.code == CODE_OK
        # The last resort sends no reasoning field at all.
        assert "extra_body" not in client.recorder[-1]


class TestSchemaFallback:
    """A refused structured-output dialect must degrade, never abort."""

    def setup_method(self):
        llm_format.reset_probe_cache()

    def test_schema_rejection_tries_the_next_dialect(self):
        entry = make_entry()
        client = FakeClient(
            [
                RuntimeError(
                    "Error code: 400 - Unsupported response_format type json_schema"
                ),
                GOOD_REPLY,
            ]
        )

        llm.complete_entries([entry], client=client, model="m")

        assert entry.code == CODE_OK
        assert client.recorder[0]["response_format"]["json_schema"]["strict"] is True
        # The second request used a later dialect.
        assert (
            client.recorder[1]["response_format"]
            != client.recorder[0]["response_format"]
        )

    def test_a_downgrade_does_not_consume_the_retry_budget(self):
        config.configure(
            dict_choice="MW", use_llm=False, env_path="", llm_retries=0
        )
        entry = make_entry()
        # One retry budget (a single attempt); the dialect walk must still
        # reach the working candidate without being starved by it.
        client = FakeClient(
            [
                RuntimeError("Error code: 400 - unsupported response_format"),
                GOOD_REPLY,
            ]
        )

        llm.complete_entries([entry], client=client, model="m")

        assert entry.code == CODE_OK
        assert len(client.recorder) == 2

    def test_schema_downgrade_is_remembered_across_records(self):
        """A dialect proved unsupported must not be re-probed per record."""
        first = make_entry("give up", "phrase")
        second = make_entry("hold on", "phrase")
        client = FakeClient(
            [
                RuntimeError("Error code: 400 - unsupported response_format"),
                GOOD_REPLY,
                GOOD_REPLY,
            ]
        )

        llm.complete_entries([first, second], client=client, model="m")

        assert first.code == CODE_OK
        assert second.code == CODE_OK
        # Exactly one request ever carried the *strict* schema dialect: the
        # rejection is remembered for the rest of the run.  (Later dialects
        # also use type "json_schema", so the strict flag is what identifies
        # the first candidate.)
        strict_calls = [
            call
            for call in client.recorder
            if call.get("response_format", {})
            .get("json_schema", {})
            .get("strict")
            is True
        ]
        assert len(strict_calls) == 1

    def test_all_dialects_rejected_still_succeeds(self):
        """The terminal candidate sends no response_format and cannot fail."""
        entry = make_entry()
        refusals = [
            RuntimeError(
                f"Error code: 400 - unsupported response_format variant {i}"
            )
            for i in range(len(llm_format.SCHEMA_CANDIDATES))
        ]
        client = FakeClient(refusals + [GOOD_REPLY])

        llm.complete_entries([entry], client=client, model="m")

        assert entry.code == CODE_OK
        assert "response_format" not in client.recorder[-1]

    def test_every_dialect_rejected_still_terminates(self, monkeypatch):
        """A provider that refuses everything must not loop forever."""
        monkeypatch.setattr(llm.time, "sleep", lambda _seconds: None)
        config.configure(
            dict_choice="MW", use_llm=False, env_path="", llm_retries=0
        )
        entry = make_entry()
        client = FakeClient(
            [RuntimeError("Error code: 400 - unsupported response_format")] * 40
        )

        llm.complete_entries([entry], client=client, model="m")

        # Bounded: one attempt against each dialect, then one more with the
        # terminal (format-free) candidate.  The record stays pending.
        assert len(client.recorder) <= len(llm_format.SCHEMA_CANDIDATES) + 1
        assert entry.code == CODE_NOT_A_SINGLE_WORD
        assert entry.source == SOURCE_EXTRACTOR

    def test_unsupported_is_announced_once(self, capsys):
        entry = make_entry()
        refusals = [
            RuntimeError("Error code: 400 - unsupported response_format")
        ] * len(llm_format.SCHEMA_CANDIDATES)
        client = FakeClient(refusals + [GOOD_REPLY])

        llm.complete_entries([entry], client=client, model="m")
        out = capsys.readouterr().out

        assert out.count("structured output unsupported") == 1

    def test_format_rejection_does_not_swallow_a_reasoning_rejection(self):
        """The two candidate lists must stay independent."""
        assert not llm_format.is_format_rejection(
            RuntimeError("Error code: 400 - unknown parameter: reasoning")
        )

    def test_the_reproduced_feature_wording_falls_back(self):
        """Regression: the real OpenRouter refusal must trigger the walk.

        The wording is copied verbatim from a live run.  Before the fix it
        matched no marker, so the walk never advanced and the retries were
        spent re-sending the same refused request.
        """
        config.configure(
            dict_choice="MW", use_llm=False, env_path="", llm_retries=0
        )
        entry = make_entry()
        client = FakeClient(
            [
                RuntimeError(
                    "HTTP 400: model: inclusionai/ling-3.0-flash-sante does "
                    "not support feature: structured-outputs"
                ),
                GOOD_REPLY,
            ]
        )

        llm.complete_entries([entry], client=client, model="m")

        assert entry.code == CODE_OK
        assert len(client.recorder) == 2
        assert client.recorder[0]["response_format"]["json_schema"]["strict"] is True
        # The fallback reached a dialect the endpoint accepted.
        assert (
            client.recorder[1]["response_format"]
            != client.recorder[0]["response_format"]
        )

    def test_an_unrecognised_400_advances_one_candidate(self):
        """A 400 we cannot attribute is still treated as a request-shape problem.

        ``response_format`` is the part of the request this module varies, so
        trying the next candidate is strictly better than re-sending the
        request that was just refused.
        """
        config.configure(
            dict_choice="MW", use_llm=False, env_path="", llm_retries=0
        )
        entry = make_entry()
        client = FakeClient(
            [RuntimeError("Error code: 400 - oops"), GOOD_REPLY]
        )

        llm.complete_entries([entry], client=client, model="m")

        assert entry.code == CODE_OK
        assert len(client.recorder) == 2
        assert (
            client.recorder[1]["response_format"]
            != client.recorder[0]["response_format"]
        )

    def test_a_reasoning_400_is_never_treated_as_a_format_rejection(
        self, monkeypatch
    ):
        """A reasoning refusal must not be consumed by the format walk.

        The message contains no format field name, so the format predicate
        declines it and the request is handled by the reasoning walk instead.
        Only once that walk is exhausted does the generic ``bad_request``
        fallback advance the format walk — which is the correct order, since
        by then the reasoning field has been dropped altogether.
        """
        monkeypatch.setattr(llm.time, "sleep", lambda _seconds: None)
        config.configure(
            dict_choice="MW",
            use_llm=False,
            env_path="",
            llm_retries=3,
            llm_workers=1,
        )
        exc = RuntimeError(
            "Error code: 400 - invalid_request_error: unknown parameter "
            "reasoning_effort"
        )
        assert llm._classify_llm_error(exc) == "bad_request"
        assert not llm_format.is_format_rejection(exc)

        entry = make_entry()
        client = FakeClient([exc] * 6 + [GOOD_REPLY])

        llm.complete_entries([entry], client=client, model="m")

        # The reasoning walk steps through every reasoning candidate without
        # disturbing the format walk: the format dialect stays on its first
        # candidate for exactly as many requests as the reasoning walk takes.
        reasoning_candidates = len(
            llm_reasoning.ReasoningState().candidates
        )
        first_dialects = [
            call.get("response_format") for call in client.recorder
        ]
        strict = [
            call
            for call in client.recorder
            if call.get("response_format", {})
            .get("json_schema", {})
            .get("strict")
            is True
        ]
        assert len(strict) == reasoning_candidates
        # ...and the format walk only starts moving after that.
        assert first_dialects[-1]["type"] != "json_schema"


class TestErrorSummary:
    """Provider errors must be reduced to one actionable line."""

    def test_openrouter_wrapper_is_unwrapped(self):
        exc = RuntimeError(
            "Error code: 429 - {'error': {'message': 'Provider returned error', "
            "'code': 429, 'metadata': {'raw': 'google/gemma-4-31b-it:free is "
            "temporarily rate-limited upstream. Please retry shortly', "
            "'provider_name': 'Google AI Studio'}}}"
        )
        summary = llm._summarize_error(exc)
        assert summary.startswith("HTTP 429")
        assert "rate-limited upstream" in summary
        assert "user_id" not in summary

    def test_nested_json_blob_is_skipped(self):
        exc = RuntimeError(
            "Error code: 400 - {'error': {'message': 'Provider returned error', "
            "'metadata': {'raw': '{\\n  \"error\": {\\n    \"code\": 400,\\n    "
            "\"message\": \"User location is not supported for the API use.\"\\n  }\\n}'}}}"
        )
        summary = llm._summarize_error(exc)
        assert "HTTP 400" in summary

    def test_plain_error_still_reports_status(self):
        summary = llm._summarize_error(RuntimeError("Error code: 503 - boom"))
        assert summary.startswith("HTTP 503")

    def test_no_status_degrades_gracefully(self):
        summary = llm._summarize_error(ValueError("something odd"))
        assert summary == "request failed"


class TestLlmCacheIntegration:
    """The stage consults and fills the cache when one is handed to it."""

    def test_a_second_run_makes_no_requests(self, tmp_path, capsys):
        entries = [make_entry(f"phrase {i}", "phrase") for i in range(3)]
        cache = llm_cache.LlmCache("m", tmp_path / "c.json")

        first = FakeClient([GOOD_REPLY] * 3)
        llm.complete_entries(entries, client=first, model="m", cache=cache)
        assert len(first.recorder) == 3

        # A brand-new client, so any request at all would be visible.
        entries2 = [make_entry(f"phrase {i}", "phrase") for i in range(3)]
        second = FakeClient([])
        llm.complete_entries(entries2, client=second, model="m", cache=cache)

        assert second.recorder == []
        assert cache.hits == 3
        assert all(entry.code == CODE_OK for entry in entries2)
        out = capsys.readouterr().out
        assert out.count(": ok (cache)") == 3

    def test_a_fresh_success_is_reported_without_the_marker(self, capsys):
        cache = llm_cache.LlmCache("m", None)
        cache.enabled = False  # isolate the formatting from any real store
        llm.complete_entries(
            [make_entry()], client=FakeClient([GOOD_REPLY]), model="m", cache=cache
        )
        out = capsys.readouterr().out
        assert ": ok" in out
        assert "(cache)" not in out

    def test_a_failure_is_not_cached(self, tmp_path):
        cache = llm_cache.LlmCache("m", tmp_path / "c.json")
        entry = make_entry()
        client = FakeClient([RuntimeError("HTTP 500 boom")])

        llm.complete_entries([entry], client=client, model="m", cache=cache)
        assert len(cache) == 0
        # The record stays pending so a later run may succeed: a failed
        # request must never be frozen into the cache.
        assert entry.source == SOURCE_EXTRACTOR
        assert entry.code != CODE_OK
        assert entry.detail

    def test_cache_hit_skips_the_request_entirely(self, tmp_path):
        cache = llm_cache.LlmCache("m", tmp_path / "c.json")
        entry = make_entry()
        llm.complete_entries(
            [entry], client=FakeClient([GOOD_REPLY]), model="m", cache=cache
        )

        # A client with no canned replies would raise if called at all.
        entry2 = make_entry()
        caller = FakeClient([])
        llm.complete_entries([entry2], client=caller, model="m", cache=cache)
        assert caller.recorder == []
        assert entry2.entry is not None

    def test_concurrent_hits_keep_the_log_ordered(self, tmp_path, capsys):
        cache = llm_cache.LlmCache("m", tmp_path / "c.json")
        llm.complete_entries(
            [make_entry(f"p{i}", "phrase") for i in range(4)],
            client=FakeClient([GOOD_REPLY] * 4),
            model="m",
            cache=cache,
        )
        capsys.readouterr()  # discard the first run's output

        entries = [make_entry(f"p{i}", "phrase") for i in range(4)]
        llm.complete_entries(
            entries, client=FakeClient([]), model="m", cache=cache, workers=4
        )
        out = capsys.readouterr().out
        positions = [out.index(f"[llm {i}/4]") for i in range(1, 5)]
        assert positions == sorted(positions)

    def test_an_empty_payload_is_still_served_from_cache(self, tmp_path):
        """A cached reply must be reused verbatim, not re-derived."""
        cache = llm_cache.LlmCache("m", tmp_path / "c.json")
        entry = make_entry()
        llm.complete_entries(
            [entry], client=FakeClient([GOOD_REPLY]), model="m", cache=cache
        )

        reused = make_entry()
        llm.complete_entries(
            [reused], client=FakeClient([]), model="m", cache=cache
        )
        assert reused.entry == entry.entry



class TestPackageSurface:
    """``llm`` is a package, so its entry points must be reachable from it.

    ``excerpt.cli`` calls ``llm.run`` as an *attribute* of the imported module,
    which is what keeps the CLI tests' monkeypatching effective.  These
    re-exports are the contract that makes that spelling possible.
    """

    def test_the_stage_is_importable_from_the_package_root(self):
        import llm as llm_package

        assert callable(llm_package.run)
        assert callable(llm_package.complete_entries)
        assert callable(llm_package.schema_for)
        assert isinstance(llm_package.SYSTEM_PROMPT, str)

    def test_cli_reaches_the_stage_through_the_package(self, monkeypatch):
        """Patching the package attribute must reach the CLI's call site.

        ``from llm import run`` in ``cli`` would bind the function by value and
        send the test to the network; this asserts the attribute access that
        makes the patch land.
        """
        import llm as llm_package

        seen: list = []
        monkeypatch.setattr(
            llm_package, "run", lambda entries, *a, **k: seen.append(entries) or entries
        )
        assert llm_package.run([]) == []
        assert seen == [[]]


class TestPackageSurface:
    """``llm`` is a package, so its entry points must be reachable from it.

    ``excerpt.cli`` calls ``llm.run`` as an *attribute* of the imported module,
    which is what keeps the CLI tests' monkeypatching effective.  These
    re-exports are the contract that makes that spelling possible.
    """

    def test_the_stage_is_importable_from_the_package_root(self):
        import llm as llm_package

        assert callable(llm_package.run)
        assert callable(llm_package.complete_entries)
        assert callable(llm_package.schema_for)
        assert isinstance(llm_package.SYSTEM_PROMPT, str)

    def test_cli_reaches_the_stage_through_the_package(self, monkeypatch):
        """Patching the package attribute must reach the CLI's call site.

        ``from llm import run`` in ``cli`` would bind the function by value and
        send the test to the network; this asserts the attribute access that
        makes the patch land.
        """
        import llm as llm_package

        seen: list = []
        monkeypatch.setattr(
            llm_package, "run", lambda entries, *a, **k: seen.append(entries) or entries
        )
        assert llm_package.run([]) == []
        assert seen == [[]]
