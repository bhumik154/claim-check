import argparse
import sys
from pathlib import Path

import pytest

from claim_check._args import positive_timeout
from claim_check.runner import result_from_captured_output, run_pytest


def _write_passing_test(tmp_path):
    (tmp_path / "test_sample.py").write_text(
        "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n",
        encoding="utf-8",
    )


def test_default_command_runs_real_pytest_and_parses_counts(tmp_path):
    _write_passing_test(tmp_path)
    result = run_pytest(tmp_path)
    assert result.counts is not None
    assert result.counts.passed == 2
    assert result.parse_error is None


def test_command_override_is_actually_used_not_ignored(tmp_path):
    # A custom --command must reach the real subprocess call, not just be
    # accepted by argparse and silently dropped.
    _write_passing_test(tmp_path)
    result = run_pytest(tmp_path, command=f'"{sys.executable}" -m pytest')
    assert result.counts is not None
    assert result.counts.passed == 2


def test_bad_command_fails_open_with_descriptive_error_instead_of_crashing(tmp_path):
    result = run_pytest(tmp_path, command="definitely_not_a_real_command_xyz123")
    assert result.counts is None
    assert "definitely_not_a_real_command_xyz123" in result.parse_error


def test_timeout_kills_a_hanging_command_and_fails_open(tmp_path):
    sleep_command = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    result = run_pytest(tmp_path, command=sleep_command, timeout_s=0.5)
    assert result.counts is None
    assert result.returncode == -1
    assert "0.5" in result.parse_error


def test_pytest_args_are_appended_after_the_command(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("def test_b():\n    assert True\n", encoding="utf-8")
    result = run_pytest(tmp_path, pytest_args=["test_a.py"])
    assert result.counts is not None
    assert result.counts.passed == 1


def test_a_byte_invalid_in_the_locale_encoding_does_not_crash_the_whole_process(tmp_path):
    # On Windows, subprocess.run's text-mode decoding defaults to the
    # locale's preferred encoding (often cp1252), not UTF-8. Confirmed
    # directly: capture_output's reader thread (subprocess.py's Windows-
    # only _readerthread, run in a daemon thread) calls fh.read(), and if
    # that raises UnicodeDecodeError, the exception is swallowed inside the
    # thread (only printed, never propagated), leaving proc.stdout as None
    # - which then crashes run_pytest with an unrelated TypeError once None
    # reaches parse_summary_line. 0x81 is a real trigger: it's one of the
    # handful of byte values cp1252 leaves undefined, and it's also
    # invalid on its own as UTF-8 (0x80-0xBF are continuation-only bytes),
    # so this reproduces the crash regardless of platform/locale. Requires
    # -s (or an equivalent capture-disabling pytest config) to reach the
    # subprocess pipe at all: pytest's own default capture-and-report
    # machinery already re-encodes captured output safely before ever
    # printing it, masking this for the common case.
    (tmp_path / "test_raw_byte.py").write_text(
        "import os\n\ndef test_raw_bytes():\n    os.write(1, bytes([0x81]))\n    assert True\n",
        encoding="utf-8",
    )
    result = run_pytest(tmp_path, pytest_args=["-s"])
    assert result.returncode == 0
    assert result.counts is not None
    assert result.counts.passed == 1


def test_nonexistent_working_directory_fails_open_naming_the_directory(tmp_path):
    # Only FileNotFoundError was caught. Windows raises NotADirectoryError
    # for a bad cwd, so this crashed with an unhandled traceback - and in
    # the commit-msg hook a traceback means a nonzero exit, which aborts an
    # entirely honest commit. On Linux it was caught but blamed the wrong
    # thing: "could not find or run the test command: '<python>'".
    missing = tmp_path / "no-such-dir"
    result = run_pytest(missing, timeout_s=20)
    assert result.counts is None
    assert "no-such-dir" in result.parse_error


def test_working_directory_that_is_a_file_fails_open(tmp_path):
    target = tmp_path / "notadir.txt"
    target.write_text("x", encoding="utf-8")
    result = run_pytest(target, timeout_s=20)
    assert result.counts is None
    assert "notadir.txt" in result.parse_error


def test_working_directory_permission_error_on_stat_fails_open(tmp_path, monkeypatch):
    # Path.is_dir() only swallows a small ignored-errno set (ENOENT,
    # ENOTDIR, EBADF, ELOOP, ...); EACCES is not in it, so a PermissionError
    # from a locked share or an ACL-mismatched mount re-raises straight out
    # of Path.is_dir(). Simulated with monkeypatch since real permission
    # failures aren't reliably reproducible on Windows.
    def _raise_permission_error(self):
        raise PermissionError("simulated EACCES")

    monkeypatch.setattr(Path, "is_dir", _raise_permission_error)
    result = run_pytest(tmp_path, timeout_s=20)
    assert result.counts is None
    assert "simulated EACCES" in result.parse_error


def test_none_working_directory_falls_back_to_the_current_directory(tmp_path):
    _write_passing_test(tmp_path)
    result = run_pytest(None, pytest_args=[str(tmp_path)], timeout_s=60)
    assert result.counts is not None
    assert result.counts.passed == 2


def test_unbalanced_quote_in_command_fails_open_instead_of_crashing(tmp_path):
    # shlex.split sat outside the try block, so a plain quoting typo in
    # --command raised ValueError: No closing quotation and blocked the
    # commit. The README itself tells people to quote this flag.
    result = run_pytest(tmp_path, command='poetry "run pytest', timeout_s=20)
    assert result.counts is None
    assert "run pytest" in result.parse_error or "quot" in result.parse_error.lower()


def test_whitespace_only_command_fails_open_instead_of_crashing(tmp_path):
    # shlex.split("   ") returns [], and subprocess.run([]) raised
    # OSError [WinError 87]; base_command[0] in the error handler would
    # have raised IndexError on top of it.
    result = run_pytest(tmp_path, command="   ", timeout_s=20)
    assert result.counts is None
    assert result.parse_error is not None


def test_positive_timeout_rejects_zero_and_negative_values():
    # --timeout 0 silently killed every run instantly and failed open,
    # reporting "did not finish within 0s" - a tool that verifies nothing
    # while looking like it works is the worst possible outcome here, so
    # this is a deliberate exception to the fail-open rule.
    assert positive_timeout("30") == 30.0
    for bad in ("0", "-5"):
        with pytest.raises(argparse.ArgumentTypeError):
            positive_timeout(bad)


def test_positive_timeout_rejects_unparseable_values():
    with pytest.raises(argparse.ArgumentTypeError):
        positive_timeout("abc")


def test_captured_output_helper_tolerates_non_string_input():
    assert result_from_captured_output(0, None).counts is None
    assert result_from_captured_output(0, b"==== 1 passed in 1.0s ====").counts.passed == 1
