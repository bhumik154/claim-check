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
