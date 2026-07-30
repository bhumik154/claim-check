"""Records real test runs the agent made, so a claim can be checked against
what actually happened instead of re-running the suite.

This exists because the alternative is unaffordable. Verifying a claim at the
end of every turn by running pytest would add a full suite run to each turn;
observing the runs the agent already made costs nothing.

Two invariants govern everything here, and both exist to preserve the
property that makes this project usable - that it does not produce false
positives:

1. **Staleness can only ever downgrade evidence to "unknown", never promote
   a claim to "false".** A wrong fingerprint, a missing file, a corrupt
   record, an unreadable cache directory: every one of them resolves to "no
   usable evidence", which means no finding is reported. A cache bug can
   therefore cause a missed catch. It can never cause a false accusation.

2. **When scope is uncertain, evidence is marked scoped.** Scoped evidence
   cannot confirm or contradict a whole-suite claim. Over-marking loses
   coverage; under-marking would let `pytest -k something` reporting
   "1 passed, 1 deselected" confirm "all tests pass" while the real suite is
   red. That is worse than not checking at all, so the bias is deliberate.

Evidence lives outside the working tree, under the user's cache directory.
Inside the repo it would be a file the agent under verification can write,
and editing the evidence would be a cheaper way to satisfy a check than
telling the truth.
"""

import hashlib
import json
import os
import shlex
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence

from .models import PytestCounts
from .pytest_parser import parse_summary_line

# Test results depend on state that mtimes cannot observe: environment
# variables, database contents, installed package versions, the clock. Even
# an unchanged tree stops being reliable evidence after a while.
DEFAULT_TTL_S = 900.0

# Directories that change constantly without changing what the tests do.
_IGNORED_DIRS = frozenset(
    {
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "build",
        "dist",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".eggs",
        "htmlcov",
    }
)

# Flags that narrow what pytest collects, or stop it early. Any of these means
# the run's totals are not the whole suite's totals.
_SCOPE_NARROWING_FLAGS = frozenset(
    {
        "-k",
        "-m",
        "-x",
        "--exitfirst",
        "--lf",
        "--last-failed",
        "--ff",
        "--failed-first",
        "--sw",
        "--stepwise",
        "--stepwise-skip",
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "--maxfail",
        "--co",
        "--collect-only",
    }
)

# Options that consume the following token as their value, so that token is
# not a path argument.
_VALUE_TAKING_FLAGS = frozenset(
    {
        "-k",
        "-m",
        "-p",
        "-n",
        "-o",
        "-c",
        "-W",
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "--maxfail",
        "--rootdir",
        "--junitxml",
        "--override-ini",
        "--confcutdir",
        "--import-mode",
        "--dist",
    }
)

_SHELL_OPERATORS = frozenset({"&&", "||", ";", "|", "&", "(", ")", "|&"})

_RUNNER_TOKENS = frozenset({"python", "python3", "py", "-m", "pytest", "py.test", "poetry", "hatch", "run", "uv", "pipenv", "exec"})


def cache_root() -> Path:
    """Base directory for all evidence, outside any working tree."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "claim-check"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "claim-check"
    return Path.home() / ".cache" / "claim-check"


def evidence_path(project_dir, session_id: str) -> Path:
    """One file per (project, session).

    Keyed on session so one session's run can never be read as evidence for
    another session's claim - two agents working in the same repo would
    otherwise silently judge each other's work.
    """
    digest = hashlib.sha256(str(Path(project_dir).resolve()).encode("utf-8", "replace")).hexdigest()[:16]
    safe_session = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64] or "unknown"
    return cache_root() / digest / f"{safe_session}.json"


def fingerprint(project_dir) -> str:
    """Cheap identity for the state of a source tree.

    Hashes (relative path, mtime_ns, size) for every file, never contents.
    Deliberately not `git write-tree` (needs the index and mutates the object
    database) nor `git status` (a subprocess, on a path that runs every turn).

    Returns "" if the tree cannot be walked, which reads downstream as "no
    usable fingerprint" and therefore as unusable evidence.
    """
    try:
        root = Path(project_dir).resolve()
    except (OSError, ValueError):
        return ""

    digest = hashlib.sha256()
    try:
        stack = [root]
        entries = []
        while stack:
            current = stack.pop()
            with os.scandir(current) as scanner:
                for entry in scanner:
                    name = entry.name
                    if name.startswith(".") or name in _IGNORED_DIRS:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            entries.append((os.path.relpath(entry.path, root), stat.st_mtime_ns, stat.st_size))
                    except OSError:
                        # A file that vanished mid-walk, or one we cannot
                        # stat. Skipping it can only make the fingerprint
                        # differ, which downgrades evidence to stale.
                        continue
    except OSError:
        return ""

    for relative, mtime_ns, size in sorted(entries):
        digest.update(f"{relative}\0{mtime_ns}\0{size}\0".encode("utf-8", "replace"))
    return digest.hexdigest()


def _pytest_segments(tokens: Sequence[str]) -> list:
    """Splits a shell command into segments and returns those running pytest.

    The Bash tool hands over a whole shell line, not a bare invocation -
    measured against a live session, an ordinary run arrives as
    `cd /repo && python -m pytest -q 2>&1 | tail -1`. Analysing that as one
    flat argv reads "cd" as a path argument and marks every real run scoped,
    which silently makes the entire evidence store unusable.
    """
    segments = [[]]
    for token in tokens:
        if (
            token in _SHELL_OPERATORS
            or token.startswith(">")
            or token.startswith("<")
            or token.startswith("2>")
            or token.startswith("1>")
            or token.startswith("&>")
        ):
            segments.append([])
        else:
            segments[-1].append(token)

    return [
        segment
        for segment in segments
        if any(token == "pytest" or token.endswith("pytest") or token.endswith("py.test") for token in segment)
    ]


def is_scoped(argv: Sequence[str]) -> bool:
    """True if this invocation ran less than the whole suite.

    Biased towards True. Anything unrecognised, unparseable, or ambiguous is
    reported as scoped, because scoped evidence is merely unusable while
    wrongly-unscoped evidence can confirm a false whole-suite claim.
    """
    if not argv:
        return True

    pytest_segments = _pytest_segments(argv)
    if not pytest_segments:
        # No recognisable pytest invocation. It may well have run the whole
        # suite ("make test"), but nothing here can establish that, and
        # unknown scope is never usable as whole-suite evidence.
        return True

    return any(_segment_is_scoped(segment) for segment in pytest_segments)


def _segment_is_scoped(tokens: list) -> bool:

    # "-m" is ambiguous: for `python -m pytest` it is the interpreter's module
    # flag, but for `pytest -m integration` it is a marker filter that narrows
    # collection. Only the second meaning implies scope, so drop the
    # interpreter form before parsing. Without this, every ordinary
    # `python -m pytest` run was marked scoped and no evidence was ever
    # usable - confirmed directly by the test suite.
    for position in range(len(tokens) - 1):
        if tokens[position] == "-m" and tokens[position + 1] in ("pytest", "py.test"):
            del tokens[position : position + 2]
            break

    index = 0
    while index < len(tokens):
        token = tokens[index]

        if token in _SCOPE_NARROWING_FLAGS:
            return True
        if token.startswith("--") and "=" in token and token.split("=", 1)[0] in _SCOPE_NARROWING_FLAGS:
            return True
        # Bundled short flags such as "-xvs" carry -x inside them.
        if token.startswith("-") and not token.startswith("--") and len(token) > 1:
            if "x" in token[1:] and not token.startswith("-x="):
                return True

        if token in _VALUE_TAKING_FLAGS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue

        # A bare token. Runner words are expected; anything else is a path or
        # a node id, which narrows collection.
        if token not in _RUNNER_TOKENS and not token.endswith("pytest"):
            return True
        index += 1

    return False


def record_run(
    project_dir,
    session_id: str,
    command: str,
    stdout: str,
    ttl_s: float = DEFAULT_TTL_S,
) -> Optional[dict]:
    """Parses a completed test run and stores it as evidence.

    Returns the stored record, or None if the output held no pytest summary
    (meaning this was not a test run, or produced nothing to learn from).

    Never raises. A failure to write evidence must not disturb the tool call
    being observed.
    """
    counts = parse_summary_line(stdout)
    if counts is None:
        return None

    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        argv = []

    record = {
        "recorded_at": time.time(),
        "session_id": str(session_id),
        "cwd": str(project_dir),
        "argv": argv,
        "command": command[:500],
        "scoped": is_scoped(argv),
        "fingerprint": fingerprint(project_dir),
        "ttl_s": ttl_s,
        "counts": {
            "passed": counts.passed,
            "failed": counts.failed,
            "skipped": counts.skipped,
            "xfailed": counts.xfailed,
            "xpassed": counts.xpassed,
            "errors": counts.errors,
            "warnings": counts.warnings,
            "deselected": counts.deselected,
            "duration_s": counts.duration_s,
            "raw_summary_line": counts.raw_summary_line[:500],
        },
    }

    _write_atomic(evidence_path(project_dir, session_id), record)
    return record


def load_fresh(
    project_dir,
    session_id: str,
    now: Optional[float] = None,
) -> Optional[dict]:
    """Returns stored evidence only if it is still trustworthy.

    Returns None - meaning "unknown", never "false" - when the file is
    missing, unreadable, malformed, past its TTL, or recorded against a
    different tree state.
    """
    path = evidence_path(project_dir, session_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None

    current = now if now is not None else time.time()
    recorded_at = record.get("recorded_at")
    ttl = record.get("ttl_s", DEFAULT_TTL_S)
    if not isinstance(recorded_at, (int, float)) or not isinstance(ttl, (int, float)):
        return None
    if current - recorded_at > ttl:
        return None

    stored_fingerprint = record.get("fingerprint")
    if not stored_fingerprint or stored_fingerprint != fingerprint(project_dir):
        return None

    return record


def counts_from_record(record: dict) -> Optional[PytestCounts]:
    """Rebuilds PytestCounts from a stored record, or None if malformed."""
    raw = record.get("counts")
    if not isinstance(raw, dict):
        return None
    try:
        return PytestCounts(
            passed=int(raw["passed"]),
            failed=int(raw["failed"]),
            skipped=int(raw["skipped"]),
            xfailed=int(raw["xfailed"]),
            xpassed=int(raw["xpassed"]),
            errors=int(raw["errors"]),
            warnings=int(raw["warnings"]),
            deselected=int(raw["deselected"]),
            duration_s=float(raw.get("duration_s", 0.0)),
            raw_summary_line=str(raw.get("raw_summary_line", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_atomic(path: Path, record: dict) -> None:
    """Writes via a temp file and os.replace, swallowing every failure.

    A full disk, a read-only mount, or a locked file must never propagate:
    this runs inside a hook where an exception is a nonzero exit.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(record, stream)
            os.replace(temp_name, path)
        except Exception:  # noqa: BLE001 - see docstring
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    except Exception:  # noqa: BLE001 - see docstring
        pass
