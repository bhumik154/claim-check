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


def test_null_byte_in_input_path_allows_the_commit_instead_of_raising():
    # open() on a path containing an embedded null byte raises ValueError
    # ("embedded null byte"), not UnicodeDecodeError or OSError. The
    # open() call used to sit outside the catch-all backstop, so this
    # ValueError escaped main() entirely, producing a traceback and a
    # nonzero exit - aborting the commit, which contradicts this module's
    # own guarantee that no internal error can ever block one.
    code = main(["bad\x00path"])
    assert code == 0


def test_non_positive_timeout_is_rejected_loudly(tmp_path):
    from claim_check.entrypoints import precommit

    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("22 passed", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        precommit.main([str(msg), "--timeout", "0"])
    assert excinfo.value.code == 2


def _narrowed_run(passed):
    from claim_check.models import PytestCounts, RunResult

    counts = PytestCounts(
        passed=passed, failed=0, skipped=0, xfailed=0, xpassed=0,
        errors=0, warnings=0, deselected=0, duration_s=0.1, raw_summary_line="fixture",
    )
    return RunResult(0, "", "", counts, None, argv=("-k", "slow"), cwd=".", command=None)


def test_a_narrowed_run_warns_and_allows_instead_of_blocking(tmp_path, monkeypatch, capsys):
    # The uninstall risk, at the entry point. An honest claim against a run
    # that covered less than the claim is about must not abort the commit.
    from claim_check.entrypoints import precommit

    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("All 150 tests pass", encoding="utf-8")
    monkeypatch.setattr(precommit, "run_pytest", lambda *a, **k: _narrowed_run(40))

    assert precommit.main([str(msg)]) == 0
    out = capsys.readouterr().out
    assert "could not verify" in out.lower()
    assert "150" in out and "40" in out


def test_the_commit_path_speaks_when_it_does_not_gate(tmp_path, monkeypatch, capsys):
    # This hook was invoked to gate, so "I did not gate" is information the
    # user needs. Silence here would be the tool verifying nothing while
    # looking like it worked.
    from claim_check.entrypoints import precommit

    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("All 150 tests pass", encoding="utf-8")
    monkeypatch.setattr(precommit, "run_pytest", lambda *a, **k: _narrowed_run(40))
    precommit.main([str(msg)])
    assert capsys.readouterr().out.strip() != ""


def test_an_unrestricted_run_still_blocks_a_wrong_count(tmp_path, monkeypatch):
    # The regression that matters most: abstention must not leak into the
    # ordinary case.
    from claim_check.entrypoints import precommit
    from claim_check.models import PytestCounts, RunResult

    counts = PytestCounts(
        passed=21, failed=0, skipped=0, xfailed=0, xpassed=0,
        errors=0, warnings=0, deselected=0, duration_s=0.1, raw_summary_line="fixture",
    )
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("All 22 tests pass", encoding="utf-8")
    monkeypatch.setattr(
        precommit, "run_pytest",
        lambda *a, **k: RunResult(0, "", "", counts, None, argv=(), cwd=".", command=None),
    )
    assert precommit.main([str(msg)]) == 1
