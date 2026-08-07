"""Evidence store: recording real test runs so a claim can be checked
against what actually happened rather than by re-running the suite.

The two invariants under test throughout: staleness only ever downgrades
evidence to "unknown" (never promotes a claim to "false"), and uncertain
scope is always resolved as "scoped" (unusable), never as whole-suite.
"""

import json
import time

import pytest

from claim_check import evidence


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A source tree plus an isolated cache root, so tests never touch the
    real user cache."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    return root


PASSING_OUTPUT = (
    "============================= test session starts =============================\n"
    "collected 22 items\n\n"
    "========================= 22 passed in 0.30s =========================\n"
)


def test_evidence_is_stored_outside_the_working_tree(project):
    # The agent under verification can write anything inside the repo, so
    # evidence kept there could simply be edited rather than earned.
    evidence.record_run(project, "s1", "python -m pytest", PASSING_OUTPUT)
    path = evidence.evidence_path(project, "s1")
    assert path.is_file()
    assert project not in path.parents
    assert str(project) not in str(path)


def test_a_real_run_is_recorded_with_its_counts_and_argv(project):
    record = evidence.record_run(project, "s1", "python -m pytest", PASSING_OUTPUT)
    assert record["counts"]["passed"] == 22
    assert record["argv"] == ["python", "-m", "pytest"]
    assert record["scoped"] is False


def test_output_with_no_summary_line_is_not_recorded_as_evidence(project):
    # An ordinary Bash call that happens to be observed is not a test run.
    assert evidence.record_run(project, "s1", "ls -la", "total 0\ndrwxr-xr-x\n") is None
    assert not evidence.evidence_path(project, "s1").exists()


def test_fresh_evidence_round_trips(project):
    evidence.record_run(project, "s1", "python -m pytest", PASSING_OUTPUT)
    loaded = evidence.load_fresh(project, "s1")
    assert loaded is not None
    counts = evidence.counts_from_record(loaded)
    assert counts.passed == 22
    assert counts.total == 22


def test_evidence_past_its_ttl_reads_as_unknown(project):
    evidence.record_run(project, "s1", "python -m pytest", PASSING_OUTPUT, ttl_s=60)
    assert evidence.load_fresh(project, "s1", now=time.time() + 61) is None


def test_evidence_recorded_against_a_different_tree_state_reads_as_unknown(project):
    # The whole point of the fingerprint: a run from before the last edit is
    # not evidence about the code as it stands now.
    evidence.record_run(project, "s1", "python -m pytest", PASSING_OUTPUT)
    assert evidence.load_fresh(project, "s1") is not None
    (project / "test_b.py").write_text("def test_b():\n    assert True\n", encoding="utf-8")
    assert evidence.load_fresh(project, "s1") is None


def test_another_sessions_evidence_is_never_read_for_this_session(project):
    # Two agents in one repo must not silently judge each other's work.
    evidence.record_run(project, "session-a", "python -m pytest", PASSING_OUTPUT)
    assert evidence.load_fresh(project, "session-b") is None


def test_missing_corrupt_and_non_dict_records_all_read_as_unknown(project):
    assert evidence.load_fresh(project, "never-recorded") is None

    path = evidence.evidence_path(project, "s1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert evidence.load_fresh(project, "s1") is None

    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert evidence.load_fresh(project, "s1") is None

    path.write_text(json.dumps({"recorded_at": "soon"}), encoding="utf-8")
    assert evidence.load_fresh(project, "s1") is None


def test_recording_never_raises_even_when_the_cache_is_unwritable(project, monkeypatch, tmp_path):
    # A hook that crashes because a disk is full is worse than one that
    # silently records nothing.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(blocker))
    record = evidence.record_run(project, "s1", "python -m pytest", PASSING_OUTPUT)
    assert record["counts"]["passed"] == 22  # parsed fine, just not persisted


def test_fingerprint_of_an_unreachable_tree_is_empty_not_an_exception(tmp_path):
    assert evidence.fingerprint(tmp_path / "does-not-exist") == ""


def test_fingerprint_ignores_churn_that_cannot_change_test_behaviour(project):
    before = evidence.fingerprint(project)
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "junk.pyc").write_bytes(b"\x00\x01")
    (project / ".pytest_cache").mkdir()
    (project / ".pytest_cache" / "v").write_text("x", encoding="utf-8")
    assert evidence.fingerprint(project) == before


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest",
        "pytest",
        "python3 -m pytest -q",
        "pytest -v --tb=short",
        "poetry run pytest",
    ],
)
def test_whole_suite_invocations_are_not_marked_scoped(command):
    import shlex

    assert evidence.is_scoped(shlex.split(command)) is False


@pytest.mark.parametrize(
    "command",
    [
        "pytest -k slow",                     # the documented -k hazard
        "pytest -m integration",
        "pytest tests/unit",                  # a path narrows collection
        "pytest tests/test_a.py::test_one",   # a node id narrows it further
        "pytest -x",                          # stops early, total is meaningless
        "pytest -xvs",                        # -x bundled into a short-flag run
        "pytest --lf",
        "pytest --ff",
        "pytest --maxfail=1",
        "pytest --deselect tests/test_a.py::test_one",
        "pytest --collect-only",
    ],
)
def test_scope_narrowing_invocations_are_marked_scoped(command):
    import shlex

    assert evidence.is_scoped(shlex.split(command)) is True


def test_an_unparseable_command_is_treated_as_scoped(project):
    # Bias: unknown scope must never be usable as whole-suite evidence,
    # because a wrongly-unscoped record can confirm a false claim, whereas a
    # wrongly-scoped one merely goes unused.
    record = evidence.record_run(project, "s1", 'pytest "unbalanced', PASSING_OUTPUT)
    assert record["scoped"] is True


def test_empty_argv_is_treated_as_scoped():
    assert evidence.is_scoped([]) is True


# --- real commands are shell pipelines, not bare pytest invocations ----------
# Measured against a live session: the Bash tool's command is the whole shell
# line, e.g. `cd /repo && python -m pytest -q 2>&1 | tail -1`. The first
# version of is_scoped assumed argv was only the pytest invocation, so it read
# "cd" as a path argument and marked every real run scoped - which would have
# made the evidence store useless without ever being wrong in a unit test.

@pytest.mark.parametrize(
    "command",
    [
        'cd /repo && python -m pytest -q 2>&1 | tail -1',
        'cd /repo && pytest',
        'cd "/c/Claude Code/projects/claim-check" && python -m pytest -q',
        'python -m pytest -q 2>&1 | tail -1',
        'pytest > out.txt',
        'pytest 2>&1 | tee log.txt',
        'cd backend && poetry run pytest && echo done',
    ],
)
def test_whole_suite_runs_inside_a_shell_pipeline_are_not_scoped(command):
    import shlex

    assert evidence.is_scoped(shlex.split(command)) is False


@pytest.mark.parametrize(
    "command",
    [
        'cd /repo && pytest -k slow',
        'cd /repo && python -m pytest tests/unit',
        'pytest -x 2>&1 | tail -1',
        'cd /repo && pytest --lf | tail -1',
    ],
)
def test_scope_narrowing_survives_the_shell_pipeline(command):
    import shlex

    assert evidence.is_scoped(shlex.split(command)) is True


def test_a_command_with_no_recognisable_pytest_invocation_is_scoped():
    # "make test" may well run the whole suite, but nothing here can tell.
    # Unknown scope must never be usable as whole-suite evidence.
    import shlex

    assert evidence.is_scoped(shlex.split("make test")) is True
    assert evidence.is_scoped(shlex.split("npm run test")) is True


def test_a_narrowed_run_anywhere_in_the_pipeline_marks_the_whole_thing_scoped():
    import shlex

    assert evidence.is_scoped(shlex.split("pytest && pytest tests/unit")) is True


# --- runners other than pytest ------------------------------------------------

VITEST_OUTPUT = " Test Files  1 passed (1)\n      Tests  3 passed (3)\n   Duration  577ms\n"
JEST_OUTPUT = (
    "Test Suites: 1 passed, 1 total\n"
    "Tests:       1 skipped, 1 todo, 3 passed, 5 total\n"
    "Time:        0.485 s\n"
)


def test_a_vitest_run_is_recorded_as_evidence(project):
    record = evidence.record_run(project, "s1", "npx vitest run", VITEST_OUTPUT)
    assert record is not None
    assert record["counts"]["passed"] == 3
    assert record["scoped"] is False


def test_a_jest_run_is_recorded_as_evidence(project):
    record = evidence.record_run(project, "s1", "npx jest", JEST_OUTPUT)
    assert record["counts"]["passed"] == 3
    assert record["counts"]["skipped"] == 2
    assert record["scoped"] is False


def test_a_filtered_js_run_is_recorded_but_marked_scoped(project):
    # Same hazard as pytest -k: a true tally for a subset must never be
    # usable to confirm a claim about the whole suite.
    record = evidence.record_run(project, "s1", "npx jest -t login", JEST_OUTPUT)
    assert record["scoped"] is True


def test_a_js_run_behind_an_npm_script_is_marked_scoped(project):
    # "npm test" hides which runner ran and with what arguments.
    record = evidence.record_run(project, "s1", "npm test", VITEST_OUTPUT)
    assert record["scoped"] is True
