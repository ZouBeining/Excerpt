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

    def test_only_the_target_bold_is_marked(self):
        sentence = 'And when she **hung up**, I was like, "**what\'s up**, what did he say?'
        rendered = latex.render_sentence(sentence, "hung up")
        assert r"\wmark{hung up}" in rendered
        assert r"\wmark{what's up}" not in rendered

    def test_the_other_record_marks_its_own_bold(self):
        sentence = 'And when she **hung up**, I was like, "**what\'s up**, what did he say?'
        rendered = latex.render_sentence(sentence, "what's up")
        assert r"\wmark{what's up}" in rendered
        assert r"\wmark{hung up}" not in rendered

    def test_lemma_form_is_the_marked_bold(self):
        rendered = latex.render_sentence("He was **deformed** by it.", "deform", "deformed")
        assert r"\wmark{deformed}" in rendered

    def test_several_unmatched_bolds_mark_none(self):
        assert r"\wmark" not in latex.render_sentence("**alpha** and **beta**", "gamma")


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
        assert r"\wsense{n.}{the head of a group}" in text
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
        assert r"\wtag{Inflections:}{plural directors}" in text

    def test_full_word_labels_keep_their_colon(self):
        entry = self._entry()
        text = "\n".join(latex._render_entry(entry, 1))
        assert r"\wtag{Etymology:}{see direct}" in text
        assert r"\wtag{First Use:}{15th century}" in text

    def test_abbreviated_labels_drop_the_colon(self):
        """``Syn.`` ends in its own period, so no colon may follow it."""
        entry = self._entry()
        entry["entry"]["antonyms"] = ["subordinate"]
        text = "\n".join(latex._render_entry(entry, 1))
        assert r"\wtag{Syn.}{manager}" in text
        assert r"\wtag{Anton.}{subordinate}" in text
        assert "Syn.:" not in text
        assert "Anton.:" not in text

    def test_only_the_entry_bold_is_marked(self):
        entry = self._entry(
            word="hung up",
            sentence='And when she **hung up**, I was like, "**what\'s up**, what did he say?',
        )
        text = "\n".join(latex._render_entry(entry, 1))
        assert r"\wmark{hung up}" in text
        assert r"\wmark{what's up}" not in text

    def test_lemma_entry_marks_the_inflected_form(self):
        entry = self._entry(
            word="deform", sentence="He was **deformed** by it.", lemma_from="deformed"
        )
        text = "\n".join(latex._render_entry(entry, 1))
        assert r"\whead{deform}" in text
        assert r"\wmark{deformed}" in text

    def test_pos_is_abbreviated(self):
        entry = self._entry()
        entry["entry"]["senses"][0]["pos"] = "transitive verb"
        text = "\n".join(latex._render_entry(entry, 1))
        assert r"\wsense{v.t.}{the head of a group}" in text

    def test_missing_pos_keeps_its_placeholder(self):
        entry = self._entry()
        entry["entry"]["senses"][0]["pos"] = ""
        text = "\n".join(latex._render_entry(entry, 1))
        assert r"\wsense{---}{the head of a group}" in text


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


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

class TestFindXelatex:
    def test_explicit_existing_path_is_used(self, tmp_path):
        fake = tmp_path / "xelatex.exe"
        fake.write_text("", encoding="utf-8")
        assert latex.find_xelatex(fake) == str(fake)

    def test_unknown_explicit_path_returns_none(self, tmp_path):
        assert latex.find_xelatex(tmp_path / "nope.exe") is None

    def test_path_lookup_is_consulted(self, monkeypatch):
        monkeypatch.setattr(latex.shutil, "which", lambda name: "/usr/bin/xelatex")
        assert latex.find_xelatex() == "/usr/bin/xelatex"

    def test_missing_everywhere_returns_none(self, monkeypatch):
        monkeypatch.setattr(latex.shutil, "which", lambda name: None)
        monkeypatch.setattr(latex, "_XELATEX_FALLBACK_DIRS", ())
        assert latex.find_xelatex() is None


class TestCompileTex:
    def test_skips_when_xelatex_is_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(latex.shutil, "which", lambda name: None)
        monkeypatch.setattr(latex, "_XELATEX_FALLBACK_DIRS", ())
        (tmp_path / "main.tex").write_text("", encoding="utf-8")

        result = latex.compile_tex(tmp_path)
        assert result["skipped"] is True
        assert result["ok"] is False
        assert result["pdf"] is None
        assert "xelatex not found" in result["reason"]

    def test_reports_a_missing_main_tex(self, tmp_path, monkeypatch):
        monkeypatch.setattr(latex.shutil, "which", lambda name: "/bin/xelatex")
        result = latex.compile_tex(tmp_path)
        assert result["ok"] is False
        assert "main.tex not found" in result["reason"]

    def test_runs_two_passes_and_returns_the_pdf(self, tmp_path, monkeypatch):
        monkeypatch.setattr(latex.shutil, "which", lambda name: "/bin/xelatex")
        (tmp_path / "main.tex").write_text("", encoding="utf-8")
        calls = []

        class Done:
            returncode = 0
            stdout = b"ok"

        def fake_run(argv, **kwargs):
            calls.append(argv)
            (tmp_path / "main.pdf").write_bytes(b"%PDF-1.5")
            return Done()

        monkeypatch.setattr(latex.subprocess, "run", fake_run)
        result = latex.compile_tex(tmp_path)

        assert result["ok"] is True
        assert result["passes"] == 2
        assert len(calls) == 2
        assert result["pdf"].endswith("main.pdf")
        # Non-interactive invocation: a failure must not block on stdin.
        assert "-interaction=nonstopmode" in calls[0]

    def test_stops_after_the_first_failing_pass(self, tmp_path, monkeypatch):
        monkeypatch.setattr(latex.shutil, "which", lambda name: "/bin/xelatex")
        (tmp_path / "main.tex").write_text("", encoding="utf-8")
        calls = []

        class Done:
            returncode = 1
            stdout = b"! Undefined control sequence."

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return Done()

        monkeypatch.setattr(latex.subprocess, "run", fake_run)
        result = latex.compile_tex(tmp_path)

        assert result["ok"] is False
        assert len(calls) == 1
        assert "Undefined control sequence" in result["reason"]

    def test_timeout_is_reported_not_raised(self, tmp_path, monkeypatch):
        import subprocess as real_subprocess

        monkeypatch.setattr(latex.shutil, "which", lambda name: "/bin/xelatex")
        (tmp_path / "main.tex").write_text("", encoding="utf-8")

        def fake_run(argv, **kwargs):
            raise real_subprocess.TimeoutExpired(cmd=argv, timeout=1)

        monkeypatch.setattr(latex.subprocess, "run", fake_run)
        result = latex.compile_tex(tmp_path)

        assert result["ok"] is False
        assert "could not run xelatex" in result["reason"]


class TestBuildWithCompile:
    def test_build_stays_tex_only_by_default(self, tmp_path):
        result = latex.build([], title="t", tex_dir=tmp_path)
        assert result["pdf"] is None
        assert result["compile"] is None

    def test_build_compiles_when_asked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(latex.shutil, "which", lambda name: "/bin/xelatex")

        class Done:
            returncode = 0
            stdout = b"ok"

        def fake_run(argv, **kwargs):
            (tmp_path / "main.pdf").write_bytes(b"%PDF-1.5")
            return Done()

        monkeypatch.setattr(latex.subprocess, "run", fake_run)
        result = latex.build([], title="t", tex_dir=tmp_path, compile_pdf=True)

        assert result["pdf"] is not None
        assert result["compile"]["ok"] is True

    def test_missing_xelatex_does_not_break_the_tex_output(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(latex.shutil, "which", lambda name: None)
        monkeypatch.setattr(latex, "_XELATEX_FALLBACK_DIRS", ())
        result = latex.build([], title="t", tex_dir=tmp_path, compile_pdf=True)

        # The .tex files must still exist even though no PDF was produced.
        assert Path(result["main"]).is_file()
        assert Path(result["unit"]).is_file()
        assert result["pdf"] is None
        assert result["compile"]["skipped"] is True


class TestConsoleCompileFlag:
    def _source(self, tmp_path):
        payload = [
            {
                "word": "director",
                "type": "word",
                "sentence": "the **director**",
                "line": 1,
                "code": 0,
                "entry": {"senses": []},
            }
        ]
        source = tmp_path / "test.mw.words.json"
        source.write_text(json.dumps(payload), encoding="utf-8")
        return source

    def test_compile_flag_produces_a_pdf(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(latex.shutil, "which", lambda name: "/bin/xelatex")

        class Done:
            returncode = 0
            stdout = b"ok"

        def fake_run(argv, **kwargs):
            (tmp_path / "out" / "main.pdf").write_bytes(b"%PDF-1.5")
            return Done()

        monkeypatch.setattr(latex.subprocess, "run", fake_run)
        rc = latex.main(
            [str(self._source(tmp_path)), "-o", str(tmp_path / "out"), "--compile"]
        )

        assert rc == 0
        assert "[pdf]" in capsys.readouterr().out

    def test_compile_flag_reports_a_missing_tool(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(latex.shutil, "which", lambda name: None)
        monkeypatch.setattr(latex, "_XELATEX_FALLBACK_DIRS", ())
        rc = latex.main(
            [str(self._source(tmp_path)), "-o", str(tmp_path / "out"), "--compile"]
        )

        assert rc == 0
        out = capsys.readouterr().out
        assert "skipped compilation" in out
