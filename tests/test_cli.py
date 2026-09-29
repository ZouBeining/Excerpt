"""Tests for the CLI pipeline, the argument parser and the configuration order.

The pipeline is exercised with the lookup stage stubbed out, so nothing here
touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common import config
from common.config import CODE_NOT_A_SINGLE_WORD, CODE_OK, BoldEntry
from excerpt import cli
from excerpt.arg import build_parser


SAMPLE = """# Sample

He is the head **director** here. And **give up** right now!
"""


@pytest.fixture
def stub_lookup(monkeypatch):
    """Replace ``lookup.run`` with a stub that resolves words offline."""
    captured: dict = {}

    def fake_run(entries, art, **kwargs):
        captured["entries"] = list(entries)
        captured["art"] = art
        captured["kwargs"] = kwargs
        resolved = []
        for entry in entries:
            if entry.type == "word":
                entry.code = CODE_OK
                entry.source = "api"
                entry.dict_slug = art.dict_slug
                entry.entry = {
                    "word": entry.word,
                    "pos": "noun",
                    "senses": [
                        {
                            "pos": "noun",
                            "definition": f"definition of {entry.word}",
                            "examples": [],
                        }
                    ],
                }
            resolved.append(entry)

        from lookup import write_json

        summary = write_json.write_documents(
            resolved,
            words_path=art.out_dir + "/" + art.words_json,
            errors_path=art.out_dir + "/" + art.errors_json,
            dict_slug=art.dict_slug,
        )
        captured["summary"] = summary
        return None, resolved, summary

    monkeypatch.setattr("lookup.run", fake_run)
    return captured


class TestArgumentParser:
    def test_source_is_required(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_dict_choice_defaults_to_none(self):
        # None means "fall back to .env or the config default".
        args = build_parser().parse_args(["a.md"])
        assert args.dict_choice is None

    def test_use_llm_defaults_to_none_so_env_decides(self):
        args = build_parser().parse_args(["a.md"])
        assert args.use_llm is None

    def test_use_llm_and_no_llm_are_opposites(self):
        assert build_parser().parse_args(["a.md", "--use-llm"]).use_llm is True
        assert build_parser().parse_args(["a.md", "--no-llm"]).use_llm is False

    def test_llm_flags_are_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["a.md", "--use-llm", "--no-llm"])

    def test_no_latex_flag(self):
        assert build_parser().parse_args(["a.md", "--no-latex"]).no_latex is True

    def test_dict_choice_is_accepted(self):
        args = build_parser().parse_args(["a.md", "--dict-choice", "FD"])
        assert args.dict_choice == "FD"

    def test_session_flags(self):
        args = build_parser().parse_args(
            ["a.md", "--timeout", "5", "--retry", "1", "--delay", "0"]
        )
        assert args.timeout == 5
        assert args.retry == 1


class TestPipeline:
    def test_missing_source_returns_one(self, tmp_path, capsys):
        rc = cli.main([str(tmp_path / "nope.md")])
        assert rc == 1
        assert "doesn't exist" in capsys.readouterr().err

    def test_extraction_only_writes_the_index(self, tmp_path, stub_lookup):
        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        out = tmp_path / "out"

        rc = cli.main(
            [str(source), "-o", str(out), "--no-lookup", "--no-latex", "--no-llm"]
        )
        assert rc == 0
        assert (out / "s.index.json").is_file()
        assert (out / "s.mw.index.md").is_file()

        payload = json.loads((out / "s.index.json").read_text(encoding="utf-8"))
        assert payload["title"] == "s"
        assert payload["items"][0]["word"] == "director"

    def test_full_run_writes_every_artifact(self, tmp_path, stub_lookup):
        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        out = tmp_path / "out"

        rc = cli.main([str(source), "-o", str(out), "--no-llm"])
        assert rc == 0

        for name in (
            "s.index.json",
            "s.mw.index.md",
            "s.mw.words.json",
            "s.mw.errors.json",
            "s.mw.md",
            "main.tex",
            "preamble.excerpt.tex",
        ):
            assert (out / name).is_file(), name
        assert (out / "entries" / "s.tex").is_file()

    def test_words_file_uses_the_dict_slug(self, tmp_path, stub_lookup):
        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        out = tmp_path / "out"
        cli.main([str(source), "-o", str(out), "--no-llm"])

        written = json.loads((out / "s.mw.words.json").read_text(encoding="utf-8"))
        assert written
        assert all(record["dict"] == "mw" for record in written)
        assert all(record["entry"] for record in written)

    def test_title_override_is_normalized(self, tmp_path, stub_lookup):
        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        out = tmp_path / "out"
        cli.main([str(source), "-o", str(out), "--title", "My Note", "--no-llm"])
        assert (out / "mynote.index.json").is_file()
        assert (out / "mynote.mw.md").is_file()

    def test_chinese_file_name_falls_back_to_excerpt(self, tmp_path, stub_lookup):
        source = tmp_path / "\u7ae0\u8282.md"
        source.write_text(SAMPLE, encoding="utf-8")
        out = tmp_path / "out"
        cli.main([str(source), "-o", str(out), "--no-llm"])
        assert (out / "excerpt.index.json").is_file()

    def test_default_output_directory_uses_the_title(self, tmp_path, stub_lookup):
        source = tmp_path / "note.md"
        source.write_text(SAMPLE, encoding="utf-8")
        rc = cli.main([str(source), "--no-lookup", "--no-latex", "--no-llm"])
        assert rc == 0
        assert (tmp_path / ".note_out" / "note.index.json").is_file()

    def test_no_latex_skips_the_tex_files(self, tmp_path, stub_lookup):
        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        out = tmp_path / "out"
        cli.main([str(source), "-o", str(out), "--no-latex", "--no-llm"])
        assert not (out / "main.tex").exists()

    def test_invalid_dict_choice_is_reported(self, tmp_path, capsys):
        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        rc = cli.main([str(source), "--dict-choice", "NOPE", "--no-lookup", "--no-latex"])
        assert rc == 2
        assert "[error]" in capsys.readouterr().err

    def test_llm_stage_runs_when_enabled(self, tmp_path, stub_lookup, monkeypatch):
        called: list = []

        def fake_llm_run(entries, art, dict_slug=""):
            called.append(art.title)
            return list(entries)

        monkeypatch.setattr("excerpt.llm.run", fake_llm_run)

        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        cli.main(
            [str(source), "-o", str(tmp_path / "out"), "--use-llm", "--no-latex"]
        )
        assert called == ["s"]

    def test_llm_stage_is_skipped_when_disabled(self, tmp_path, stub_lookup, monkeypatch):
        called: list = []
        monkeypatch.setattr(
            "excerpt.llm.run", lambda *a, **k: called.append(1) or a[0]
        )

        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        cli.main([str(source), "-o", str(tmp_path / "out"), "--no-llm", "--no-latex"])
        assert called == []


class TestLlmPreflight:
    """The preflight must run before the pipeline and gate the exit code."""

    def test_preflight_passes_when_config_is_valid(
        self, tmp_path, stub_lookup, monkeypatch, capsys
    ):
        from excerpt.llm_check import CheckResult

        monkeypatch.setattr(
            "excerpt.cli.check_llm_config",
            lambda **kwargs: CheckResult(ok=True, code="OK", status=200),
        )
        monkeypatch.setattr("excerpt.llm.run", lambda entries, art, dict_slug="": entries)

        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        rc = cli.main(
            [str(source), "-o", str(tmp_path / "out"), "--use-llm", "--no-latex"]
        )
        assert rc == 0
        assert "configuration verified: ok" in capsys.readouterr().out

    def test_preflight_aborts_on_invalid_config(
        self, tmp_path, stub_lookup, monkeypatch, capsys
    ):
        from excerpt.llm_check import CheckResult

        monkeypatch.setattr(
            "excerpt.cli.check_llm_config",
            lambda **kwargs: CheckResult(
                ok=False, code="INVALID_API_KEY", reason="401 rejected"
            ),
        )
        ran: list = []
        monkeypatch.setattr(
            "excerpt.llm.run", lambda *a, **k: ran.append(1) or a[0]
        )

        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        rc = cli.main(
            [str(source), "-o", str(tmp_path / "out"), "--use-llm", "--no-latex"]
        )
        captured = capsys.readouterr()
        assert rc == 3
        assert "[error]" in captured.err
        assert ran == [], "no stage may run after a failed preflight"
        assert not (tmp_path / "out" / "s.index.json").exists()

    def test_preflight_warns_but_continues_when_transient(
        self, tmp_path, stub_lookup, monkeypatch, capsys
    ):
        from excerpt.llm_check import CheckResult

        monkeypatch.setattr(
            "excerpt.cli.check_llm_config",
            lambda **kwargs: CheckResult(
                ok=True, code="RATE_LIMITED", reason="slow down", transient=True
            ),
        )
        monkeypatch.setattr("excerpt.llm.run", lambda entries, art, dict_slug="": entries)

        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        rc = cli.main(
            [str(source), "-o", str(tmp_path / "out"), "--use-llm", "--no-latex"]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert "[warn]" in captured.out
        assert (tmp_path / "out" / "s.index.json").is_file()

    def test_no_llm_check_flag_skips_the_probe(
        self, tmp_path, stub_lookup, monkeypatch
    ):
        def explode(**kwargs):  # pragma: no cover - must never run
            raise AssertionError("preflight must be skipped")

        monkeypatch.setattr("excerpt.cli.check_llm_config", explode)
        monkeypatch.setattr("excerpt.llm.run", lambda entries, art, dict_slug="": entries)

        source = tmp_path / "s.md"
        source.write_text(SAMPLE, encoding="utf-8")
        rc = cli.main(
            [
                str(source),
                "-o",
                str(tmp_path / "out"),
                "--use-llm",
                "--no-llm-check",
                "--no-latex",
            ]
        )
        assert rc == 0


class TestConfigurationPriority:
    def test_cli_beats_env(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DICT_CHOICE=FD\nUSE_LLM=True\n", encoding="utf-8")

        config.configure(
            dict_choice="MW", use_llm=False, env_path=str(env_file)
        )
        assert config.get_dict_choice() == "MW"
        assert config.get_use_llm() is False

    def test_env_is_used_when_cli_is_silent(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DICT_CHOICE=FD\nUSE_LLM=True\n", encoding="utf-8")

        config.configure(dict_choice=None, use_llm=None, env_path=str(env_file))
        assert config.get_dict_choice() == "FD"
        assert config.get_use_llm() is True

    def test_default_use_llm_is_false(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DICT_CHOICE=MW\n", encoding="utf-8")
        config.configure(dict_choice=None, use_llm=None, env_path=str(env_file))
        assert config.get_use_llm() is False

    def test_env_overrides_a_preexisting_shell_variable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DICT_CHOICE", "MW")
        env_file = tmp_path / ".env"
        env_file.write_text("DICT_CHOICE=FD\n", encoding="utf-8")
        config.configure(dict_choice=None, env_path=str(env_file))
        assert config.get_dict_choice() == "FD"

    def test_explicit_empty_env_path_loads_nothing(self, monkeypatch):
        monkeypatch.setenv("DICT_CHOICE", "FD")
        config.configure(dict_choice="MW", env_path="")
        assert config.get_dict_choice() == "MW"


class TestDictionaryExtensibility:
    """No legal dictionary option may be hard-coded outside config.py."""

    def test_slug_comes_from_the_config(self):
        assert config.get_dict_slug("MW") == "mw"
        assert config.get_dict_slug("FD") == "fd"

    def test_unknown_choice_raises(self):
        with pytest.raises(KeyError):
            config.get_dict_config("nope")

    def test_blank_choice_falls_back_to_the_active_one(self):
        """``""`` means "no explicit choice", not "an invalid choice"."""
        config.configure(dict_choice="FD", env_path="")
        assert config.get_dict_config("") is config.get_dict_config()
        assert config.get_dict_slug("") == "fd"
        assert config.get_dict_slug("   ") == "fd"

    def test_artifacts_accept_an_empty_slug(self, tmp_path):
        """A caller without a slug must not be rejected."""
        from common.config import artifacts

        config.configure(dict_choice="MW", env_path="")
        art = artifacts("doc", "", str(tmp_path))
        assert art.dict_slug == "mw"
        assert art.words_json == "doc.mw.words.json"

    def test_configure_rejects_an_unknown_choice(self):
        with pytest.raises(ValueError):
            config.configure(dict_choice="nope", env_path="")

    def test_artifacts_follow_the_slug(self, tmp_path):
        from common.config import artifacts

        art = artifacts("doc", "FD", str(tmp_path))
        assert art.words_json == "doc.fd.words.json"
        assert art.errors_json == "doc.fd.errors.json"
        assert art.index_md == "doc.fd.index.md"
