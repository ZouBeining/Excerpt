"""Tests for the LLM completion cache.

Offline by construction: the cache is pure file IO plus a hash, so nothing
here touches the network or the real ``~/.cache``.
"""

from __future__ import annotations

import json

import pytest

from excerpt import llm_cache


class TestSanitizeModel:
    def test_slash_and_colon_become_underscores(self):
        # The shape an aggregator hands out, and the one Windows refuses as a
        # filename because ':' opens an alternate data stream.
        assert llm_cache.sanitize_model("vendor/model:free") == "vendor_model_free"

    def test_every_forbidden_character_is_replaced(self):
        assert llm_cache.sanitize_model('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"

    def test_control_characters_are_replaced(self):
        assert llm_cache.sanitize_model("a\x00b\x1fc") == "a_b_c"

    def test_a_plain_name_is_untouched(self):
        assert llm_cache.sanitize_model("gpt-4o-mini") == "gpt-4o-mini"

    def test_surrounding_whitespace_is_stripped(self):
        assert llm_cache.sanitize_model("  model  ") == "model"

    def test_an_empty_name_falls_back_to_default(self):
        assert llm_cache.sanitize_model("") == "default"
        assert llm_cache.sanitize_model("   ") == "default"

    def test_a_name_of_only_unsafe_characters_yields_underscores(self):
        # Still a legal filename, so there is nothing to fall back from.
        assert llm_cache.sanitize_model("///") == "___"

    def test_a_blank_name_falls_back_rather_than_vanishing(self):
        assert llm_cache.sanitize_model("\x00") == "_"


class TestCacheKey:
    def test_identical_inputs_give_identical_keys(self):
        assert llm_cache.cache_key("Return your call", "phrase") == (
            llm_cache.cache_key("Return your call", "phrase")
        )

    def test_the_word_changes_the_key(self):
        assert llm_cache.cache_key("alpha", "phrase") != (
            llm_cache.cache_key("beta", "phrase")
        )

    def test_the_type_changes_the_key(self):
        assert llm_cache.cache_key("alpha", "phrase") != (
            llm_cache.cache_key("alpha", "sentence")
        )

    def test_the_prompt_version_changes_the_key(self):
        assert llm_cache.cache_key("alpha", "phrase", prompt_version=1) != (
            llm_cache.cache_key("alpha", "phrase", prompt_version=2)
        )

    def test_surrounding_whitespace_is_ignored(self):
        assert llm_cache.cache_key("  alpha  ", "phrase") == (
            llm_cache.cache_key("alpha", "phrase")
        )

    def test_case_is_significant(self):
        # "Alpha" and "alpha" are genuinely different inputs; folding them
        # together would serve one record the other's answer.
        assert llm_cache.cache_key("Alpha", "phrase") != (
            llm_cache.cache_key("alpha", "phrase")
        )

    def test_the_key_is_a_hex_digest(self):
        key = llm_cache.cache_key("alpha", "phrase")
        assert len(key) == 64
        assert all(ch in "0123456789abcdef" for ch in key)


class TestLlmCache:
    @pytest.fixture
    def cache(self, tmp_path):
        return llm_cache.LlmCache("test-model", tmp_path / "c.json")

    def test_put_then_get_round_trips(self, cache):
        payload = {"definition": "a thing", "examples": ["x"]}
        cache.put("k", payload)
        assert cache.get("k") == payload

    def test_a_miss_returns_none_and_counts(self, cache):
        assert cache.get("nope") is None
        assert cache.misses == 1
        assert cache.hits == 0

    def test_a_hit_counts(self, cache):
        cache.put("k", {"a": 1})
        cache.get("k")
        assert cache.hits == 1
        assert cache.misses == 0

    def test_get_returns_a_copy(self, cache):
        cache.put("k", {"nested": {"a": 1}})
        first = cache.get("k")
        first["nested"]["a"] = 999
        # The stored record must be untouched, and so must the next read.
        assert cache.get("k")["nested"]["a"] == 1

    def test_the_path_is_per_model(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EXCERPT_CACHE_DIR", str(tmp_path))
        one = llm_cache.LlmCache("vendor/model:free")
        two = llm_cache.LlmCache("other")
        assert one.path.name == "vendor_model_free.cache.json"
        assert one.path != two.path

    def test_an_explicit_path_wins(self, tmp_path):
        custom = tmp_path / "deep" / "llm.json"
        cache = llm_cache.LlmCache("m", custom)
        assert cache.path == custom

    def test_save_is_atomic_and_well_formed(self, cache):
        cache.put("k", {"a": 1})
        cache.save()
        assert cache.path.is_file()
        raw = json.loads(cache.path.read_text(encoding="utf-8"))
        assert raw["version"] == llm_cache.CACHE_VERSION
        assert raw["model"] == "test-model"
        assert raw["entries"]["k"]["payload"] == {"a": 1}
        # No stray temp files are left behind by the os.replace dance.
        assert list(cache.path.parent.glob("*.tmp")) == []

    def test_save_is_skipped_when_not_dirty(self, tmp_path):
        cache = llm_cache.LlmCache("m", tmp_path / "c.json")
        cache.save()
        assert not cache.path.exists()

    def test_a_second_instance_reads_what_was_saved(self, tmp_path):
        path = tmp_path / "c.json"
        first = llm_cache.LlmCache("m", path)
        first.put("k", {"a": 1})
        first.save()

        second = llm_cache.LlmCache("m", path)
        assert second.get("k") == {"a": 1}

    def test_a_corrupt_file_is_ignored(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text("{not json", encoding="utf-8")
        cache = llm_cache.LlmCache("m", path)
        assert len(cache) == 0
        assert cache.get("k") is None

    def test_a_version_mismatch_is_discarded(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text(
            json.dumps({"version": 999, "model": "m", "entries": {"k": {}}}),
            encoding="utf-8",
        )
        cache = llm_cache.LlmCache("m", path)
        assert len(cache) == 0

    def test_disabled_cache_never_reads_or_writes(self, tmp_path):
        path = tmp_path / "c.json"
        cache = llm_cache.LlmCache("m", path, enabled=False)
        cache.put("k", {"a": 1})
        cache.save()
        assert not path.exists()
        assert cache.get("k") is None
        assert cache.misses == 1

    def test_contains_and_len(self, cache):
        assert len(cache) == 0
        assert "k" not in cache
        cache.put("k", {"a": 1})
        assert len(cache) == 1
        assert "k" in cache

    def test_putting_an_identical_payload_does_not_mark_dirty(self, cache):
        cache.put("k", {"a": 1})
        cache.save()
        assert not cache._dirty
        cache.put("k", {"a": 1})
        assert not cache._dirty
