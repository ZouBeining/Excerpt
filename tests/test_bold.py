"""Tests for the shared bold-span helpers.

The regression these cover: ``extractor.find_sentence`` keeps the ``**`` markers
of every bold in a sentence, so a sentence holding two bolded records used to be
rendered with both spans marked in each of the two records.
"""

from __future__ import annotations

import pytest

from excerpt.bold import (
    normalize_bold_target,
    render_markdown_sentence,
    split_bold,
    target_bold_segments,
)

#: A real sentence from the sample text: it bolds two different records.
TWO_BOLDS = 'And when she **hung up**, I was like, "**what\'s up**, what did he say?'


class TestSplitBold:
    def test_markers_are_stripped_and_flagged(self):
        assert split_bold("a **word** here") == [
            ("a ", False),
            ("word", True),
            (" here", False),
        ]

    def test_underscore_markers_are_recognised(self):
        assert split_bold("an __underscored__ word") == [
            ("an ", False),
            ("underscored", True),
            (" word", False),
        ]

    def test_sentence_without_markers_is_one_plain_chunk(self):
        assert split_bold("a plain sentence.") == [("a plain sentence.", False)]

    def test_empty_sentence_yields_nothing(self):
        assert split_bold("") == []


class TestTargetBoldSegments:
    def test_only_the_matching_span_is_flagged(self):
        parts, had_markers = target_bold_segments(TWO_BOLDS, ("hung up",))
        assert had_markers
        assert [(chunk, flag) for chunk, flag in parts if flag] == [("hung up", True)]

    def test_the_other_record_marks_its_own_span(self):
        parts, _ = target_bold_segments(TWO_BOLDS, ("what's up",))
        assert [chunk for chunk, flag in parts if flag] == ["what's up"]

    def test_matching_is_case_insensitive(self):
        parts, _ = target_bold_segments("a **Word** here", ("word",))
        assert [chunk for chunk, flag in parts if flag] == ["Word"]

    def test_lemma_from_matches_the_inflected_bold(self):
        parts, _ = target_bold_segments("He was **deformed** by it.", ("deform", "deformed"))
        assert [chunk for chunk, flag in parts if flag] == ["deformed"]

    def test_a_single_unmatched_bold_is_still_marked(self):
        parts, _ = target_bold_segments("a **Word** here", ("gamma",))
        assert [chunk for chunk, flag in parts if flag] == ["Word"]

    def test_several_unmatched_bolds_mark_none(self):
        parts, _ = target_bold_segments("**alpha** and **beta**", ("gamma",))
        assert [flag for _, flag in parts] == [False, False, False]

    def test_a_sentence_without_markers_reports_no_markers(self):
        parts, had_markers = target_bold_segments("a plain sentence.", ("plain",))
        assert had_markers is False
        assert parts == [("a plain sentence.", False)]


class TestRenderMarkdownSentence:
    def test_only_this_entry_stays_bold(self):
        rendered = render_markdown_sentence(TWO_BOLDS, ("hung up",))
        assert "**hung up**" in rendered
        assert "**what's up**" not in rendered
        assert "what's up" in rendered

    def test_underscore_bold_is_normalised_to_asterisks(self):
        assert render_markdown_sentence("an __underscored__ word", ("underscored",)) == (
            "an **underscored** word"
        )

    def test_sentence_without_markers_is_returned_verbatim(self):
        assert render_markdown_sentence("a plain sentence.", ("plain",)) == (
            "a plain sentence."
        )

    def test_empty_sentence(self):
        assert render_markdown_sentence("", ("word",)) == ""


class TestNormalizeBoldTarget:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  Director  ", "director"),
            ("'em", "em"),
            ("-duty-", "duty"),
            (None, ""),
        ],
    )
    def test_normalises_like_the_extractor(self, raw, expected):
        assert normalize_bold_target(raw) == expected
