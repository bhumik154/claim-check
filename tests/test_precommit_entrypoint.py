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
        lambda cwd: result_from_captured_output(0, "============ 22 passed in 0.15s ============"),
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
        lambda cwd: result_from_captured_output(0, "============ 21 passed in 0.15s ============"),
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


def test_runner_error_fails_open_returns_success(tmp_path, monkeypatch):
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("22 passed", encoding="utf-8")

    from claim_check.entrypoints import precommit as precommit_module
    from claim_check.runner import result_from_captured_output

    monkeypatch.setattr(
        precommit_module,
        "run_pytest",
        lambda cwd: result_from_captured_output(0, "INTERNALERROR> something broke"),
    )
    assert main([str(msg_file)]) == 0
