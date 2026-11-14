"""Claude Code PostToolUseFailure hook: records failed verification runs.

Claude Code 2.1.274 sends a failed Bash call here and never to PostToolUse,
so until this hook existed claim-check had never seen a command fail.
"""

import io
import contextlib
import json
from pathlib import Path

import pytest

from claim_check import run_log
from claim_check.entrypoints import claude_posttoolusefailure_hook as hook

OUTPUTS = Path(__file__).parent / "fixtures" / "tool_outputs"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "Makefile").write_text("check:\n\tgo test ./...\n", encoding="utf-8")
    return root


def run(payload):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hook.main(stdin_text=json.dumps(payload))
    return code, out.getvalue()


def payload(project, command, error, interrupt=False):
    return {
        "hook_event_name": "PostToolUseFailure", "session_id": "s1", "cwd": str(project),
        "tool_name": "Bash", "tool_input": {"command": command}, "tool_use_id": "t1",
        "error": error, "is_interrupt": interrupt, "duration_ms": 1200,
    }


def test_a_failed_run_is_recorded_and_blamed(project):
    error = "Exit code 2\n" + (OUTPUTS / "make-check-fail.txt").read_text(encoding="utf-8")
    code, out = run(payload(project, "make check", error))
    assert (code, out) == (0, "")
    assert run_log.contradiction("make check", project, "s1").startswith("make: ***")


def test_the_exit_code_alone_blames_a_sole_command(project):
    code, _ = run(payload(project, f'cd "{project}" && go vet ./...', "Exit code 1\npkg/vet.go:5:12: bad\n"))
    assert code == 0
    assert run_log.contradiction("go vet ./...", project, "s1") == "exit code 1"


def test_an_interrupted_run_blames_nothing(project):
    error = "Exit code 130\n" + (OUTPUTS / "make-check-fail.txt").read_text(encoding="utf-8")
    run(payload(project, "make check", error, interrupt=True))
    assert run_log.contradiction("make check", project, "s1") is None


@pytest.mark.parametrize("bad", [
    "not json", "[]", json.dumps({"tool_name": "Bash"}),
    json.dumps({"tool_name": "Read", "error": "Exit code 1\n"}),
    json.dumps({"tool_name": "Bash", "tool_input": {"command": 7}, "error": "x"}),
    json.dumps({"tool_name": "Bash", "tool_input": {"command": "make check"}, "error": None}),
])
def test_malformed_payloads_never_raise_and_never_emit(bad):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert hook.main(stdin_text=bad) == 0
    assert out.getvalue() == ""
