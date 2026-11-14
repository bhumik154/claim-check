"""The run log: which command a failure can be blamed on.

A claim that `make check` passes is contradicted only when the session's most
recent run of exactly that command, against the current files, provably
failed. These tests are the blame rules. Real failures in the author's
sessions show why they are strict: a failing call labelled `npx tsc` had
printed "TYPECHECK OK" (vitest failed), and every failing call containing
"make" was really a `gh` error.
"""

from pathlib import Path

import pytest

from claim_check import run_log

OUTPUTS = Path(__file__).parent / "fixtures" / "tool_outputs"


def out(name):
    return (OUTPUTS / name).read_text(encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "Makefile").write_text("check:\n\tgo test ./...\n", encoding="utf-8")
    (root / "package.json").write_text("{}", encoding="utf-8")
    return root


def record(project, command, output, outcome="ok", exit_code=None, session="s1"):
    return run_log.record_call(project, session, command, output, outcome, exit_code)


def contradiction(project, command, session="s1", cwd=None):
    return run_log.contradiction(command, cwd or project, session)


# --- what proves a failure ---------------------------------------------------

def test_a_failed_make_check_contradicts_a_claim_that_it_passes(project):
    record(project, "make check", out("make-check-fail.txt"), "failed", 2)
    assert contradiction(project, "make check").startswith("make: *** [Makefile:4: test] Error 1")


def test_a_failure_line_proves_it_even_through_a_pipe(project):
    # `| tail` makes the call succeed; the printed line still tells.
    record(project, "make check 2>&1 | tail -15", out("make-check-fail.txt"))
    assert contradiction(project, "make check") is not None


@pytest.mark.parametrize("claimed", ["tsc --noEmit", "npx tsc --noEmit", "tsc"])
def test_a_tsc_error_contradicts_any_way_of_naming_that_run(project, claimed):
    record(project, "npx tsc --noEmit 2>&1 | tail -40", out("tsc-fail.txt"))
    assert "error TS2322" in contradiction(project, claimed)


def test_a_passing_run_contradicts_nothing(project):
    record(project, "make check", out("make-check-pass.txt"))
    assert contradiction(project, "make check") is None


def test_a_sole_command_that_exited_nonzero_failed_even_without_a_failure_line(project):
    # go vet prints only file:line messages; the exit code is its own.
    record(project, f'cd "{project}" && go vet ./...', out("go-vet-fail.txt"), "failed", 1)
    assert contradiction(project, "go vet ./...") == "exit code 1"


def test_an_exit_code_is_not_blamed_on_a_command_after_a_cd_that_failed(project):
    record(project, "cd missing-dir && go vet ./...", "cd: missing-dir: No such file or directory", "failed", 1)
    assert contradiction(project, "go vet ./...") is None


# --- what does not ---------------------------------------------------------------

def test_another_tools_failure_is_not_blamed_on_tsc(project):
    # Real shape: tsc was fine, vitest failed, the call failed.
    output = "TYPECHECK OK\n\n RUN  v4.1.10\n\n Test Files  1 failed (1)\n      Tests  1 failed | 8 passed (9)\n"
    record(project, "npx tsc --noEmit && echo TYPECHECK OK && npx vitest run", output, "failed", 1)
    assert contradiction(project, "tsc --noEmit") is None
    assert contradiction(project, "npx vitest run") is not None


def test_a_failure_with_no_line_from_the_claimed_tool_is_not_blamed_on_it(project):
    # Real shape: `make` in the line, but `gh` is what failed.
    record(project, "make check && gh pr list -c", "unknown shorthand flag: 'c' in -c\n", "failed", 1)
    assert contradiction(project, "make check") is None


def test_a_failure_line_another_stage_could_have_printed_is_not_proof(project):
    record(project, "make check; echo 'make: *** [x] Error 1'", out("make-check-pass.txt") + "make: *** [x] Error 1\n")
    assert contradiction(project, "make check") is None


def test_an_old_log_printed_beside_the_run_is_not_proof(project):
    record(project, "cat last-build.log && make check", out("make-check-fail.txt"))
    assert contradiction(project, "make check") is None


def test_an_interrupted_run_proves_nothing(project):
    record(project, "make check", out("make-check-fail.txt"), "interrupted")
    assert contradiction(project, "make check") is None


# --- which run counts ---------------------------------------------------------------

def test_the_most_recent_run_wins(project):
    record(project, "make check", out("make-check-fail.txt"), "failed", 2)
    record(project, "make check", out("make-check-pass.txt"))
    assert contradiction(project, "make check") is None


def test_a_failure_against_files_that_have_since_changed_proves_nothing(project):
    record(project, "make check", out("make-check-fail.txt"), "failed", 2)
    (project / "fix.go").write_text("package pkg\n", encoding="utf-8")
    assert contradiction(project, "make check") is None


def test_a_different_command_is_not_the_claimed_one(project):
    record(project, "make webcheck", out("make-check-fail.txt"), "failed", 2)
    assert contradiction(project, "make check") is None


def test_another_sessions_run_is_never_used(project):
    record(project, "make check", out("make-check-fail.txt"), "failed", 2, session="other")
    assert contradiction(project, "make check") is None


def test_npm_sections_tell_two_scripts_apart(project):
    output = out("npm-run-typecheck-fail.txt") + out("npm-run-lintok-pass.txt")
    record(project, "npm run typecheck; npm run lintok", output)
    assert "error TS2322" in contradiction(project, "npm run typecheck")
    assert contradiction(project, "npm run lintok") is None


def test_npm_test_and_npm_run_test_are_the_same_command(project):
    record(project, "npm run test", out("npm-run-lint-fail.txt").replace(" lint\n", " test\n"), "failed", 1)
    assert contradiction(project, "npm test") is not None


def test_a_run_in_the_project_is_found_from_its_parent_folder(project):
    record(project, "make check", out("make-check-fail.txt"), "failed", 2)
    assert contradiction(project, "make check", cwd=project.parent) is not None


def test_commands_that_verify_nothing_are_not_recorded(project):
    assert record(project, "git status", "nothing to commit") is None
    assert record(project, "ls -la", "") is None


def test_the_log_keeps_only_the_most_recent_calls(project):
    for i in range(run_log.KEEP + 5):
        record(project, f"make check-{i}", out("make-check-pass.txt"))
    assert len(list(run_log.run_dir(project, "s1").glob("*.json"))) == run_log.KEEP


def test_unreadable_entries_are_skipped_not_fatal(project):
    record(project, "make check", out("make-check-fail.txt"), "failed", 2)
    (run_log.run_dir(project, "s1") / "9999999999999999999-0-x.json").write_text("{not json", encoding="utf-8")
    assert contradiction(project, "make check") is not None


def test_npm_sections_are_not_trusted_when_a_header_was_cut_off(project):
    # Real case from the replay: `| tail -20` dropped lint's "> pkg lint"
    # header, so the nearest header above eslint's error was build's, and the
    # error was blamed on `npm run build`.
    output = (
        "\n> app@0.1.0 build\n> next build\n\n   Compiled successfully\n"
        "  1:7  error  'x' is assigned a value but never used  no-unused-vars\n\n"
        "✖ 1 problem (1 error, 0 warnings)\n"
    )
    record(project, "npm run build 2>&1 | tail -30 && npm run lint 2>&1 | tail -20", output)
    assert contradiction(project, "npm run build") is None
    assert contradiction(project, "npm run lint") is None
