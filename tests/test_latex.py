"""Tests for the LaTeX renderer and the ``excerpt-latex`` entry point."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.config import artifacts
from excerpt import latex


class TestEscaping:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("plain", "plain"),
            ("a_b", r"a\_b"),
            ("50%", r"50\%"),
            ("a&b", r"a\&b"),
            ("$5", r"\$5"),
            ("#1", r"\#1"),
            ("{x}", r"\{x\}"),
            ("C:\\path", r"C:\textbackslash{}path"),
            ("~home", r"\textasciitilde{}home"),
            ("x^2", r"x\textasciicircum{}2"),
        ],
    )
    def test_special_characters(self, raw, expected):
        assert latex.escape_latex(raw) == expected

    def test_smart_quotes_are_replaced(self):
        assert latex.escape_latex("\u201cquoted\u201d") == "``quoted''"
        assert latex.escape_latex("\u2018a\u2019") == "`a'"

    def test_dashes(self):
        assert latex.escape_latex("a\u2013b") == "a--b"
        assert latex.escape_latex("a\u2014b") == "a---b"

    def test_unicode_is_preserved_for_xelatex(self):
        assert latex.escape_latex("d\u0259-\u02c8rek-t\u0259r") == "d\u0259-\u02c8rek-t\u0259r"
        assert latex.escape_latex("\u505c\u987f") == "\u505c\u987f"

    def test_lists_are_joined(self):
        assert latex.escape_latex(["a_b", "c"]) == r"a\_b, c"

    def test_none_is_empty(self):
        assert latex.escape_latex(None) == ""


class TestSentence:
    def test_bold_span_is_marked(self):
        assert (
            latex.render_sentence("a **word** here", "word")
            == r"a \wmark{word} here"
        )

    def test_underscore_bold_span_is_marked(self):
        rendered = latex.render_sentence("a __word__ here", "word")
        assert r"\wmark{word}" in rendered

    def test_plain_sentence_without_markers_still_marks_the_word(self):
        rendered = latex.render_sentence("a word here", "word")
        assert rendered == r"a \wmark{word} here"

    def test_special_characters_inside_are_escaped(self):
        rendered = latex.render_sentence("pay **50%** now", "50%")
        assert r"\wmark{50\%}" in rendered

    def test_empty_sentence_falls_back_to_the_word(self):
        assert latex.render_sentence("", "word") == "word"


class TestEntryBlocks:
    def _entry(self, **overrides):
        base = {
            "word": "director",
            "sentence": "the middle-school **director**.",
            "entry": {
                "senses": [
                    {
                        "pos": "noun",
                        "definition": "the head of a group",
                        "examples": ["a communications director"],
                    }
                ],
                "synonyms": ["manager"],
                "etymology": "see direct",
                "firstUse": "15th century",
            },
        }
        base.update(overrides)
        return base

    def test_full_block_uses_preamble_commands(self):
        lines = latex._render_entry(self._entry(), 1)
        text = "\n".join(lines)
        assert r"\whead{director}" in text
        assert r"\wsent{" in text
        assert r"\wmark{director}" in text
        assert r"\wsense{noun}{the head of a group}" in text
        assert r"\wex{a communications director}" in text
        assert r"\wtag{Syn.}{manager}" in text

    def test_missing_definition_emits_a_blank(self):
        lines = latex._render_entry(
            {"word": "give up", "sentence": "to **give up**", "entry": {}}, 1
        )
        assert r"\wblank" in "\n".join(lines)

    def test_underscore_in_definition_is_escaped(self):
        entry = self._entry()
        entry["entry"]["senses"][0]["definition"] = "a_b definition"
        text = "\n".join(latex._render_entry(entry, 1))
        assert r"a\_b definition" in text

    def test_inflections_are_rendered_as_a_tag(self):
        entry = self._entry()
        entry["entry"]["inflections"] = [{"form": "directors", "label": "plural"}]
        text = "\n".join(latex._render_entry(entry, 1))
        assert r"\wtag{Inflections}{plural directors}" in text


class TestBuild:
    def test_all_three_outputs_are_created(self, tmp_path):
        result = latex.build(
            [{"word": "director", "sentence": "the **director**", "entry": {}}],
            title="test",
            tex_dir=tmp_path,
            dict_slug="mw",
        )
        assert Path(result["main"]).is_file()
        assert Path(result["preamble"]).is_file()
        assert Path(result["unit"]).is_file()
        assert Path(result["unit"]).parent.name == "entries"
        assert Path(result["unit"]).name == "test.tex"

    def test_main_tex_subfile_placeholder_is_replaced(self, tmp_path):
        latex.build([], title="mytitle", tex_dir=tmp_path, dict_slug="mw")
        main = (tmp_path / "main.tex").read_text(encoding="utf-8")
        assert "__ENTRIES__" not in main
        assert r"\subfile{entries/mytitle}" in main

    def test_main_tex_input_path_is_fixed_for_the_flat_layout(self, tmp_path):
        latex.build([], title="t", tex_dir=tmp_path, dict_slug="mw")
        main = (tmp_path / "main.tex").read_text(encoding="utf-8")
        assert r"\input{../preamble.excerpt}" not in main
        assert r"\input{preamble.excerpt}" in main

    def test_entry_tex_is_a_subfile(self, tmp_path):
        result = latex.build([], title="t", tex_dir=tmp_path, dict_slug="mw")
        unit = Path(result["unit"]).read_text(encoding="utf-8")
        assert r"\documentclass[../main.tex]{subfiles}" in unit
        assert r"\begin{document}" in unit

    def test_title_is_normalized(self, tmp_path):
        result = latex.build([], title="My Note!", tex_dir=tmp_path, dict_slug="mw")
        assert result["title"] == "mynote"
        assert Path(result["unit"]).name == "mynote.tex"


class TestLoadEntries:
    def test_reads_a_words_json_file(self, tmp_path):
        path = tmp_path / "w.json"
        path.write_text(json.dumps([{"word": "a"}]), encoding="utf-8")
        assert latex.load_entries(path) == [{"word": "a"}]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            latex.load_entries(tmp_path / "nope.json")

    def test_non_array_json_raises(self, tmp_path):
        path = tmp_path / "w.json"
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError):
            latex.load_entries(path)

    def test_reads_a_markdown_note(self, tmp_path):
        from common.config import BoldEntry
        from excerpt import write_md

        art = artifacts("t", "MW", str(tmp_path))
        entry = BoldEntry(
            word="director",
            type="word",
            sentence="the middle-school director.",
            line=8,
            code=0,
            source="api",
            entry={
                "senses": [
                    {
                        "pos": "noun",
                        "definition": "the head of a group",
                        "examples": ["a director"],
                    }
                ]
            },
        )
        write_md.write_md([entry], art, source_name="test.md")

        records = latex.load_entries(tmp_path / "t.mw.md")
        assert len(records) == 1
        assert records[0]["word"] == "director"
        assert records[0]["sentence"] == "the middle-school director."


class TestConsoleEntryPoint:
    def test_converts_a_words_json(self, tmp_path, capsys):
        payload = [
            {
                "word": "director",
                "type": "word",
                "sentence": "the **director**",
                "line": 3,
                "code": 0,
                "entry": {"senses": []},
            }
        ]
        source = tmp_path / "test.mw.words.json"
        source.write_text(json.dumps(payload), encoding="utf-8")

        rc = latex.main([str(source), "-o", str(tmp_path / "out"), "--title", "test"])
        assert rc == 0
        assert (tmp_path / "out" / "main.tex").is_file()
        assert (tmp_path / "out" / "entries" / "test.tex").is_file()
        assert "[write]" in capsys.readouterr().out

    def test_missing_input_returns_one(self, tmp_path, capsys):
        rc = latex.main([str(tmp_path / "nope.json")])
        assert rc == 1
        assert "[error]" in capsys.readouterr().out
