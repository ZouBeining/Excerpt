"""Tests for the shared configuration helpers."""

from __future__ import annotations

import pytest

from common.config import abbreviate_pos, tag_label


class TestAbbreviatePos:
    """The abbreviation is a display concern: the stored ``pos`` is untouched."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("noun", "n."),
            ("verb", "v."),
            ("adjective", "adj."),
            ("adverb", "adv."),
            ("pronoun", "pron."),
            ("preposition", "prep."),
            ("conjunction", "conj."),
            ("interjection", "interj."),
            ("abbreviation", "abbr."),
            ("phrase", "phr."),
            ("transitive verb", "v.t."),
            ("intransitive verb", "v.i."),
            ("phrasal verb", "phr. v."),
            ("plural noun", "n. pl."),
        ],
    )
    def test_single_labels(self, raw, expected):
        assert abbreviate_pos(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("phrasal verb / idiom", "phr. v. / idiom"),
            ("conjunction phrase", "conj. phr."),
            ("adjective phrase / phrasal adjective", "adj. phr. / phrasal adj."),
            ("verb (past continuous)", "v. (past continuous)"),
            ("idiomatic expression", "idiomatic expression"),
        ],
    )
    def test_compound_labels(self, raw, expected):
        assert abbreviate_pos(raw) == expected

    @pytest.mark.parametrize("raw", ["idiom", "phrasal", "verbatim", "nounish"])
    def test_unknown_text_is_left_alone(self, raw):
        assert abbreviate_pos(raw) == raw

    def test_matching_ignores_case(self):
        assert abbreviate_pos("Noun") == "n."

    def test_noun_is_not_read_inside_pronoun(self):
        assert abbreviate_pos("pronoun") == "pron."

    def test_verb_is_not_read_inside_verbatim(self):
        assert abbreviate_pos("verbatim") == "verbatim"

    @pytest.mark.parametrize("raw", ["", None])
    def test_empty_input(self, raw):
        assert abbreviate_pos(raw) == ""


class TestTagLabel:
    """Abbreviation and colon must never meet: ``Syn.`` never becomes ``Syn.:``."""

    def test_abbreviation_takes_no_colon(self):
        assert tag_label("Syn.") == "Syn."
        assert tag_label("Anton.") == "Anton."
        assert tag_label("E.g.") == "E.g."

    def test_full_word_takes_the_colon(self):
        assert tag_label("Etymology") == "Etymology:"
        assert tag_label("First Use") == "First Use:"
        assert tag_label("Inflections") == "Inflections:"

    def test_the_separator_is_configurable(self):
        assert tag_label("Etymology", "\uff1a") == "Etymology\uff1a"

    def test_abbreviation_uses_the_abbreviation_separator(self):
        assert tag_label("E.g.", "\uff1a", abbreviation_separator=" ") == "E.g. "

    def test_surrounding_whitespace_is_dropped(self):
        assert tag_label("  Syn.  ") == "Syn."
        assert tag_label("  Etymology  ") == "Etymology:"

    @pytest.mark.parametrize("raw", ["", None, "   "])
    def test_empty_input_stays_empty(self, raw):
        assert tag_label(raw) == ""
