import json

from claim_check.entrypoints import claude_hook as claude_hook_module
from claim_check.entrypoints.claude_hook import main
from claim_check.runner import result_from_captured_output


def _payload(command, tool_name="Bash", cwd="."):
    return json.dumps(
        {
            "session_id": "abc123",
            "cwd": cwd,
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"command": command},
        }
    )


def test_non_bash_tool_is_ignored_exits_zero(capsys):
    code = main(json.dumps({"tool_name": "Write", "tool_input": {}}))
    assert code == 0
    assert capsys.readouterr().out == ""


def test_bash_command_that_is_not_a_git_commit_is_ignored(capsys):
    code = main(_payload("npm test"))
    assert code == 0
    assert capsys.readouterr().out == ""


def test_git_commit_with_no_message_flag_is_ignored(capsys):
    code = main(_payload("git commit"))
    assert code == 0
    assert capsys.readouterr().out == ""


def test_malformed_json_on_stdin_fails_open_without_crashing():
    assert main("not valid json {{{") == 0


def test_shell_parse_failure_fails_open_without_denying(capsys):
    code = main(_payload('git commit -m "unterminated'))
    assert code == 0
    assert capsys.readouterr().out == ""


def test_matching_claim_allows_silently(monkeypatch, capsys):
    monkeypatch.setattr(
        claude_hook_module,
        "run_pytest",
        lambda cwd: result_from_captured_output(0, "============ 22 passed in 0.15s ============"),
    )
    code = main(_payload('git commit -m "22 passed"'))
    assert code == 0
    assert capsys.readouterr().out == ""


def test_mismatched_claim_denies_via_exact_json_shape(monkeypatch, capsys):
    monkeypatch.setattr(
        claude_hook_module,
        "run_pytest",
        lambda cwd: result_from_captured_output(0, "============ 21 passed in 0.15s ============"),
    )
    code = main(_payload('git commit -m "22 passed"'))
    assert code == 0

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": 'claimed "22 passed" but 21 actually passed',
        }
    }


def test_runner_error_fails_open_does_not_deny(monkeypatch, capsys):
    monkeypatch.setattr(
        claude_hook_module,
        "run_pytest",
        lambda cwd: result_from_captured_output(0, "INTERNALERROR> something broke"),
    )
    code = main(_payload('git commit -m "22 passed"'))
    assert code == 0
    assert capsys.readouterr().out == ""


def test_handles_the_exact_heredoc_pattern_this_environment_uses(monkeypatch, capsys):
    monkeypatch.setattr(
        claude_hook_module,
        "run_pytest",
        lambda cwd: result_from_captured_output(0, "============ 21 passed in 0.15s ============"),
    )
    command = (
        "git commit -m \"$(cat <<'EOF'\n"
        "Fix the scale-invariance bug\n"
        "\n"
        "22 passed\n"
        "EOF\n"
        ")\""
    )
    code = main(_payload(command))
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "22 passed" in output["hookSpecificOutput"]["permissionDecisionReason"]
