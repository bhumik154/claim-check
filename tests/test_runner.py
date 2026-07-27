import sys

import pytest

from claim_check.runner import run_pytest


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
