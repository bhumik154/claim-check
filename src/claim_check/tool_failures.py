"""Lines in a command's output that prove a particular tool failed.

A Bash call's exit code rarely says whether the tool in it failed: agents pipe
output through `tail` (29 of 32 traced claims), whose exit code is tail's, and
a successful call reports no exit code at all. What the tool printed does say.
Each pattern below was taken from real output captured on CI; see
tests/fixtures/tool_outputs/README.md.

Each line found is tagged with the tool family that prints it, so the caller
can decide whether the command a claim names is the one that could have
printed it. That decision lives in run_log.py.
"""

import re
from typing import List, Tuple

from .evidence import parse_run_output
from .pytest_parser import strip_ansi

_PATTERNS = (
    # GNU make's own line when a recipe fails: "make: *** [Makefile:4: test] Error 1".
    ("make", re.compile(r"^g?make(?:\[\d+\])?: \*\*\* .*$", re.MULTILINE)),
    # go test: a failed test, and the per-package FAIL line.
    ("go test", re.compile(r"^(?:--- FAIL: .*|FAIL\t\S.*)$", re.MULTILINE)),
    ("tsc", re.compile(r"^.*\berror TS\d+: .*$", re.MULTILINE)),
    # eslint prints this line on success too; only a nonzero error count fails.
    ("eslint", re.compile(r"^\s*✖ \d+ problems? \([1-9]\d* errors?.*$", re.MULTILINE)),
    ("pnpm", re.compile(r"^\s*ELIFECYCLE\s+Command failed.*$", re.MULTILINE)),
)

# The programs that can print each family's line themselves. Wrappers
# (make, npm run, a shell script) can print anything; run_log.py handles them.
PRINTERS = {
    "make": frozenset({"make", "gmake"}),
    "go test": frozenset({"go"}),
    "tsc": frozenset({"tsc"}),
    "eslint": frozenset({"eslint"}),
    "pnpm": frozenset({"pnpm"}),
    "tests": frozenset({"pytest", "py.test", "vitest", "jest"}),
}


_GO_PACKAGE = re.compile(r"^(?:FAIL\t(?P<failed>\S+).*|ok\s+(?P<passed>\S+).*|FAIL)$", re.MULTILINE)


def _a_go_package_failed(text: str) -> bool:
    """Whether go test's own package lines say a package ended failed.

    A failing package always ends with "FAIL\\t<package>" (or a bare "FAIL").
    Without one, "--- FAIL:" is some other program's text. And a later
    "ok  \\t<package>" means a rerun passed it: gotestsum --rerun-fails
    prints both, then exits 0.
    """
    failed = {}
    bare_fail = False
    for match in _GO_PACKAGE.finditer(text):
        if match.group("failed"):
            failed[match.group("failed")] = True
        elif match.group("passed"):
            failed[match.group("passed")] = False
        else:
            bare_fail = True
    if failed:
        return any(failed.values())
    return bare_fail


def failure_lines(output, command: str = "") -> List[Tuple[str, str]]:
    """(family, line) for every line in `output` that proves a failure.

    "tests" is a test runner's own summary reporting failures or errors, read
    with the same parser that records test runs, so the same protections
    against a forged or unrelated tally apply.
    """
    return [(family, line) for family, line, _offset in locate_failures(output, command)]


def locate_failures(output, command: str = "") -> List[Tuple[str, str, int]]:
    """As `failure_lines`, with each line's offset in the ANSI-stripped text,
    sorted by offset."""
    if not isinstance(output, str) or not output:
        return []
    text = strip_ansi(output)
    found = []
    for family, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if match.group(0).lstrip().startswith("> "):
                # npm and pnpm echo a script's command as "> <command>" before
                # running it. The command's own text is not its output: a
                # script mentioning "error TS1005:" once read as tsc failing.
                continue
            found.append((family, match.group(0).strip(), match.start()))
    if not _a_go_package_failed(text):
        found = [item for item in found if item[0] != "go test"]
    counts = parse_run_output(text, command or "")
    if counts is not None and (counts.failed or counts.errors):
        line = counts.raw_summary_line.strip()
        found.append(("tests", line, max(text.rfind(line), 0)))
    return sorted(found, key=lambda item: item[2])
