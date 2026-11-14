"""Which verification commands a session ran, and which provably failed.

A claim that `make check` passes is contradicted only when the session's most
recent run of exactly that command, against the current files, provably
failed. Proof is narrow on purpose:

- a failure line the claimed command could have printed, and no other command
  in the same Bash call could have (`echo`, `cat` of an old log, another
  tool, a script); or
- the call failed and the claimed command was the only thing it ran, so the
  exit code is that command's.

Real failures in the author's sessions show why: a failing call labelled
`npx tsc` had printed "TYPECHECK OK" (vitest failed), and every failing call
containing "make" was really a `gh` error. Blame decided only by "the call
failed and contained the command" would have been wrong every time.

Blame is decided when the call is recorded, while its output is at hand, so
only the verdict per command is stored, never the output. One small file per
call, so calls the agent runs in parallel never overwrite each other.
"""

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from .command_claims import _PROGRAMS
from .evidence import (
    _REDIRECT,
    _contains,
    _pipelines,
    _safe_session,
    _write_atomic,
    cache_root,
    evidence_path,
    fingerprint,
    project_root,
)
from .shell_parser import leading_cd_target, shell_tokens
from .evidence import parse_run_output
from .pytest_parser import strip_ansi
from .tool_failures import PRINTERS, locate_failures

KEEP = 40
"""Calls kept per (project, session). A claim is about the latest runs."""

_LINE_CAP = 300

# Print nothing that could be read as a tool's failure line.
_QUIET = frozenset({
    "cd", "pushd", "popd", "export", "set", "unset", "true", "false", ":", "mkdir", "rm",
    "cp", "mv", "touch", "sleep", "source", ".", "exit", "wait", "chmod", "ln",
})
# Pass their input through. After a pipe and with no file to read, they only
# relay what an earlier command printed; reading a file, they can print an
# old log's failure lines, so then they count as printers.
_RELAYS = frozenset({
    "tail", "head", "tee", "grep", "egrep", "fgrep", "rg", "sort", "uniq", "cut", "tr",
    "sed", "awk", "wc", "less", "more", "cat", "column", "nl",
})
# Tools that print only their own family's failure line.
_SPECIFIC = {"tsc": "tsc", "eslint": "eslint", "pytest": "tests", "py.test": "tests",
             "vitest": "tests", "jest": "tests"}
_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})
_ECHOERS = frozenset({"echo", "printf", "write-output", "write-host"})
_DROPPED_PREFIXES = frozenset({"npx", "bunx", "time", "env"})
_RUN_PREFIXES = {("pnpm", "exec"), ("pnpm", "dlx"), ("yarn", "dlx"), ("uv", "run"),
                 ("poetry", "run"), ("hatch", "run"), ("pdm", "run"), ("pipenv", "run")}
_SEPARATORS = frozenset({";", "&&", "||", "&"})
_PYTHONS = frozenset({"python", "python3", "py"})
# npm and pnpm announce each script they run: "> name@1.0.0 lint".
_SCRIPT_HEADER = re.compile(r"^> \S+@\S+ (?P<script>\S+)", re.MULTILINE)


def run_dir(project_dir, session_id) -> Path:
    """Where one session's calls in one project are kept."""
    path = evidence_path(project_dir, session_id)
    return path.with_name(f"{_safe_session(session_id)}.runs")


def normalized(tokens) -> tuple:
    """A command stage reduced to what identifies it.

    Redirects, environment assignments and launchers (`npx`, `uv run`) are
    dropped, `python -m X` is X, and `npm run test` is `npm test`, so the
    way a claim names a command and the way it was run can be compared.
    """
    words = [t for t in tokens if not _REDIRECT.match(t)]
    while words and ("=" in words[0] and not words[0].startswith("-") or words[0] in _DROPPED_PREFIXES):
        words = words[1:]
    while len(words) >= 2 and (words[0], words[1]) in _RUN_PREFIXES:
        words = words[2:]
    if not words:
        return ()
    program = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if program.endswith(".exe"):
        program = program[:-4]
    rest = words[1:]
    if program in _PYTHONS and len(rest) >= 2 and rest[0] == "-m":
        program, rest = rest[1], rest[2:]
    if program in _PACKAGE_MANAGERS and rest and rest[0] in ("run", "run-script"):
        rest = rest[1:]
    return (program, *rest)


def record_call(project_dir, session_id, command, output, outcome, exit_code=None,
                cd_ok: Optional[bool] = None, blame: bool = True) -> Optional[dict]:
    """Records one Bash call if it ran a verification command.

    `outcome` is "ok", "failed" or "interrupted". `cd_ok` says whether a
    leading `cd` reached its directory; None works it out for an absolute
    path and otherwise assumes it did not. With `blame` False the call is
    recorded as the latest run of its commands and blamed for nothing: that
    is how a PowerShell call is kept, since its syntax is not bash's. Returns
    the stored record, or None when nothing in the call verifies anything.
    Never raises.
    """
    try:
        return _record(project_dir, session_id, command, output, outcome, exit_code, cd_ok, blame)
    except Exception:  # noqa: BLE001 - recording must never disturb the call
        return None


def _record(project_dir, session_id, command, output, outcome, exit_code, cd_ok, blame):
    if not isinstance(command, str):
        return None
    stages = _stages(command)
    if not any(s["normal"] and s["normal"][0] in _PROGRAMS for s in stages):
        return None

    failures = {}
    if blame and outcome != "interrupted":
        failures = _blame(stages, output if isinstance(output, str) else "", command)
        sole = _sole_stage(stages, command, cd_ok)
        if outcome == "failed" and isinstance(exit_code, int) and sole is not None:
            failures.setdefault(sole, [f"exit code {exit_code}"])
        if outcome == "ok":
            # Whatever these printed, they exited 0. A test that logs a
            # compiler diagnostic once made a passing `npm test` look failed.
            for index in _proven_ok(command):
                failures.pop(index, None)

    record = {
        "recorded_at": time.time(),
        "session_id": str(session_id),
        "cwd": str(Path(project_dir).resolve()),
        "command": command[:1000],
        "stages": [list(s["normal"]) for s in stages],
        "outcome": outcome,
        "exit_code": exit_code,
        "failures": {str(k): v for k, v in failures.items()},
        "fingerprint": fingerprint(project_dir),
    }
    folder = run_dir(project_dir, session_id)
    name = f"{time.time_ns():020d}-{os.getpid()}-{uuid.uuid4().hex[:6]}.json"
    _write_atomic(folder / name, record)
    _prune(folder)
    return record


def _stages(command: str) -> list:
    stages = []
    for pipeline in _pipelines(shell_tokens(command)):
        for position, tokens in enumerate(pipeline):
            stages.append({"tokens": tokens, "normal": normalized(tokens), "after_pipe": position > 0})
    return stages


def _could_print(stage, family: str, line: str) -> bool:
    if not stage["normal"]:
        return False
    program, args = stage["normal"][0], stage["normal"][1:]
    if program in _QUIET:
        return False
    if program in _ECHOERS:
        # `echo TYPECHECK OK` prints only its own words. With an expansion,
        # a printf format or an escape in them ("error TS%d", "\x65rror")
        # it could print anything.
        said = " ".join(args)
        return any(mark in said for mark in "$`%\\") or line.strip() in said
    if program in _RELAYS:
        reads_a_file = any(not a.startswith("-") and not a.isdigit() for a in args
                           if program not in ("grep", "egrep", "fgrep", "rg", "sed", "awk") or a != args[0])
        return reads_a_file or not stage["after_pipe"]
    if program in _SPECIFIC:
        return _SPECIFIC[program] == family
    if program == "go":
        return family == "go test" or (bool(args) and args[0] in ("run", "generate"))
    if program in PRINTERS.get(family, ()):
        return True
    # make, npm run, a script, an unknown program: could print anything.
    return True


def _blame(stages, output: str, command: str) -> dict:
    """Failure lines per stage index, only where the stage is provably the
    one that printed them."""
    blamed = {}
    headers = [(m.start(), m.group("script")) for m in _SCRIPT_HEADER.finditer(output)]
    kept_going_to = _passing_summary_at(output, command)
    for family, line, offset in locate_failures(output, command):
        candidates = [i for i, s in enumerate(stages) if _could_print(s, family, line)]
        target = None
        if len(candidates) == 1:
            target = candidates[0]
        elif candidates and all(stages[i]["normal"][0] in _PACKAGE_MANAGERS for i in candidates):
            # Several package-manager scripts: the nearest "> pkg@1.0.0 name"
            # header above the line says which one printed it, but only when
            # every one of their headers is there, in the order they ran. A
            # `| tail` that cut a header off moves the line under the wrong
            # one: a real lint error was once blamed on `npm run build`.
            above = [script for start, script in headers if start <= offset]
            expected = [stages[i]["normal"][1] if len(stages[i]["normal"]) > 1 else "" for i in candidates]
            if above and [script for _start, script in headers] == expected:
                owners = [i for i in candidates if stages[i]["normal"][1:2] == (above[-1],)]
                if len(owners) == 1:
                    target = owners[0]
        if target is not None and offset < kept_going_to and _is_wrapper(stages[target], family):
            # A wrapper that went on to a passing test summary did not stop
            # at this line: a test logged it, or the script ignored it.
            target = None
        if target is not None:
            blamed.setdefault(target, []).append(line[:_LINE_CAP])
    # The command's own family first: "make: *** ..." before the go lines under it.
    for index, lines in blamed.items():
        program = stages[index]["normal"][0]
        own = [l for l in lines if program in ("make", "gmake") and l.startswith(("make", "gmake"))]
        blamed[index] = (own + [l for l in lines if l not in own])[:5]
    return blamed


def _passing_summary_at(output: str, command: str) -> int:
    """Where a test runner's summary reporting every test passed sits in the
    output (ANSI stripped, as failure offsets are), or -1."""
    text = strip_ansi(output)
    counts = parse_run_output(text, command or "")
    if counts is None or counts.failed or counts.errors or not counts.passed:
        return -1
    return text.rfind(counts.raw_summary_line.strip())


def _is_wrapper(stage, family: str) -> bool:
    """A command that runs other tools: make, a package manager, a script.
    Not the tool that prints this family's line itself."""
    program = stage["normal"][0]
    return _SPECIFIC.get(program) != family and program not in PRINTERS.get(family, ())


def _sole_stage(stages, command: str, cd_ok: Optional[bool]) -> Optional[int]:
    """The index of the only real command in the call, if it ran alone and
    unpiped after at most a successful `cd`; otherwise None."""
    pipelines = _pipelines(shell_tokens(command))
    if pipelines and len(pipelines[0]) == 1 and pipelines[0][0][:1] == ["cd"]:
        if cd_ok is None:
            target = leading_cd_target(command)
            cd_ok = bool(target) and Path(target).is_absolute() and Path(target).is_dir()
        if not cd_ok:
            return None
        pipelines = pipelines[1:]
    if len(pipelines) != 1 or len(pipelines[0]) != 1:
        return None
    return len(stages) - 1


def _proven_ok(command: str) -> set:
    """Stage indexes, as `_stages` numbers them, that a call's exit 0 proves
    exited 0 too.

    The call's status is its last pipeline's, and that pipeline's is its last
    stage's (Claude Code's shell has no pipefail). So exit 0 proves the last
    pipeline when it is one command, and every one-command pipeline joined to
    it by an unbroken `&&` chain, since each of those had to succeed for the
    chain to get there. `;`, `||` and `&` break the chain; anything in
    brackets proves nothing.
    """
    links = []  # [stage count, separator after]
    stage_open = False
    stages = 0
    for token in shell_tokens(command):
        if token in ("(", ")"):
            return set()
        if token in _SEPARATORS:
            stages += stage_open
            if stages:
                links.append([stages, token])
            stages, stage_open = 0, False
        elif token in ("|", "|&"):
            stages += stage_open
            stage_open = False
        else:
            stage_open = True
    stages += stage_open
    if stages:
        links.append([stages, None])
    if not links or links[-1][1] == "&":
        return set()

    first = []
    total = 0
    for count, _separator in links:
        first.append(total)
        total += count
    proven = set()
    index = len(links) - 1
    while index >= 0:
        if links[index][0] == 1:
            proven.add(first[index])
        if index == 0 or links[index - 1][1] != "&&":
            break
        index -= 1
    return proven


def _prune(folder: Path) -> None:
    try:
        entries = sorted(folder.glob("*.json"))
        for stale in entries[:-KEEP]:
            stale.unlink()
    except OSError:
        pass


def contradiction(command, cwd, session_id) -> Optional[str]:
    """The line proving `command` failed in its most recent run in this
    session, against the current files; None when that cannot be shown.

    None means unknown, never "it passed": no run, a later passing run, a run
    whose failure cannot be pinned on this command, an interrupted run, files
    changed since, or matching runs in more than one project.
    """
    try:
        return _contradiction(command, cwd, session_id)
    except Exception:  # noqa: BLE001 - a hook must never fail on this
        return None


def _contradiction(command, cwd, session_id):
    claimed = [normalized(stage) for pipeline in _pipelines(shell_tokens(command)) for stage in pipeline]
    if len(claimed) != 1 or not claimed[0]:
        return None
    claim = claimed[0]

    latest = None
    folders = set()
    for record in _records_nearby(cwd, session_id):
        index = _matching_stage(record, claim)
        if index is None:
            continue
        folders.add(os.path.normcase(record["cwd"]))
        if latest is None or record["recorded_at"] >= latest[0]["recorded_at"]:
            latest = (record, index)
    if latest is None or len(folders) != 1:
        return None

    record, index = latest
    if record.get("outcome") == "interrupted":
        return None
    if record.get("fingerprint") != fingerprint(record["cwd"]):
        return None
    lines = (record.get("failures") or {}).get(str(index))
    return lines[0] if lines else None


def _matching_stage(record, claim) -> Optional[int]:
    found = None
    for index, stage in enumerate(record.get("stages") or []):
        stage = tuple(stage)
        if stage == claim or (len(claim) == 1 and stage[:1] == claim):
            found = index
    return found


def _records_nearby(cwd, session_id):
    """This session's records for `cwd` and for projects nested with it,
    under the same rules as test-run evidence: inside a project, only that
    project's own runs."""
    try:
        here = Path(cwd).resolve()
        folders = list(cache_root().glob(f"*/{_safe_session(session_id)}.runs"))
    except (OSError, ValueError):
        return
    home = project_root(here)
    for folder in folders:
        for path in sorted(folder.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                there = Path(record["cwd"])
                record["recorded_at"] = float(record["recorded_at"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if not (_contains(here, there) or _contains(there, here)):
                break
            if home is not None and project_root(there) != home:
                break
            yield record
