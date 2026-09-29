"""Tests for the lookup package: caching, HTTP handling, data cleaning, output."""

from __future__ import annotations

import json

import pytest
import requests

from common import config
from common.config import (
    CODE_BAD_RESPONSE,
    CODE_INVALID_API_KEY,
    CODE_LOOKUP_FAILED,
    CODE_MISSING_API_KEY,
    CODE_OK,
    CODE_RATE_LIMITED,
    CODE_WORD_NOT_FOUND,
    BoldEntry,
)
from lookup import cache as cache_mod
from lookup import mw_api
from lookup import write_json
from lookup.mw_markup import clean_text
from lookup.outcome import classify_http_result


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class TestWordCache:
    def test_default_path_uses_the_dict_slug(self, tmp_path, monkeypatch):
        monkeypatch.delenv("EXCERPT_CACHE_PATH", raising=False)
        resolved = cache_mod.cache_path_for("MW")
        assert resolved.name == "mw.cache.json"
        assert resolved.parent.name == "excerpt"

    def test_round_trip(self, tmp_path):
        path = tmp_path / "c.json"
        cache = cache_mod.WordCache(path)
        cache.put_raw("director", CODE_OK, {"hwi": {}})
        cache.save()

        reloaded = cache_mod.WordCache(path)
        assert reloaded.get_raw("director") == {"hwi": {}}

    def test_only_deterministic_codes_are_stored(self, tmp_path):
        cache = cache_mod.WordCache(tmp_path / "c.json")
        cache.put_raw("word", CODE_RATE_LIMITED, {"x": 1})
        cache.put_raw("word", CODE_LOOKUP_FAILED, {"x": 2})
        assert cache.get_raw("word") is None

    def test_lookup_is_case_insensitive(self, tmp_path):
        cache = cache_mod.WordCache(tmp_path / "c.json")
        cache.put_raw("Director", CODE_OK, {"a": 1})
        assert cache.get_raw("DIRECTOR") == {"a": 1}

    def test_disabled_cache_never_hits(self, tmp_path):
        path = tmp_path / "c.json"
        enabled = cache_mod.WordCache(path)
        enabled.put_raw("word", CODE_OK, {"a": 1})
        enabled.save()

        disabled = cache_mod.WordCache(path, enabled=False)
        assert disabled.get_raw("word") is None

    def test_corrupt_cache_file_is_ignored(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text("{not json", encoding="utf-8")
        assert len(cache_mod.WordCache(path)) == 0


# ---------------------------------------------------------------------------
# HTTP classification
# ---------------------------------------------------------------------------

class TestClassifyHttpResult:
    def test_plain_text_auth_error_becomes_a_key_problem(self):
        from lookup.http import HttpResult

        result = HttpResult(
            ok=True,
            status=200,
            body="Invalid API key. Not subscribed for this reference.",
        )
        assert classify_http_result(result).code == CODE_INVALID_API_KEY

    def test_missing_key_message(self):
        from lookup.http import HttpResult

        result = HttpResult(ok=True, status=200, body="Key is required.")
        assert classify_http_result(result).code == CODE_MISSING_API_KEY

    def test_404_is_not_found(self):
        from lookup.http import HttpResult

        result = HttpResult(ok=True, status=404, body={"code": "x"})
        assert classify_http_result(result).code == CODE_WORD_NOT_FOUND

    def test_empty_body_is_not_found(self):
        from lookup.http import HttpResult

        result = HttpResult(ok=True, status=200, body=[])
        assert classify_http_result(result).code == CODE_WORD_NOT_FOUND

    def test_failed_result_passes_its_code_through(self):
        from lookup.http import HttpResult

        result = HttpResult(ok=False, code=CODE_RATE_LIMITED, detail="HTTP 429")
        outcome = classify_http_result(result)
        assert outcome.code == CODE_RATE_LIMITED

    def test_random_text_is_a_bad_response(self):
        from lookup.http import HttpResult

        result = HttpResult(ok=True, status=200, body="<html>oops</html>")
        assert classify_http_result(result).code == CODE_BAD_RESPONSE


# ---------------------------------------------------------------------------
# MW markup cleaning
# ---------------------------------------------------------------------------

class TestCleanText:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("plain text", "plain text"),
            ("{it}italic{/it}", "italic"),
            ("{b}bold{/b}", "bold"),
            ("one{bc}two", "one: two"),
            ("{a_link|backward}", "backward"),
            ("{d_link|text|target}", "text"),
            ("{sx|say||1a}", "say 1a"),
            ("{ma}{mat|fly|}{/ma}", "more at fly"),
            ("{ldquo}x{rdquo}", "\u201cx\u201d"),
            ("before 12th century{ds|t|1|a|1}", "before 12th century"),
            ("{dx}{d_link|abide|abide:1}{/dx}", "\u2014 abide"),
        ],
    )
    def test_tokens_are_resolved(self, raw, expected):
        assert clean_text(raw) == expected

    def test_nested_lists_are_flattened(self):
        assert clean_text([["a ", "b"], " c"]) == "a b c"


# ---------------------------------------------------------------------------
# MW data cleaning
# ---------------------------------------------------------------------------

SAMPLE_MW_ENTRY = {
    "meta": {
        "id": "director:1",
        "stems": ["director", "directors"],
        "lang": "en",
        "src": "collegiate",
        "section": "alpha",
        "offensive": False,
    },
    "hwi": {
        "hw": "di*rec*tor",
        "prs": [{"mw": "d\u0259-\u02c8rek-t\u0259r", "sound": {"audio": "direct12"}}],
    },
    "fl": "noun",
    "lbs": ["often attributive"],
    "def": [
        {
            "sseq": [
                [
                    [
                        "sense",
                        {
                            "dt": [
                                ["text", "the head of an organized group"],
                                ["vis", [{"t": "director of religious education"}]],
                            ]
                        },
                    ]
                ]
            ]
        }
    ],
    "shortdef": ["one who directs"],
    "et": [["text", "see {a_link|direct}"]],
    "date": "15th century",
}


class TestMwData:
    def test_maps_every_standard_key(self):
        from lookup import mw_data

        cleaned = mw_data.clean_entry(SAMPLE_MW_ENTRY, word="director")
        assert set(cleaned.keys()) == set(config.STANDARD_ENTRY_KEYS)

    def test_headword_syllable_marks_are_removed(self):
        from lookup import mw_data

        cleaned = mw_data.clean_entry(SAMPLE_MW_ENTRY, word="director")
        assert cleaned["word"] == "director"

    def test_pronunciation_and_audio_url(self):
        from lookup import mw_data

        cleaned = mw_data.clean_entry(SAMPLE_MW_ENTRY, word="director")
        first = cleaned["pronunciations"][0]
        assert first["mw"] == "d\u0259-\u02c8rek-t\u0259r"
        assert first["audio"].endswith("/d/direct12.mp3")

    def test_senses_and_examples(self):
        from lookup import mw_data

        cleaned = mw_data.clean_entry(SAMPLE_MW_ENTRY, word="director")
        assert cleaned["senses"][0]["definition"] == "the head of an organized group"
        assert cleaned["senses"][0]["examples"] == ["director of religious education"]
        assert cleaned["senses"][0]["pos"] == "noun"

    def test_leading_sense_colon_is_dropped(self):
        """MW prefixes a sense body with ``{bc}``; the schema stores it bare."""
        from lookup import mw_data

        entry = {
            "hwi": {"hw": "di*rec*tor"},
            "fl": "noun",
            "def": [
                {
                    "sseq": [
                        [
                            ["sense", {"dt": [["text", "{bc}the head of an organized group"]]}],
                            ["sense", {"dt": [["text", "{bc}one who directs{bc}such as"]]}],
                        ]
                    ]
                }
            ],
        }
        senses = mw_data.clean_entry(entry, word="director")["senses"]
        assert senses[0]["definition"] == "the head of an organized group"
        # An interior colon is part of the wording and must survive.
        assert senses[1]["definition"] == "one who directs: such as"

    def test_etymology_and_first_use(self):
        from lookup import mw_data

        cleaned = mw_data.clean_entry(SAMPLE_MW_ENTRY, word="director")
        assert cleaned["etymology"] == "see direct"
        assert cleaned["firstUse"] == "15th century"

    def test_empty_entry_still_has_every_key(self):
        from lookup import mw_data

        cleaned = mw_data.clean_entry({}, word="ghost")
        assert set(cleaned.keys()) == set(config.STANDARD_ENTRY_KEYS)
        assert cleaned["word"] == "ghost"

    def test_suggestion_list_is_recognised(self):
        from lookup import mw_data

        assert mw_data.looks_like_suggestions(["dog", "dot"])
        assert not mw_data.looks_like_suggestions([{"hwi": {}}])


# ---------------------------------------------------------------------------
# lookup_entries: dict_slug stamping
# ---------------------------------------------------------------------------

class TestLookupEntries:
    def test_every_entry_is_stamped_with_the_active_slug(
        self, stub_session, stub_response
    ):
        """Downstream writers must not depend on write_json to set ``dict``."""
        from lookup import lookup_entries

        session = stub_session([stub_response([SAMPLE_MW_ENTRY])])
        entries = [
            BoldEntry(word="director", type="word", line=1),
            BoldEntry(word="hung up", type="phrase", line=2),
        ]
        resolved = lookup_entries(
            entries, session=session, cache=None, api_key="k", delay=0
        )
        assert [e.dict_slug for e in resolved] == ["mw", "mw"]


# ---------------------------------------------------------------------------
# MW API client
# ---------------------------------------------------------------------------

class TestMwApi:
    def test_missing_key_short_circuits(self):
        outcome, _ = mw_api.lookup_word("director", api_key="", session=None)
        assert outcome.code == CODE_MISSING_API_KEY

    def test_invalid_query_is_rejected_without_a_request(self, stub_session):
        session = stub_session()
        outcome, _ = mw_api.lookup_word("x" * 100, api_key="k", session=session)
        assert outcome.code != CODE_OK
        assert session.calls == []

    def test_successful_lookup(self, stub_session, stub_response, tmp_path):
        session = stub_session([stub_response([SAMPLE_MW_ENTRY])])
        outcome, cleaned = mw_api.lookup_word(
            "director",
            api_key="k",
            session=session,
            cache=cache_mod.WordCache(tmp_path / "c.json"),
            delay=0,
        )
        assert outcome.code == CODE_OK
        assert cleaned["word"] == "director"
        assert session.calls[0]["params"]["key"] == "k"

    def test_suggestions_mean_not_found(self, stub_session, stub_response, tmp_path):
        session = stub_session([stub_response(["dog", "dot"])])
        outcome, _ = mw_api.lookup_word(
            "dgo", api_key="k", session=session, cache=None, delay=0
        )
        assert outcome.code == CODE_WORD_NOT_FOUND

    def test_network_error_becomes_a_failure_not_an_exception(
        self, stub_session, tmp_path
    ):
        session = stub_session(error=requests.exceptions.ConnectionError("boom"))
        outcome, _ = mw_api.lookup_word(
            "director",
            api_key="k",
            session=session,
            cache=None,
            retry=0,
            delay=0,
        )
        assert outcome.code == CODE_LOOKUP_FAILED

    def test_cache_hit_skips_the_network(self, stub_session, stub_response, tmp_path):
        path = tmp_path / "c.json"
        warm = cache_mod.WordCache(path)
        warm.put_raw("director", CODE_OK, [SAMPLE_MW_ENTRY])
        warm.save()

        session = stub_session()  # no queued responses: any call would fail
        outcome, _ = mw_api.lookup_word(
            "director",
            api_key="k",
            session=session,
            cache=cache_mod.WordCache(path),
            delay=0,
        )
        assert outcome.code == CODE_OK
        assert session.calls == []


# ---------------------------------------------------------------------------
# write_json
# ---------------------------------------------------------------------------

class TestWriteJson:
    def _entry(self, word="director", code=CODE_OK, type_="word"):
        return BoldEntry(
            word=word,
            type=type_,
            sentence=f"a {word} here",
            line=1,
            code=code,
            source="api",
            entry={"word": word},
        )

    def test_success_goes_to_words_and_failure_to_errors(self, tmp_path):
        words = tmp_path / "w.json"
        errors = tmp_path / "e.json"

        summary = write_json.write_documents(
            [
                self._entry("director", CODE_OK, "word"),
                self._entry("obfuscate", CODE_WORD_NOT_FOUND, "word"),
                self._entry("give up", 1001, "phrase"),
            ],
            words_path=words,
            errors_path=errors,
            dict_slug="mw",
        )

        written = json.loads(words.read_text(encoding="utf-8"))
        assert len(written) == 1
        assert written[0]["dict"] == "mw"

        document = json.loads(errors.read_text(encoding="utf-8"))
        assert document["total"] == 2
        assert summary["total"] == 2

    def test_counters_are_recomputed(self, tmp_path):
        errors = tmp_path / "e.json"
        write_json.write_documents(
            [
                self._entry("a", CODE_RATE_LIMITED, "word"),
                self._entry("b", CODE_MISSING_API_KEY, "word"),
                self._entry("c", CODE_WORD_NOT_FOUND, "word"),
            ],
            words_path=tmp_path / "w.json",
            errors_path=errors,
            dict_slug="mw",
        )
        document = json.loads(errors.read_text(encoding="utf-8"))
        assert document["retriable"] == 1
        assert document["dict_api_key_problems"] == 1
        assert document["total"] == 3

    def test_errors_are_deduplicated_on_word_and_type(self, tmp_path):
        errors = tmp_path / "e.json"
        for _ in range(2):
            write_json.write_documents(
                [self._entry("obfuscate", CODE_WORD_NOT_FOUND, "word")],
                words_path=tmp_path / "w.json",
                errors_path=errors,
                dict_slug="mw",
            )
        document = json.loads(errors.read_text(encoding="utf-8"))
        assert document["total"] == 1

    def test_success_marks_a_pending_record_filled(self, tmp_path):
        words = tmp_path / "w.json"
        errors = tmp_path / "e.json"

        write_json.write_documents(
            [self._entry("give up", 1001, "phrase")],
            words_path=words,
            errors_path=errors,
            dict_slug="mw",
        )
        write_json.write_documents(
            [self._entry("give up", CODE_OK, "phrase")],
            words_path=words,
            errors_path=errors,
            dict_slug="mw",
            llm_source=True,
        )

        document = json.loads(errors.read_text(encoding="utf-8"))
        record = next(r for r in document["errors"] if r["word"] == "give up")
        assert record["status"] == "filled"

    def test_error_records_carry_id_and_timestamp(self, tmp_path):
        errors = tmp_path / "e.json"
        write_json.write_documents(
            [self._entry("ghost", CODE_WORD_NOT_FOUND, "word")],
            words_path=tmp_path / "w.json",
            errors_path=errors,
            dict_slug="mw",
        )
        record = json.loads(errors.read_text(encoding="utf-8"))["errors"][0]
        assert record["id"]
        assert record["reason"] == "WORD_NOT_FOUND"
        assert record["status"] == "pending"
        assert "T" in record["time"]
