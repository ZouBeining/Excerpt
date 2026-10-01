r"""The ``excerpt`` pipeline package.

Keep :data:`__version__` in step with ``[project].version`` in
``pyproject.toml``, which is the source of truth: ``uv.lock`` mirrors it for
the editable install, and this value is what the code reports at runtime.
``tests/test_docs.py`` fails if the three ever disagree.

It is deliberately **not** read from the installed distribution metadata: the
editable install's ``*.dist-info`` only refreshes when the environment is
re-synced, so ``importlib.metadata.version("excerpt")`` reports whatever was
installed last rather than what the source tree says.
"""

__version__ = "0.1.9"

from .cli import main
