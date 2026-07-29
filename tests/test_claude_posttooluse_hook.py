"""PostToolUse observer: records real pytest runs, emits nothing, blocks
nothing, and never raises."""

import io
import json
import contextlib

import pytest

from claim_check import evidence
from claim_check.entrypoints import claude_posttooluse_hook as hook

PASSING_OUTPUT = (
    "============================= test session starts =============================\n"
    "collected 22 items\n\n"
    "========================= 22 passed in 0.30s =========================\n"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    return root


def _payload(project, command="python -m pytest", stdout=PASSING_OUTPUT, key="tool_response"):
    return {
        "hook_event_name": "PostToolUse",
        "session_id": "s1",
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_input": {"command": command},
        key: {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False},
    }


def _run(payload_obj):
    """Returns (exit_code, stdout_text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hook.main(stdin_text=json.dumps(payload_obj) if not isinstance(payload_obj, str) else payload_obj)
    return code, out.getvalue()


def test_a_real_pytest_run_is_recorded_as_evidence(project):
    code, out = _run(_payload(project))
    assert code == 0
    assert out == ""
    record = evidence.load_fresh(project, "s1")
    assert record is not None
    assert evidence.counts_from_record(record).passed == 22
    assert record["scoped"] is False


def test_both_documented_result_field_names_produce_identical_evidence(project, tmp_path, monkeypatch):
    # The official plugin docs and their own sample generator disagree:
    # shipping hook code reads `tool_response`, the sample generator emits
    # `tool_result`. Whichever a given Claude Code version sends must work.
    _run(_payload(project, key="tool_response"))
    from_response = evidence.load_fresh(project, "s1")["counts"]

    evidence.evidence_path(project, "s1").unlink()
    _run(_payload(project, key="tool_result"))
    from_result = evidence.load_fresh(project, "s1")["counts"]

    assert from_response == from_result


def test_a_bare_string_result_is_also_accepted(project):
    payload = _payload(project)
    del payload["tool_response"]
    payload["tool_result"] = PASSING_OUTPUT
    code, _ = _run(payload)
    assert code == 0
    assert evidence.load_fresh(project, "s1") is not None


def test_a_scoped_run_is_recorded_but_marked_scoped(project):
    # The documented -k hazard: "1 passed, 1 deselected" is a true summary
    # for a subset. Recorded, but never usable to confirm a whole-suite
    # claim - treating it as such would automate a false confirmation, which
    # is worse than not checking at all.
    _run(_payload(project, command="pytest -k something"))
    assert evidence.load_fresh(project, "s1")["scoped"] is True


def test_a_non_test_bash_call_records_nothing(project):
    code, _ = _run(_payload(project, command="ls -la", stdout="total 0\n"))
    assert code == 0
    assert evidence.load_fresh(project, "s1") is None


def test_a_non_bash_tool_call_records_nothing(project):
    payload = _payload(project)
    payload["tool_name"] = "Write"
    _run(payload)
    assert evidence.load_fresh(project, "s1") is None


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        "null",
        "[1, 2, 3]",
        json.dumps({"tool_name": "Bash", "tool_input": None}),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": None}}),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": 42}}),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest"}, "tool_response": None}),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest"}, "tool_response": {"stdout": 7}}),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest"}, "cwd": None,
                    "tool_response": {"stdout": PASSING_OUTPUT}}),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest"}, "cwd": 7,
                    "tool_response": {"stdout": PASSING_OUTPUT}}),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest"}, "session_id": None,
                    "tool_response": {"stdout": PASSING_OUTPUT}}),
    ],
)
def test_malformed_payloads_never_raise_and_never_emit(payload, project):
    code, out = _run(payload)
    assert code == 0
    assert out == ""


def test_an_unexpected_internal_error_still_exits_zero(project, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic internal failure")

    monkeypatch.setattr(hook, "record_run", boom)
    code, out = _run(_payload(project))
    assert code == 0
    assert out == ""
