"""Tests for the LLM completion stage.

Every provider interaction is mocked: no test in this file may reach the
network.
"""

from __future__ import annotations

import json

import pytest

from common import config
from common.config import (
    CODE_NOT_A_SINGLE_WORD,
    CODE_OK,
    SOURCE_EXTRACTOR,
    SOURCE_LLM,
    BoldEntry,
)
from excerpt import llm


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

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
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
        client = FakeClient(["junk", "junk again"])
        llm.complete_entries([entry], client=client, model="m")
        assert entry.code == CODE_NOT_A_SINGLE_WORD
        assert entry.detail


class TestPrompts:
    def test_sentence_prompt_focuses_on_pattern_not_meaning(self):
        entry = make_entry("what's up, what did he say?", "sentence")
        client = FakeClient([GOOD_REPLY])
        llm.complete_entries([entry], client=client, model="m")
        prompt = client.recorder[0]["messages"][1]["content"].lower()
        assert "sentence" in prompt
        assert "do not translate" in prompt

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
