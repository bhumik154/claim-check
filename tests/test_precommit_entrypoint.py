import sys

import pytest

from claim_check.entrypoints.precommit import main


def test_no_claim_message_exits_zero(tmp_path):
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("Refactor the loop for clarity", encoding="utf-8")
    assert main([str(msg_file)]) == 0


def test_matching_claim_exits_zero(tmp_path, monkeypatch):
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("22 passed", encoding="utf-8")

    from claim_check.entrypoints import precommit as precommit_module
    from claim_check.runner import result_from_captured_output

    monkeypatch.setattr(
        precommit_module,
        "run_pytest",
        lambda cwd, **kwargs: result_from_captured_output(0, "============ 22 passed in 0.15s ============"),
    )
    assert main([str(msg_file)]) == 0


def test_mismatched_claim_exits_nonzero_and_aborts_the_commit(tmp_path, monkeypatch):
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("22 passed", encoding="utf-8")

    from claim_check.entrypoints import precommit as precommit_module
    from claim_check.runner import result_from_captured_output

    monkeypatch.setattr(
        precommit_module,
        "run_pytest",
        lambda cwd, **kwargs: result_from_captured_output(0, "============ 21 passed in 0.15s ============"),
    )
    assert main([str(msg_file)]) == 1


def test_utf8_decode_error_fails_open_returns_success(tmp_path):
    # A deliberate divergence from conventional-pre-commit's own behavior
    # (which fails the commit on a decode error): a decode error isn't
    # evidence a claim is wrong, it's an environment issue, so this fails
    # open instead.
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_bytes(b"\xff\xfe not valid utf-8 \x00\x01")
    assert main([str(msg_file)]) == 0


def test_missing_input_file_fails_open_instead_of_crashing(capsys):
    # Confirmed directly: open() on a nonexistent path raises
    # FileNotFoundError uncaught when only UnicodeDecodeError is caught.
    # git and pre-commit always pass a real COMMIT_EDITMSG path, but a
    # developer manually testing this hook from a terminal with a typo'd
    # path shouldn't get an unhandled traceback for it.
    code = main(["definitely_does_not_exist_xyz.txt"])
    assert code == 0
    assert "missing" in capsys.readouterr().out


def test_runner_error_fails_open_returns_success(tmp_path, monkeypatch):
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("22 passed", encoding="utf-8")

    from claim_check.entrypoints import precommit as precommit_module
    from claim_check.runner import result_from_captured_output

    monkeypatch.setattr(
        precommit_module,
        "run_pytest",
        lambda cwd, **kwargs: result_from_captured_output(0, "INTERNALERROR> something broke"),
    )
    assert main([str(msg_file)]) == 0


def test_command_flag_reaches_a_real_subprocess_invocation(tmp_path):
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("1 passed", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(
        "def test_one():\n    assert True\n", encoding="utf-8"
    )
    code = main(
        [
            str(msg_file),
            "--cwd",
            str(tmp_path),
            "--command",
            f'"{sys.executable}" -m pytest',
        ]
    )
    assert code == 0


def test_bad_command_flag_fails_open_instead_of_crashing(tmp_path):
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("22 passed", encoding="utf-8")
    code = main(
        [
            str(msg_file),
            "--cwd",
            str(tmp_path),
            "--command",
            "definitely_not_a_real_command_xyz123",
        ]
    )
    assert code == 0


def test_timeout_flag_kills_a_hanging_command_and_fails_open(tmp_path):
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("22 passed", encoding="utf-8")
    code = main(
        [
            str(msg_file),
            "--cwd",
            str(tmp_path),
            "--command",
            f'"{sys.executable}" -c "import time; time.sleep(5)"',
            "--timeout",
            "0.5",
        ]
    )
    assert code == 0


def test_an_unexpected_internal_error_allows_the_commit(tmp_path, monkeypatch):
    # A commit-msg hook exits nonzero to abort the commit, so any unhandled
    # exception aborts an honest commit with a raw traceback. Confirmed end
    # to end before this guard existed: a test printing a line shaped like
    # "deploy finished in 1.2.3s" blocked the commit.
    from claim_check.entrypoints import precommit

    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("22 passed", encoding="utf-8")

    def boom(*a, **kw):
        raise RuntimeError("synthetic internal failure")

    monkeypatch.setattr(precommit, "run_pytest", boom)
    assert precommit.main([str(msg)]) == 0


def test_non_positive_timeout_is_rejected_loudly(tmp_path):
    from claim_check.entrypoints import precommit

    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("22 passed", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        precommit.main([str(msg), "--timeout", "0"])
    assert excinfo.value.code == 2
