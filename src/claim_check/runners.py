"""Single entry point for reading any supported test runner's output.

Dispatch is by content, not by command string. Deciding from the output is
what let the PostToolUse observer recognise a wrapper like "make test" or
"poetry run pytest" on its merits, and the same reasoning applies here: a
command can lie about what it runs, output cannot.

Each parser must decline what is not its own. If two accepted the same text,
dispatch order would silently decide correctness, so there are tests pinning
that pytest output is rejected by the JS parser and vice versa.
"""

from typing import Optional

from .js_parser import parse_js_summary
from .models import PytestCounts
from .pytest_parser import parse_summary_line

# PytestCounts predates any second runner and is exported in __all__, so it
# keeps its name. This alias is the one to reach for in new code; the
# pytest-specific fields (xfailed, xpassed, deselected) are simply zero for
# runners that have no such concept.
TestCounts = PytestCounts

_PARSERS = (parse_summary_line, parse_js_summary)


def parse_test_output(output) -> Optional[PytestCounts]:
    """Counts from a test run, or None if this output is not one.

    Never raises. Every entry point that reaches here runs inside a hook.
    """
    if not isinstance(output, str) or not output:
        return None
    for parser in _PARSERS:
        try:
            counts = parser(output)
        except Exception:  # noqa: BLE001 - a parser bug must not break a hook
            continue
        if counts is not None:
            return counts
    return None
