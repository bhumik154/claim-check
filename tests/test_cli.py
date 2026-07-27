from pathlib import Path

from claim_check.cli import main, verify_tests


def test_no_claim_message_exits_zero_without_running_pytest(tmp_path):
    # No pytest_output_file provided and the cwd has no test suite at all -
    # if this tried to run pytest, it would fail; passing means the
    # claim-extraction gate correctly skipped the runner entirely.
    code = verify_tests("Refactor the loop for clarity", cwd=tmp_path)
    assert code == 0


def test_matching_claim_against_captured_output_exits_zero(tmp_path):
    output_file = tmp_path / "pytest_output.txt"
    output_file.write_text("============ 22 passed in 0.15s ============", encoding="utf-8")
    code = verify_tests("22 passed", pytest_output_file=output_file)
    assert code == 0


def test_mismatched_claim_against_captured_output_exits_nonzero(tmp_path):
    output_file = tmp_path / "pytest_output.txt"
    output_file.write_text("============ 21 passed in 0.15s ============", encoding="utf-8")
    code = verify_tests("22 passed", pytest_output_file=output_file)
    assert code == 1


def test_runner_error_against_captured_crash_output_still_exits_zero(tmp_path):
    # Fails open on a pytest crash, per the resolved crash policy.
    output_file = tmp_path / "pytest_output.txt"
    output_file.write_text("INTERNALERROR> something broke", encoding="utf-8")
    code = verify_tests("22 passed", pytest_output_file=output_file)
    assert code == 0


def test_disambiguates_a_real_file_path_from_a_literal_message():
    # The literal string below is not a path that exists, so it must be
    # treated as message text directly, not raise a file-not-found error.
    code = verify_tests("Refactor without any test claim at all")
    assert code == 0


def test_reads_message_from_an_actual_file_path(tmp_path):
    message_file = tmp_path / "COMMIT_EDITMSG"
    message_file.write_text("22 passed", encoding="utf-8")
    output_file = tmp_path / "pytest_output.txt"
    output_file.write_text("============ 21 passed in 0.15s ============", encoding="utf-8")
    code = verify_tests(str(message_file), pytest_output_file=output_file)
    assert code == 1


def test_main_verify_tests_subcommand_end_to_end(tmp_path):
    output_file = tmp_path / "pytest_output.txt"
    output_file.write_text("============ 22 passed in 0.15s ============", encoding="utf-8")
    code = main(["verify-tests", "22 passed", "--pytest-output", str(output_file)])
    assert code == 0
