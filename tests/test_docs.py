"""The repository's own claims must match what the code and the files say.

Three drifts motivated this file, each of which had gone unnoticed for
releases:

* ``docs/template.index.md`` was missing the ``valid_entries`` header line the
  renderer emits;
* ``docs/template.md`` quoted ``\\`US\\` /də-ˈrek-tər/``, a pronunciation line the
  renderer stopped producing when it began joining every pronunciation;
* ``src/excerpt/__init__.py`` still announced ``0.1.2`` several releases on.

``docs/template.*`` are the reference layout for the renderers, so they are
checked by regenerating them and comparing byte for byte.  Keep them in step
by re-rendering from ``docs/template.words.json`` / ``docs/template.index.json``
whenever a renderer changes — the failure message below is the assertion, and
the fix is to regenerate rather than to edit the sample by hand.

Nothing here opens a file outside the repository or touches the network.
"""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

import excerpt
from common import config
from common.config import BoldEntry, ErrorRecord, artifacts
from excerpt import write_md

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SRC = ROOT / "src"

#: How to put the samples back in step after a renderer change.
REGENERATE_HINT = (
    "regenerate docs/template.md and docs/template.index.md by rendering "
    "docs/template.words.json through excerpt.write_md, then commit both"
)


def _read_json(name: str):
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def _sample_entries() -> list[BoldEntry]:
    """Rebuild the sample records from ``docs/template.words.json``."""
    return [
        BoldEntry(
            word=record["word"],
            type=record["type"],
            sentence=record["sentence"],
            line=record["line"],
            code=record["code"],
            detail=record["detail"],
            source=record["source"],
            dict_slug=record["dict"],
            entry=record["entry"],
            is_lemma=record["is_lemma"],
            lemma_from=record["lemma_from"],
        )
        for record in _read_json("template.words.json")
    ]


class TestSampleDocuments:
    """``docs/template.*`` must be exactly what the renderers produce."""

    def test_sample_note_matches_the_renderer(self):
        art = artifacts("test", "mw", out_dir=".")
        expected = write_md.render_note_md(
            _sample_entries(), _read_json("template.index.json"), art
        )
        actual = (DOCS / "template.md").read_text(encoding="utf-8")
        assert actual == expected, f"docs/template.md is stale; {REGENERATE_HINT}"

    def test_sample_index_matches_the_renderer(self):
        art = artifacts("test", "mw", out_dir=".")
        expected = write_md.render_index_md(
            _sample_entries(), _read_json("template.index.json"), art
        )
        actual = (DOCS / "template.index.md").read_text(encoding="utf-8")
        assert actual == expected, f"docs/template.index.md is stale; {REGENERATE_HINT}"

    def test_sample_documents_use_the_standard_schemas(self):
        """AGENTS.md: the samples must carry the standard shapes, unfilled too."""
        record_keys = list(BoldEntry(word="x").to_dict().keys())
        for record in _read_json("template.words.json"):
            assert list(record.keys()) == record_keys
            assert list(record["entry"].keys()) == config.STANDARD_ENTRY_KEYS

        assert list(_read_json("template.index.json").keys()) == [
            "title",
            "source",
            "items",
        ]

        errors = _read_json("template.errors.json")
        assert list(errors.keys()) == list(config.empty_errors_document().keys())
        error_keys = list(
            ErrorRecord(
                word="x", id="x", type="word", source="lookup", code=0, reason=""
            ).to_dict().keys()
        )
        for record in errors["errors"]:
            assert list(record.keys()) == error_keys


class TestEnvExample:
    """``.env.example`` is the documented key list, so it must be complete.

    Four keys (``TIMEOUT`` / ``RETRY`` / ``DELAY`` / ``PROXY``) were missing
    from the file even though a comment inside it referred to two of them.

    The expected set is derived from the source rather than written down here:
    every ``os.getenv("X")`` / ``os.environ.get("X")`` literal under ``src/``,
    plus each dictionary's ``api_key_env``.  A new setting therefore fails this
    test until it is documented, which is the point.
    """

    @staticmethod
    def _read_env_keys() -> set[str]:
        names: set[str] = set()
        for path in sorted(SRC.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                is_getenv = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "getenv"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                )
                is_environ_get = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get"
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "environ"
                )
                first = node.args[0]
                if (
                    (is_getenv or is_environ_get)
                    and isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                ):
                    names.add(first.value)
        # The dictionary key is named by the chosen dictionary, so its name is
        # data (``api_key_env``) rather than a literal in a ``getenv`` call.
        for choice in config.DICT_CHOICES.values():
            key = str(choice.get("api_key_env") or "")
            if key:
                names.add(key)
        return names

    @staticmethod
    def _documented_keys() -> set[str]:
        lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        return {
            line.split("=", 1)[0].strip()
            for line in lines
            if line and not line.lstrip().startswith("#") and "=" in line
        }

    def test_env_example_documents_every_key_the_source_reads(self):
        undocumented = self._read_env_keys() - self._documented_keys()
        assert not undocumented, (
            f"{sorted(undocumented)} read from the environment but absent from "
            ".env.example; add them there (and to the README's .env table)"
        )

    def test_env_example_invents_no_keys(self):
        stale = self._documented_keys() - self._read_env_keys()
        assert not stale, (
            f".env.example documents {sorted(stale)}, which no source file "
            "reads; drop them or wire them up"
        )


class TestVersionConsistency:
    """A version is written in three places, and all three must agree.

    ``pyproject.toml`` is the source of truth; ``uv.lock`` mirrors it for the
    editable install, and ``src/excerpt/__init__.py`` is what the code reports
    at runtime.  The lock is edited by hand during a release (running
    ``uv lock`` in a shell without the developer's mirror settings rewrites
    every package's URL), so a guard is worth more here than usual.
    """

    @staticmethod
    def _pyproject_version() -> str:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        return data["project"]["version"]

    def test_package_version_matches_pyproject(self):
        assert excerpt.__version__ == self._pyproject_version(), (
            "src/excerpt/__init__.py and pyproject.toml disagree; a release "
            "must update both"
        )

    def test_uv_lock_mirrors_the_pyproject_version(self):
        lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        locked = [p for p in lock["package"] if p["name"] == "excerpt"]
        assert len(locked) == 1, "uv.lock should carry exactly one excerpt entry"
        assert locked[0]["version"] == self._pyproject_version(), (
            "uv.lock still names the old version; sync the excerpt entry by hand"
        )
