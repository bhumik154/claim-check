"""The end-of-turn claim check.

Declare-only in this milestone: it reports a finding and never blocks. The
gate is deliberately narrow, because a false positive here is far more
intrusive than one on `git commit`, where the user asked for an action and
expects a gate.

A finding is emitted only when ALL of these hold:
  - stop_hook_active is false (recursion guard)
  - the final assistant message asserts a claim, once quotations are stripped
  - fresh, same-session evidence exists from a real run the agent itself made
  - that run was NOT scope-narrowing
  - that run reported no errors
  - the claim contradicts it
Everything else is silence.
"""

import contextlib
import io
import json

import pytest

from claim_check import evidence
from claim_check.entrypoints import claude_stop_hook as hook

WHOLE_SUITE_OUTPUT = (
    "============================= test session starts =============================\n"
    "collected 21 items\n\n"
    "========================= 21 passed in 0.30s =========================\n"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("CLAIM_CHECK_VERBOSE", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    return root


def _transcript(tmp_path, text):
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps({
            "type": "assistant",
            "isSidechain": False,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }) + "\n",
        encoding="utf-8",
    )
    return path


def _run(project, tmp_path, message, *, command="python -m pytest",
         output=WHOLE_SUITE_OUTPUT, session="s1", record=True, **payload_extra):
    if record:
        evidence.record_run(project, session, command, output)
    payload = {
        "hook_event_name": "Stop",
        "session_id": session,
        "cwd": str(project),
        "transcript_path": str(_transcript(tmp_path, message)),
        "stop_hook_active": False,
    }
    payload.update(payload_extra)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hook.main(stdin_text=json.dumps(payload))
    return code, out.getvalue().strip()


def _message_of(stdout):
    return json.loads(stdout)["systemMessage"] if stdout else None


def test_a_contradicted_claim_produces_a_finding(project, tmp_path):
    code, out = _run(project, tmp_path, "All done. 22 passed.")
    assert code == 0
    message = _message_of(out)
    assert "22 passed" in message
    assert "21" in message


def test_a_finding_never_blocks(project, tmp_path):
    # Declare-only. A blocking Stop hook costs a full model turn and is
    # gated on a measured false-positive rate this milestone does not have.
    _, out = _run(project, tmp_path, "All done. 22 passed.")
    payload = json.loads(out)
    assert "decision" not in payload
    assert payload.get("continue", True) is not False


def test_a_true_claim_is_silent(project, tmp_path):
    code, out = _run(project, tmp_path, "All done. 21 passed.")
    assert code == 0
    assert out == ""


def test_a_turn_with_no_claim_is_silent(project, tmp_path):
    code, out = _run(project, tmp_path, "Refactored the loop and tidied imports.")
    assert code == 0
    assert out == ""


def test_quoted_output_alone_is_not_a_claim(project, tmp_path):
    # The measured 3% case: the agent showing output, not asserting a count.
    code, out = _run(project, tmp_path, "Here it is:\n\n```\n22 passed in 1.4s\n```\n")
    assert code == 0
    assert out == ""


def test_stop_hook_active_short_circuits_before_anything_else(project, tmp_path):
    # The recursion guard. Must be the first thing checked.
    code, out = _run(project, tmp_path, "All done. 22 passed.", stop_hook_active=True)
    assert code == 0
    assert out == ""


def test_no_evidence_means_silence_not_a_finding(project, tmp_path):
    # Unknown is never treated as wrong. This is the same asymmetry the
    # evidence store is built around.
    code, out = _run(project, tmp_path, "All done. 22 passed.", record=False)
    assert code == 0
    assert out == ""


def test_scoped_evidence_cannot_produce_a_finding(project, tmp_path):
    # `pytest -k thing` reports a true tally for a subset. Using it to
    # contradict a claim about the whole suite would be a false accusation.
    code, out = _run(project, tmp_path, "All done. 22 passed.", command="pytest -k thing")
    assert code == 0
    assert out == ""


def test_evidence_from_another_session_cannot_produce_a_finding(project, tmp_path):
    evidence.record_run(project, "other-session", "python -m pytest", WHOLE_SUITE_OUTPUT)
    code, out = _run(project, tmp_path, "All done. 22 passed.", record=False)
    assert code == 0
    assert out == ""


def test_stale_evidence_cannot_produce_a_finding(project, tmp_path):
    evidence.record_run(project, "s1", "python -m pytest", WHOLE_SUITE_OUTPUT)
    (project / "test_new.py").write_text("def test_n():\n    assert True\n", encoding="utf-8")
    code, out = _run(project, tmp_path, "All done. 22 passed.", record=False)
    assert code == 0
    assert out == ""


def test_a_run_reporting_errors_cannot_produce_a_finding(project, tmp_path):
    # An error means some test never ran, so the real denominator is unknown.
    # Blocking a commit on that is defensible; nagging in chat is not.
    output = (
        "============================= test session starts =============================\n"
        "===================== 21 passed, 1 error in 0.30s =====================\n"
    )
    code, out = _run(project, tmp_path, "All done. 22 passed.", output=output)
    assert code == 0
    assert out == ""


def test_every_finding_forbids_changing_code_to_fit_the_claim(project, tmp_path):
    # The one thing this must never encourage: an agent deleting an
    # assertion to make an earlier sentence true.
    _, out = _run(project, tmp_path, "All done. 22 passed.")
    assert hook.CORRECTION_INSTRUCTION in _message_of(out)


def test_no_finding_ever_tells_the_agent_to_change_tests_or_source():
    forbidden = ("fix the test", "fix the code", "make the tests pass",
                 "update the test", "change the test", "modify the source")
    lowered = hook.CORRECTION_INSTRUCTION.lower()
    for phrase in forbidden:
        assert phrase not in lowered or "do not" in lowered


def test_unverifiable_claims_are_reported_only_when_verbose_is_set(project, tmp_path, monkeypatch):
    # Silence on the common path is what keeps this installable. Around half
    # of claims have no usable evidence, so reporting each one would be a
    # steady stream of notes the user can do nothing about.
    monkeypatch.setenv("CLAIM_CHECK_VERBOSE", "1")
    code, out = _run(project, tmp_path, "All done. 22 passed.", record=False)
    assert code == 0
    assert "could not verify" in _message_of(out).lower()


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        "null",
        "[1, 2, 3]",
        json.dumps({"hook_event_name": "Stop"}),
        json.dumps({"transcript_path": None}),
        json.dumps({"transcript_path": 42}),
        json.dumps({"transcript_path": "/no/such/file.jsonl"}),
        json.dumps({"transcript_path": "/no/such/file.jsonl", "cwd": None}),
        json.dumps({"transcript_path": "/no/such/file.jsonl", "session_id": None}),
        json.dumps({"stop_hook_active": "yes please"}),
    ],
)
def test_malformed_payloads_never_raise_and_never_emit(payload):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hook.main(stdin_text=payload)
    assert code == 0
    assert out.getvalue().strip() == ""


def test_an_unexpected_internal_error_still_exits_zero(project, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic internal failure")

    monkeypatch.setattr(hook, "last_assistant_text", boom)
    code, out = _run(project, tmp_path, "All done. 22 passed.")
    assert code == 0
    assert out == ""


def test_the_hook_never_runs_pytest(project, tmp_path, monkeypatch):
    # It compares against observed evidence or stays quiet. Running the
    # suite at the end of every turn is what makes this unaffordable.
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("the Stop hook must never spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    code, _ = _run(project, tmp_path, "All done. 22 passed.")
    assert code == 0
