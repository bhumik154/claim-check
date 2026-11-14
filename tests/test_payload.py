"""Where a hook should treat a Bash command as having run."""

import os

import pytest

from claim_check._payload import effective_dir


def _payload(cwd):
    return {"cwd": str(cwd)}


def test_no_cd_means_the_payload_directory(tmp_path):
    assert effective_dir(_payload(tmp_path), "python -m pytest -q") == str(tmp_path)


def test_a_relative_cd_is_resolved_against_the_payload_directory(tmp_path):
    (tmp_path / "proj").mkdir()
    result = effective_dir(_payload(tmp_path), "cd proj && python -m pytest -q")
    assert os.path.samefile(result, tmp_path / "proj")


def test_an_absolute_cd_is_used_as_is(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    result = effective_dir(_payload("."), f'cd "{proj}" && pytest')
    assert os.path.samefile(result, proj)


def test_a_cd_to_a_directory_that_does_not_exist_falls_back(tmp_path):
    # Better to file evidence under the session directory than under a path
    # that was never real.
    assert effective_dir(_payload(tmp_path), "cd nope && pytest") == str(tmp_path)


def test_a_non_string_command_falls_back(tmp_path):
    assert effective_dir(_payload(tmp_path), None) == str(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Git Bash drive paths only exist on Windows")
def test_a_git_bash_drive_path_is_understood_on_windows(tmp_path):
    # Windows Python reads "/c/Users/..." as a path on the current drive,
    # which does not exist. This is the form the Bash tool really uses.
    proj = tmp_path / "proj"
    proj.mkdir()
    drive, rest = os.path.splitdrive(str(proj))
    git_bash = "/" + drive[0].lower() + rest.replace("\\", "/")
    result = effective_dir(_payload("."), f'cd "{git_bash}" && pytest')
    assert os.path.samefile(result, proj)



@pytest.mark.skipif(os.name != "nt", reason="Git Bash drive paths only exist on Windows")
def test_a_bare_git_bash_drive_means_the_drive_root(tmp_path):
    # Smoke test of 0.7.0 before release: "/c" became "C:", which Windows reads as "the
    # current folder on drive C", not the root of the drive.
    drive = os.path.splitdrive(str(tmp_path))[0]
    result = effective_dir(_payload(tmp_path), f"cd /{drive[0].lower()} && pytest")
    assert os.path.normcase(result) == os.path.normcase(drive + "\\")


# --- what a Bash call printed, and how it ended ---------------------------------

from claim_check._payload import leading_cd_ok, tool_failure, tool_output  # noqa: E402


def test_tool_output_joins_stdout_and_stderr():
    # make prints its "make: *** ... Error" line on stderr.
    payload = {"tool_response": {"stdout": "go test ./...\n", "stderr": "make: *** [check] Error 1\n"}}
    assert "make: ***" in tool_output(payload)
    assert tool_output({"tool_response": {"stdout": "only\n", "stderr": ""}}) == "only\n"


def test_a_failure_payload_gives_its_exit_code_and_output():
    # The PostToolUseFailure payload of Claude Code 2.1.274.
    payload = {"error": "Exit code 2\nsrc/bad.ts(1,7): error TS2322: nope\n", "is_interrupt": False}
    assert tool_failure(payload) == (2, "src/bad.ts(1,7): error TS2322: nope\n")
    assert tool_failure({"error": "Error: Exit code 1\nboom"}) == (1, "boom")


def test_a_failure_without_an_exit_code_keeps_its_text():
    assert tool_failure({"error": "Command timed out"}) == (None, "Command timed out")
    assert tool_failure({"error": None}) is None
    assert tool_failure({}) is None


def test_a_leading_cd_is_ok_only_when_its_directory_exists(tmp_path):
    payload = {"cwd": str(tmp_path)}
    (tmp_path / "proj").mkdir()
    assert leading_cd_ok(payload, "cd proj && make check") is True
    assert leading_cd_ok(payload, "cd missing && make check") is False
    assert leading_cd_ok(payload, "make check") is True
