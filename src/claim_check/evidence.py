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

from .models import PytestCounts, RunScope
from .pytest_parser import bare_summary_lines, parse_summary_line
from .runners import parse_test_output

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

# Every runner narrows scope with its own flags, so the guard has to know
# which runner it is looking at. Getting this wrong in the permissive
# direction is the expensive mistake: a filtered run recorded as whole-suite
# evidence can confirm a claim that a full run would have contradicted, which
# is worse than not supporting the runner at all.
_VITEST_NARROWING = frozenset(
    {
        "-t",
        "--testNamePattern",
        "--changed",
        "--related",
        "--bail",
        "--shard",
        "--project",
        "--dir",
    }
)
_VITEST_VALUE_TAKING = frozenset(
    {"-t", "--testNamePattern", "--bail", "--shard", "--project", "--dir",
     "--reporter", "-c", "--config", "--root", "--coverage.reporter"}
)
_VITEST_BARE = frozenset(
    {"npx", "npm", "yarn", "pnpm", "bun", "bunx", "exec", "run", "vitest", "dlx"}
)

_JEST_NARROWING = frozenset(
    {
        "-t",
        "--testNamePattern",
        "--testPathPattern",
        "--testPathPatterns",
        "-o",
        "--onlyChanged",
        "--onlyFailures",
        "--changedSince",
        "--lastCommit",
        "--findRelatedTests",
        "--bail",
        "--shard",
        "-f",
    }
)
_JEST_VALUE_TAKING = frozenset(
    {"-t", "--testNamePattern", "--testPathPattern", "--testPathPatterns",
     "--shard", "--changedSince", "-c", "--config", "--maxWorkers", "-w",
     "--reporters", "--testTimeout", "--selectProjects"}
)
_JEST_BARE = frozenset(
    {"npx", "npm", "yarn", "pnpm", "bun", "bunx", "exec", "run", "jest", "dlx"}
)

# name -> (how to spot it, narrowing flags, value-taking flags, allowed bare tokens)
_RUNNERS = {
    "pytest": (
        lambda t: t == "pytest" or t.endswith("pytest") or t.endswith("py.test"),
        _SCOPE_NARROWING_FLAGS,
        _VALUE_TAKING_FLAGS,
        _RUNNER_TOKENS,
    ),
    "vitest": (
        lambda t: t == "vitest" or t.endswith("/vitest"),
        _VITEST_NARROWING,
        _VITEST_VALUE_TAKING,
        _VITEST_BARE,
    ),
    "jest": (
        lambda t: t == "jest" or t.endswith("/jest"),
        _JEST_NARROWING,
        _JEST_VALUE_TAKING,
        _JEST_BARE,
    ),
}


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
    return cache_root() / digest / f"{_safe_session(session_id)}.json"


def _safe_session(session_id) -> str:
    return "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64] or "unknown"


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

    found = []
    for segment in segments:
        for name, (matches, _narrowing, _values, _bare) in _RUNNERS.items():
            if any(matches(token) for token in segment):
                found.append((name, segment))
                break
    return found


def is_scoped(argv: Sequence[str], unknown_runner_is_scoped: bool = True) -> bool:
    """True if this invocation ran less than the whole suite.

    Biased towards True by default. Anything unrecognised, unparseable, or
    ambiguous is reported as scoped, because scoped evidence is merely
    unusable while wrongly-unscoped evidence can confirm a false whole-suite
    claim.

    `unknown_runner_is_scoped=False` inverts that one bias, and the
    distinction is between commands the agent *typed* and commands the user
    *declared*. For an observed command, an unrecognised runner really is
    unknown scope, and abstaining costs a nice-to-have. For a declared one
    it is not: `--command "hatch run test"` and `--command "make test"` are
    both recommended in the README and contain no recognisable runner, so
    keeping the default bias would give every hatch, poetry-script and make
    user zero verification, forever, with no error to read. That is worse
    than the false positive this whole mechanism exists to remove.
    """
    if not argv:
        return unknown_runner_is_scoped

    runner_segments = _pytest_segments(argv)
    if not runner_segments:
        # No recognisable runner. "npm test" or "make test" may well run
        # everything, but nothing here can establish that.
        return unknown_runner_is_scoped

    return any(_segment_is_scoped(name, segment) for name, segment in runner_segments)


def scope_of_run(result) -> "RunScope":
    """What a finished run actually covered, from its own invocation.

    Lives here rather than beside the runner because it needs `is_scoped`,
    and `runners.py` cannot import this module without a cycle.

    The declared command and the extra arguments are inspected together: a
    filter can live in either, and `--command` is the larger of the two
    uninspected vectors since the README actively tells people to set it.

    Deliberately does NOT treat a non-default `cwd` as narrowing. The
    PreToolUse hook always passes a cwd, so that rule would mark every run
    narrowed and abstain always. Directory-level narrowing therefore remains
    undetected; see the README's scope section.
    """
    tokens = []
    command = getattr(result, "command", None)
    if isinstance(command, str) and command.strip():
        try:
            tokens.extend(shlex.split(command, posix=True))
        except ValueError:
            # An unparseable declared command tells us nothing either way.
            # Guessing "narrowed" here would abstain on a typo.
            return RunScope(narrowed=False)
    else:
        # No override means run_pytest used its default. The runner is
        # implied rather than written down, and without naming it the extra
        # arguments cannot be read under any runner's rules.
        tokens.extend(["python", "-m", "pytest"])
    tokens.extend(str(token) for token in (getattr(result, "argv", ()) or ()))

    if not tokens:
        return RunScope(narrowed=False)

    causes = [
        _segment_narrowing_cause(name, segment)
        for name, segment in _pytest_segments(tokens)
    ]
    cause = next((c for c in causes if c), None)
    if cause is None:
        return RunScope(narrowed=False)

    return RunScope(
        narrowed=True,
        reason=f"this run was narrowed by {cause!r}, so it covered less than the whole suite",
    )


def _segment_is_scoped(runner: str, tokens: list) -> bool:
    return _segment_narrowing_cause(runner, tokens) is not None


def _segment_narrowing_cause(runner: str, tokens: list):
    """The token that narrowed this run, or None if nothing did."""
    _matches, narrowing, value_taking, bare_allowed = _RUNNERS[runner]
    tokens = list(tokens)

    if runner == "pytest":
        # "-m" is ambiguous: for `python -m pytest` it is the interpreter's
        # module flag, but for `pytest -m integration` it is a marker filter
        # that narrows collection. Only the second meaning implies scope, so
        # drop the interpreter form before parsing. Without this, every
        # ordinary `python -m pytest` run was marked scoped and no evidence
        # was ever usable - confirmed directly against a live session.
        for position in range(len(tokens) - 1):
            if tokens[position] == "-m" and tokens[position + 1] in ("pytest", "py.test"):
                del tokens[position : position + 2]
                break

    index = 0
    while index < len(tokens):
        token = tokens[index]

        if token in narrowing:
            return token
        if token.startswith("--") and "=" in token and token.split("=", 1)[0] in narrowing:
            return token
        if runner == "pytest":
            # Bundled short flags such as "-xvs" carry -x inside them. Only
            # pytest bundles them this way in practice.
            if token.startswith("-") and not token.startswith("--") and len(token) > 1:
                if "x" in token[1:] and not token.startswith("-x="):
                    return token

        if token in value_taking:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue

        # A bare token. Words belonging to the invocation are expected;
        # anything else is a path, a node id or a test-name filter, all of
        # which narrow what runs.
        if token not in bare_allowed and not _RUNNERS[runner][0](token):
            return token
        index += 1

    return None


def parse_run_output(stdout, command) -> Optional[PytestCounts]:
    """Counts from a shell command's output, or None if it was not a run.

    The Bash tool hands over everything the command printed, not just
    pytest's part. `pytest -q` prints its tally undecorated, and the strict
    parser only trusts that form on the final line, so `pytest -q; echo
    exit=$?` or `... && ls` lost the run entirely. Measured across real
    sessions, 62 of 232 runs went unrecorded that way, silently.

    A quiet summary that is not last is accepted only when two things hold.
    The command must actually invoke pytest, so `cat old.log` holding a tally
    is not mistaken for a run. And the output must hold exactly one
    tally-shaped line: with several, there is no telling which belongs to this
    project, and wrong evidence is worse than none because it can confirm a
    false claim or contradict a true one.
    """
    counts = parse_test_output(stdout)
    if counts is not None:
        return counts

    try:
        tokens = shlex.split(command, posix=True) if isinstance(command, str) else []
    except ValueError:
        return None
    if not any(name == "pytest" for name, _segment in _pytest_segments(tokens)):
        return None

    tallies = bare_summary_lines(stdout)
    if len(tallies) != 1:
        return None
    return parse_summary_line(tallies[0] + "\n")


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
    counts = parse_run_output(stdout, command)
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


def load_fresh_nearby(cwd, session_id: str, now: Optional[float] = None) -> Optional[dict]:
    """Fresh evidence for `cwd`, or for one project nested with it.

    Evidence is filed under the directory a run happened in, but the end of
    a turn is checked from wherever the Bash tool happens to be, and that
    moves. Measured across real sessions, 13 of 57 claims looked in the wrong
    directory, and in every one of them one directory was inside the other:
    usually the run inside a project, the claim from its parent folder.

    So when nothing is filed under `cwd` itself, this session's runs in an
    ancestor or descendant directory are considered, each judged fresh for
    its OWN directory. Exactly one such directory is used. Several means the
    session worked in more than one nested project, and picking one could
    check a claim about one project against another's suite; that resolves
    to None, as does anything in an unrelated directory. Unknown, never wrong.
    """
    direct = load_fresh(cwd, session_id, now=now)
    if direct is not None:
        return direct

    try:
        here = Path(cwd).resolve()
        candidates = list(cache_root().glob(f"*/{_safe_session(session_id)}.json"))
    except (OSError, ValueError):
        return None

    found = {}
    for path in candidates:
        try:
            stored = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            there = Path(stored["cwd"]).resolve()
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not (_contains(here, there) or _contains(there, here)):
            continue
        record = load_fresh(there, session_id, now=now)
        if record is not None:
            found[os.path.normcase(str(there))] = record

    if len(found) == 1:
        return next(iter(found.values()))
    return None


def _contains(outer: Path, inner: Path) -> bool:
    """True when `inner` is `outer` or lies inside it, ignoring case on Windows."""
    outer_s, inner_s = os.path.normcase(str(outer)), os.path.normcase(str(inner))
    return inner_s == outer_s or inner_s.startswith(outer_s.rstrip(os.sep) + os.sep)


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
