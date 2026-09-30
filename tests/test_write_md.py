"""Tests for the Markdown renderers."""

from __future__ import annotations

import copy

from common.config import (
    CODE_NOT_A_SINGLE_WORD,
    CODE_OK,
    SOURCE_API,
    SOURCE_CACHE,
    SOURCE_LLM,
    BoldEntry,
    artifacts,
)
from excerpt import write_md


def make_entry(
    word="director",
    type_="word",
    code=CODE_OK,
    source=SOURCE_API,
    entry=None,
    is_lemma=False,
    lemma_from="",
):
    return BoldEntry(
        word=word,
        type=type_,
        sentence=f"Tushman, the middle-school {word}.",
        line=8,
        code=code,
        source=source,
        dict_slug="mw",
        entry=entry if entry is not None else {},
        is_lemma=is_lemma,
        lemma_from=lemma_from,
    )


DEFINED_ENTRY = {
    "word": "director",
    "pos": "noun",
    "pronunciations": [{"mw": "d\u0259-\u02c8rek-t\u0259r", "ipa": ""}],
    "senses": [
        {
            "pos": "noun",
            "definition": "the head of an organized group",
            "examples": ["director of religious education"],
        }
    ],
    "synonyms": ["manager"],
    "antonyms": [],
    "etymology": "see direct",
    "firstUse": "15th century",
}


class TestNoteHeader:
    def test_header_counts_and_dictionary_name(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        entries = [make_entry(entry=DEFINED_ENTRY)]
        index = {"title": "test", "source": "test.md", "items": [{"word": "director"}]}

        text = write_md.render_note_md(entries, index, art, source_name="test.md")
        assert text.startswith("# test")
        assert "valid_entries\uff1a1  /  1 bolded records" in text
        assert "Merriam-Webster" in text
        assert "cache_hit\uff1a0" in text

    def test_cache_hits_are_counted(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        entries = [make_entry(source=SOURCE_CACHE), make_entry(source=SOURCE_API)]
        text = write_md.render_note_md(entries, {"items": []}, art)
        assert "cache_hit\uff1a1" in text

    def test_lemmatized_pairs_are_listed(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        entries = [
            make_entry("deformed", is_lemma=True, lemma_from="deform"),
            make_entry("deform"),
        ]
        text = write_md.render_note_md(entries, {"items": []}, art)
        assert "deform\u2192deformed" in text


class TestEntryRendering:
    def test_sections_are_rendered(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        entries = [make_entry(entry=DEFINED_ENTRY)]
        text = write_md.render_note_md(entries, {"items": []}, art)

        assert "### 1. director" in text
        assert "> Tushman, the middle-school director." in text
        assert "- type: word" in text
        assert "- line: 8" in text
        assert "- source: api" in text
        assert "**Definition**" in text
        assert "the head of an organized group" in text
        assert "director of religious education" in text
        assert "**Etymology**" in text
        assert "see direct" in text
        assert "**First Use**" in text
        assert "15th century" in text
        assert "**Syn.**" in text
        assert "manager" in text

    def test_pronunciation_is_rendered(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        text = write_md.render_note_md(
            [make_entry(entry=DEFINED_ENTRY)], {"items": []}, art
        )
        assert "/d\u0259-\u02c8rek-t\u0259r/" in text

    def test_entry_without_a_definition_still_renders(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        entries = [make_entry("give up", "phrase", CODE_NOT_A_SINGLE_WORD)]
        text = write_md.render_note_md(entries, {"items": []}, art)
        assert "### 1. give up" in text

    def test_llm_entries_are_marked(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        entries = [make_entry("give up", "phrase", CODE_OK, SOURCE_LLM)]
        text = write_md.render_note_md(entries, {"items": []}, art)
        assert "- source: llm" in text


class TestIndexRendering:
    def test_index_is_a_table(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        entries = [make_entry(entry=DEFINED_ENTRY)]
        text = write_md.render_index_md(entries, {"items": []}, art)
        assert "| # | word | type | sentence | line |" in text
        assert "| 1 | director | word |" in text
        assert "/d\u0259-\u02c8rek-t\u0259r/" in text

    def test_missing_pronunciation_uses_a_dash(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        entries = [make_entry("give up", "phrase", CODE_NOT_A_SINGLE_WORD)]
        text = write_md.render_index_md(entries, {"items": []}, art)
        assert "\u2014" in text


class TestSentenceMarking:
    """A sentence may bold several records; only this one may be marked."""

    #: A real sentence from the sample text, bolding two different records.
    TWO_BOLDS = 'And when she **hung up**, I was like, "**what\'s up**, what did he say?'

    def _entry(self, word="hung up", type_="phrase", **overrides):
        entry = make_entry(word, type_, CODE_OK, **overrides)
        entry.sentence = self.TWO_BOLDS
        return entry

    def test_note_body_marks_only_this_entry(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        text = write_md.render_note_md([self._entry()], {"items": []}, art)
        assert "> And when she **hung up**, I was like, \"what's up, what did he say?" in text
        assert "**what's up**" not in text

    def test_index_table_marks_only_this_entry(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        text = write_md.render_index_md([self._entry()], {"items": []}, art)
        assert "**hung up**" in text
        assert "**what's up**" not in text

    def test_lemma_entry_marks_the_inflected_form(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        entry = self._entry(word="deform", type_="word", is_lemma=True, lemma_from="deformed")
        entry.sentence = "He was **deformed** by it."
        text = write_md.render_note_md([entry], {"items": []}, art)
        assert "> He was **deformed** by it." in text

    def test_sentence_without_markers_is_unchanged(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        text = write_md.render_note_md([make_entry(entry=DEFINED_ENTRY)], {"items": []}, art)
        assert "> Tushman, the middle-school director." in text


class TestPosAbbreviation:
    """Only the note body abbreviates the part of speech."""

    def test_note_body_abbreviates_the_sense_pos(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        text = write_md.render_note_md([make_entry(entry=DEFINED_ENTRY)], {"items": []}, art)
        assert "- *n.* the head of an organized group" in text

    def test_note_body_abbreviates_compound_labels(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        payload = copy.deepcopy(DEFINED_ENTRY)
        payload["senses"][0]["pos"] = "phrasal verb / idiom"
        entry = make_entry("give up", "phrase", CODE_OK, SOURCE_LLM, payload)
        text = write_md.render_note_md([entry], {"items": []}, art)
        assert "- *phr. v. / idiom* " in text

    def test_index_table_keeps_the_full_label(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        text = write_md.render_index_md([make_entry(entry=DEFINED_ENTRY)], {"items": []}, art)
        assert "| noun |" in text
        assert "| n. |" not in text


class TestWriteMd:
    def test_both_files_are_written(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        paths = write_md.write_md([make_entry(entry=DEFINED_ENTRY)], art, source_name="test.md")
        from pathlib import Path

        assert Path(paths["note_md"]).is_file()
        assert Path(paths["index_md"]).is_file()
        assert Path(paths["note_md"]).name == "test.mw.md"

    def test_pipe_in_sentence_is_escaped(self, tmp_path):
        art = artifacts("test", "MW", str(tmp_path))
        entry = make_entry(entry=DEFINED_ENTRY)
        entry.sentence = "a | b"
        text = write_md.render_index_md([entry], {"items": []}, art)
        assert "a \\| b" in text
