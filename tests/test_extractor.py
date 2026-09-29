"""Tests for the bold-record extractor."""

from __future__ import annotations

from excerpt.extractor import (
    classify,
    extract_bold_entries,
    is_single_word,
    normalize_word_key,
)
from common.config import CODE_NOT_A_SINGLE_WORD, CODE_OK


class TestSingleWordRule:
    """The word test follows the regex and rules in AGENTS.md."""

    def test_accepts_plain_words(self):
        assert is_single_word("director")
        assert is_single_word("Pause")

    def test_accepts_apostrophes_and_hyphens(self):
        assert is_single_word("what's")
        assert is_single_word("heavy-duty")
        assert is_single_word("banged-up")

    def test_rejects_single_letters(self):
        assert not is_single_word("a")
        assert not is_single_word("I")

    def test_rejects_digits_and_punctuation(self):
        assert not is_single_word("give up")
        assert not is_single_word("Pause(停顿)")
        assert not is_single_word("nightmare(噩梦)!!")
        assert not is_single_word("crack up(大笑)")
        assert not is_single_word("Goody Two-Shoes")

    def test_rejects_multi_word_phrases(self):
        assert not is_single_word("returning your call")
        assert not is_single_word("think so highly of")


class TestClassify:
    """``word`` / ``phrase`` / ``sentence`` are decided as specified."""

    def test_word(self):
        assert classify("director") == "word"
        assert classify("hung") == "word"

    def test_phrase(self):
        assert classify("returning your call") == "phrase"
        assert classify("think so highly of") == "phrase"

    def test_sentence_when_punctuation_present(self):
        assert classify("what's up, what did he say?") == "sentence"
        assert classify("So I jumped up kind of suddenly.") == "sentence"

    def test_non_ascii_parenthetical_is_a_sentence(self):
        # A Chinese gloss in parentheses is punctuation, so not a bare phrase.
        assert classify("Pause(停顿)") == "sentence"


class TestNormalizeWordKey:
    def test_lowercases_and_strips_quotes(self):
        assert normalize_word_key("  Director  ") == "director"
        assert normalize_word_key("'em") == "em"
        assert normalize_word_key("-duty-") == "duty"


class TestExtraction:
    def test_extracts_from_sample_document(self, test_markdown):
        entries = extract_bold_entries(test_markdown)
        assert len(entries) > 40
        words = {entry.word for entry in entries}
        assert "director" in words
        assert "returning your call" in words

    def test_words_get_ok_code_and_phrases_get_error_code(self, test_markdown):
        entries = extract_bold_entries(test_markdown)
        for entry in entries:
            if entry.type == "word":
                assert entry.code == CODE_OK
            else:
                assert entry.code == CODE_NOT_A_SINGLE_WORD

    def test_deduplication_keeps_first_occurrence(self):
        text = "First **Deformed** here.\n\nLater **deformed** again."
        entries = extract_bold_entries(text)
        matches = [e for e in entries if e.type == "word"]
        assert len(matches) == 1
        # The first occurrence supplies the casing.
        assert matches[0].word == "Deformed"

    def test_records_line_numbers(self):
        text = "line one\n\nbold **word** here\n"
        entries = extract_bold_entries(text)
        assert entries[0].line == 3

    def test_captures_the_enclosing_sentence(self):
        text = "He is the **director**. And then more text follows."
        entries = extract_bold_entries(text)
        assert "director" in entries[0].sentence
        assert entries[0].sentence.endswith(".")

    def test_ignores_code_fences(self):
        text = "```\n**notbold** here\n```\n\nreal **bold** here\n"
        entries = extract_bold_entries(text)
        assert [e.word for e in entries] == ["bold"]

    def test_ignores_escaped_markers(self):
        text = r"an escaped \*\*notbold\*\* marker and **real** here"
        entries = extract_bold_entries(text)
        assert [e.word for e in entries] == ["real"]

    def test_supports_underscore_bold(self):
        text = "an __underscored__ word"
        entries = extract_bold_entries(text)
        assert [e.word for e in entries] == ["underscored"]

    def test_empty_input(self):
        assert extract_bold_entries("") == []
