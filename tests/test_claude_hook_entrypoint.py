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
    code = main(json.dumps({"tool_name": "Write", "tool_input": {}}), argv=[])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_bash_command_that_is_not_a_git_commit_is_ignored(capsys):
    code = main(_payload("npm test"), argv=[])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_git_commit_with_no_message_flag_is_ignored(capsys):
    code = main(_payload("git commit"), argv=[])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_malformed_json_on_stdin_fails_open_without_crashing():
    assert main("not valid json {{{", argv=[]) == 0


def test_shell_parse_failure_fails_open_without_denying(capsys):
    code = main(_payload('git commit -m "unterminated'), argv=[])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_matching_claim_allows_silently(monkeypatch, capsys):
    monkeypatch.setattr(
        claude_hook_module,
        "run_pytest",
        lambda cwd, **kwargs: result_from_captured_output(0, "============ 22 passed in 0.15s ============"),
    )
    code = main(_payload('git commit -m "22 passed"'), argv=[])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_mismatched_claim_denies_via_exact_json_shape(monkeypatch, capsys):
    monkeypatch.setattr(
        claude_hook_module,
        "run_pytest",
        lambda cwd, **kwargs: result_from_captured_output(0, "============ 21 passed in 0.15s ============"),
    )
    code = main(_payload('git commit -m "22 passed"'), argv=[])
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
        lambda cwd, **kwargs: result_from_captured_output(0, "INTERNALERROR> something broke"),
    )
    code = main(_payload('git commit -m "22 passed"'), argv=[])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_runner_error_warning_goes_to_stderr_not_stdout(monkeypatch, capsys):
    # Claude Code parses stdout as JSON on exit 0; a plain-text warning
    # mixed into stdout would break that contract, so it must go to stderr.
    monkeypatch.setattr(
        claude_hook_module,
        "run_pytest",
        lambda cwd, **kwargs: result_from_captured_output(0, "INTERNALERROR> something broke"),
    )
    code = main(_payload('git commit -m "22 passed"'), argv=[])
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not verify" in captured.err


def test_handles_the_exact_heredoc_pattern_this_environment_uses(monkeypatch, capsys):
    monkeypatch.setattr(
        claude_hook_module,
        "run_pytest",
        lambda cwd, **kwargs: result_from_captured_output(0, "============ 21 passed in 0.15s ============"),
    )
    command = (
        "git commit -m \"$(cat <<'EOF'\n"
        "Fix the scale-invariance bug\n"
        "\n"
        "22 passed\n"
        "EOF\n"
        ")\""
    )
    code = main(_payload(command), argv=[])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "22 passed" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_command_override_is_forwarded_to_run_pytest(monkeypatch, capsys):
    # Confirms the --command flag actually reaches run_pytest, not just
    # that argparse accepts it.
    seen = {}

    def fake_run_pytest(cwd, **kwargs):
        seen.update(kwargs)
        return result_from_captured_output(0, "============ 22 passed in 0.15s ============")

    monkeypatch.setattr(claude_hook_module, "run_pytest", fake_run_pytest)
    code = main(_payload('git commit -m "22 passed"'), argv=["--command", "poetry run pytest"])
    assert code == 0
    assert seen["command"] == "poetry run pytest"


def test_interactive_stdin_exits_immediately_instead_of_hanging(monkeypatch):
    # Claude Code always pipes the hook JSON in (a piped stdin reports
    # isatty() == False, confirmed directly), so this never fires in real
    # use. It guards against a developer running the bare command in an
    # interactive terminal, which would otherwise block on stdin.read()
    # waiting for input that never arrives. Faking isatty() == True and
    # making read() raise if called proves the read is skipped entirely,
    # not just that the eventual result happens to be 0.
    class _FakeTTYStdin:
        def isatty(self):
            return True

        def read(self):
            raise AssertionError("stdin.read() must not be called when isatty() is True")

    monkeypatch.setattr(claude_hook_module.sys, "stdin", _FakeTTYStdin())
    code = main(argv=[])
    assert code == 0


def test_timeout_override_is_forwarded_to_run_pytest(monkeypatch):
    seen = {}

    def fake_run_pytest(cwd, **kwargs):
        seen.update(kwargs)
        return result_from_captured_output(0, "============ 22 passed in 0.15s ============")

    monkeypatch.setattr(claude_hook_module, "run_pytest", fake_run_pytest)
    main(_payload('git commit -m "22 passed"'), argv=["--timeout", "5"])
    assert seen["timeout_s"] == 5.0


def test_malformed_tool_input_shapes_all_allow_instead_of_crashing():
    # The hook did payload.get("tool_input", {}).get("command", "") with no
    # type guard: tool_input=null raised AttributeError, and a non-string
    # command raised TypeError inside the shell parser. The module docstring
    # promises it "fails open at every uncertain step"; it did not.
    from claim_check.entrypoints import claude_hook

    payloads = [
        {"tool_name": "Bash", "tool_input": None},
        {"tool_name": "Bash", "tool_input": "git commit -m '22 passed'"},
        {"tool_name": "Bash", "tool_input": [1, 2]},
        {"tool_name": "Bash", "tool_input": {"command": None}},
        {"tool_name": "Bash", "tool_input": {"command": 42}},
        {"tool_name": "Bash", "tool_input": {"command": ["git", "commit"]}},
    ]
    for payload in payloads:
        assert claude_hook.main(stdin_text=json.dumps(payload), argv=[]) == 0


def test_null_or_missing_cwd_falls_back_instead_of_crashing(tmp_path):
    # payload.get("cwd", ".") returns None when the key is present and null,
    # which reached subprocess as the literal directory "None".
    from claim_check.entrypoints import claude_hook

    payload = {
        "tool_name": "Bash",
        "cwd": None,
        "tool_input": {"command": "git commit -m 'chore: no claim here'"},
    }
    assert claude_hook.main(stdin_text=json.dumps(payload), argv=[]) == 0


def test_nonexistent_cwd_allows_instead_of_crashing():
    from claim_check.entrypoints import claude_hook

    payload = {
        "tool_name": "Bash",
        "cwd": "Z:/definitely/not/here",
        "tool_input": {"command": "git commit -m 'chore: no claim here'"},
    }
    assert claude_hook.main(stdin_text=json.dumps(payload), argv=[]) == 0


def test_an_unexpected_internal_error_still_allows(monkeypatch):
    # The backstop: no internal defect, present or future, may block.
    from claim_check.entrypoints import claude_hook

    def boom(*a, **kw):
        raise RuntimeError("synthetic internal failure")

    monkeypatch.setattr(claude_hook, "extract_commit_message", boom)
    payload = {"tool_name": "Bash", "tool_input": {"command": "git commit -m '22 passed'"}}
    assert claude_hook.main(stdin_text=json.dumps(payload), argv=[]) == 0


def test_debug_dump_writes_the_raw_payload_when_the_env_var_is_set(tmp_path, monkeypatch):
    # The plugin docs contradict themselves on payload field names, so being
    # able to capture what a hook actually received is the only reliable way
    # to settle it on a given Claude Code version.
    from claim_check.entrypoints import claude_hook

    monkeypatch.setenv("CLAIM_CHECK_DEBUG_DUMP", str(tmp_path / "dumps"))
    raw = json.dumps({"tool_name": "Write", "tool_input": {}})
    assert claude_hook.main(stdin_text=raw, argv=[]) == 0

    written = list((tmp_path / "dumps").glob("PreToolUse-*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text(encoding="utf-8"))["tool_name"] == "Write"


def test_debug_dump_is_a_silent_no_op_when_the_env_var_is_unset(tmp_path, monkeypatch):
    from claim_check.entrypoints import claude_hook

    monkeypatch.delenv("CLAIM_CHECK_DEBUG_DUMP", raising=False)
    raw = json.dumps({"tool_name": "Write", "tool_input": {}})
    assert claude_hook.main(stdin_text=raw, argv=[]) == 0
    assert list(tmp_path.iterdir()) == []


def test_an_unwritable_debug_dump_target_never_breaks_the_hook(tmp_path, monkeypatch):
    # A debugging aid that can break the hook it instruments is worse than
    # none: this runs in a PreToolUse hook where a raise is a nonzero exit.
    from claim_check.entrypoints import claude_hook

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CLAIM_CHECK_DEBUG_DUMP", str(blocker / "nested"))
    raw = json.dumps({"tool_name": "Write", "tool_input": {}})
    assert claude_hook.main(stdin_text=raw, argv=[]) == 0
