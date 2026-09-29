"""Dictionary lookup: API clients, caching, normalisation and JSON writing.

This package is independent from :mod:`excerpt`.  Both read their shared
configuration from :mod:`common.config`.

The convenience re-exports below let callers write ``from lookup import run``
without reaching into :mod:`lookup.__main__` directly.
"""

from .__main__ import lookup_entries, run, word_lookup_function

__all__ = ["lookup_entries", "run", "word_lookup_function"]
