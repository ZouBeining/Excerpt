"""LLM completion: prompts, provider interaction, caching and preflight.

This package is independent from :mod:`excerpt`.  Like :mod:`lookup` — its
sibling stage package — it reads its shared configuration from
:mod:`common.config`, and it is the only place that depends on the ``openai``
client.

The stage is split across submodules by concern:

* :mod:`llm.__main__` — the orchestration: prompts, schema, request loop,
  circuit breaker, and the two public entry points;
* :mod:`llm.cache` — the completion cache, keyed on the prompt version;
* :mod:`llm.check` — the preflight probe that validates the configuration;
* :mod:`llm.format` — the walk over ``response_format`` dialects;
* :mod:`llm.reasoning` — the walk over reasoning-suppression fields.

The convenience re-exports below let callers write ``from llm import run``
without reaching into :mod:`llm.__main__` directly.
"""

from .__main__ import SYSTEM_PROMPT, complete_entries, run, schema_for

__all__ = ["SYSTEM_PROMPT", "complete_entries", "run", "schema_for"]
