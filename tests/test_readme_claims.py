"""Pins the README's factual claims against the code.

Every check here comes in two halves: one asserting the README is correct
today, and one asserting the check actually FAILS against drift. The second
half is the important one. A check that cannot fail is indistinguishable from
no check, which is how the README came to claim 370 test cases while the suite
had 419.

The drift fixtures are the real historical strings, not invented ones.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import readme_facts  # noqa: E402
from readme_facts import (  # noqa: E402
    AnchorMissing, Rule, fenced_blocks, find_one, tagline_listing,
)

# The bullet exactly as it shipped broken in v0.6.0. It mentions all three
# runners, which is why a set-extraction check reads it as agreeing with the
# tagline and passes.
HISTORICAL_BAD_BULLET = (
    "**pytest only, for now.** vitest, jest and cargo test are a real gap, "
    "documented rather than silently unsupported."
)
CURRENT_TAGLINE = (
    "Checks whether the test count in your commit message is actually true. "
    "pytest, vitest and jest."
)


# ------------------------------------------------------- the README today


def test_every_syncable_fact_matches_the_code_today():
    # Adding a test changes the case count, so this goes red on the commit
    # that adds one. That is the intended cost. The message has to carry the
    # remedy, because whoever hits this is in the middle of something else.
    drift = readme_facts._drift()
    assert drift == [], (
        "The README no longer matches the code:\n  "
        + "\n  ".join(drift)
        + f"\n\nRun `{readme_facts.FIX_COMMAND}` to resync."
    )


def test_the_stop_hook_example_is_reproducible():
    shown, real = readme_facts.stop_hook_example()
    assert shown == real


def test_the_narrowed_run_example_is_reproducible():
    shown, real = readme_facts.narrowed_run_example()
    assert shown == real


def test_the_embedded_test_block_matches_the_real_source():
    shown, real = readme_facts.embedded_test_block()
    assert shown == real


def test_every_documented_command_is_a_real_console_script():
    invoked, declared = readme_facts.documented_console_scripts()
    assert invoked, "no claim-check commands found in the README's bash blocks"
    assert invoked <= declared, f"README invokes commands that do not exist: {invoked - declared}"


def test_the_plugin_install_identifier_matches_the_manifests():
    shown, real = readme_facts.plugin_install_identifier()
    assert shown == real


# --------------------------------------------- the checks can actually fail


def test_a_stale_test_count_is_caught(tmp_path):
    # The exact drift that shipped in v0.6.0: 370 against a real 419.
    doc = tmp_path / "README.md"
    doc.write_text("[`tests/`](tests/) has 370 cases covering things.\n", encoding="utf-8")
    rule = Rule("test-case count", doc, r"has (\d+) cases", "419")
    assert rule.actual() != rule.expected


def test_the_historical_runner_bullet_is_caught(tmp_path):
    # This is the case a set-of-mentioned-names check silently passes, since
    # the broken bullet names all three runners. The canonical fragment is
    # absent, so the anchor does not match and the check fails loudly.
    doc = tmp_path / "README.md"
    doc.write_text(f"{CURRENT_TAGLINE}\n\n{HISTORICAL_BAD_BULLET}\n", encoding="utf-8")
    rule = Rule(
        "runner listing",
        doc,
        r"^\*\*([a-z, ]+? and [a-z]+) only\.\*\*",
        "pytest, vitest and jest",
    )
    with pytest.raises(AnchorMissing):
        rule.actual()


def test_a_set_of_mentioned_runner_names_would_NOT_have_caught_it():
    # Pins why the canonical-fragment approach exists. If someone later
    # "simplifies" the runner check to compare mentioned names, this test
    # documents that the simpler version cannot see the bug it is meant to.
    import re

    mentioned = lambda line: {r for r in ("pytest", "vitest", "jest") if re.search(rf"\b{r}\b", line)}
    assert mentioned(HISTORICAL_BAD_BULLET) == mentioned(CURRENT_TAGLINE)


def test_a_stale_version_is_caught(tmp_path):
    doc = tmp_path / "snippet.yaml"
    doc.write_text("    rev: v0.5.0\n", encoding="utf-8")
    rule = Rule("rev", doc, r"rev: v([\d.]+)", "0.6.0")
    assert rule.actual() != rule.expected


def test_a_version_drift_report_admits_what_it_cannot_prove(sandbox, capsys):
    # An install instruction names a git TAG. This check only compares against
    # __version__, so between a release bump and its tag a synced README
    # legitimately names a tag that does not exist. That window produced a
    # broken install instruction at v0.5.0. The report has to say so, or it
    # implies a guarantee it does not provide.
    assert readme_facts.main([]) == 1
    err = capsys.readouterr().err
    assert readme_facts.VERSION_CAVEAT in err
    assert "cannot prove the tag exists" in err


# ---------------------------------------------- anchors fail loudly, never quietly


def test_a_missing_anchor_raises_rather_than_reporting_no_drift(tmp_path):
    doc = tmp_path / "README.md"
    doc.write_text("The count sentence was reworded away entirely.\n", encoding="utf-8")
    rule = Rule("test-case count", doc, r"has (\d+) cases", "419")
    with pytest.raises(AnchorMissing):
        rule.actual()


def test_a_duplicated_anchor_raises(tmp_path):
    # Two matches mean the check would pick one and let the other rot, which
    # is how a fact ends up right in one place and wrong in another.
    doc = tmp_path / "README.md"
    doc.write_text("has 419 cases here and has 370 cases there\n", encoding="utf-8")
    rule = Rule("test-case count", doc, r"has (\d+) cases", "419")
    with pytest.raises(AnchorMissing):
        rule.actual()


def test_anchor_missing_names_the_file_it_could_not_match():
    with pytest.raises(AnchorMissing) as excinfo:
        find_one("nothing here", r"has (\d+) cases", "test-case count")
    assert "test-case count" in str(excinfo.value)


# ------------------------------------------------------- the fence parser


def test_fenced_blocks_does_not_treat_prose_between_blocks_as_a_block():
    # The regression that forced a line parser. The obvious regex, a
    # non-greedy match between triple backticks, pairs a CLOSING fence with
    # the next OPENING one and returns the prose in between.
    text = "```bash\nreal code\n```\n\nprose that is not code\n\n```\nplain block\n```\n"
    bodies = [body for _, body in fenced_blocks(text)]
    assert bodies == ["real code", "plain block"]
    assert not any("prose" in b for b in bodies)


def test_fenced_blocks_records_the_language_tag():
    text = "```python\nx = 1\n```\n```\nplain\n```\n"
    assert fenced_blocks(text) == [("python", "x = 1"), (None, "plain")]


def test_fenced_blocks_ignores_an_unterminated_final_fence():
    text = "```bash\nclosed\n```\n```bash\nnever closed\n"
    assert fenced_blocks(text) == [("bash", "closed")]


def test_tagline_listing_reads_the_runner_list_from_the_opening_line():
    assert tagline_listing(CURRENT_TAGLINE + "\n") == "pytest, vitest and jest"


# ----------------------------------------------------------------- --fix


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Points the module at throwaway copies so --fix cannot touch the repo."""
    readme = tmp_path / "README.md"
    readme.write_text(
        f"{CURRENT_TAGLINE}\n\n"
        "    rev: v0.1.0\n"
        "pip install git+https://example/claim-check@v0.1.0\n"
        "[`tests/`](tests/) has 1 cases covering things.\n"
        "**pytest, vitest and jest only.** the rest are a gap.\n",
        encoding="utf-8",
    )
    snippet = tmp_path / "snippet.yaml"
    snippet.write_text("    rev: v0.1.0\n", encoding="utf-8")
    monkeypatch.setattr(readme_facts, "README", readme)
    monkeypatch.setattr(readme_facts, "PRECOMMIT_SNIPPET", snippet)
    monkeypatch.setattr(readme_facts, "collected_test_count", lambda: 419)
    monkeypatch.setattr(readme_facts, "package_version", lambda: "0.6.0")
    return readme, snippet


def test_fix_resyncs_the_stale_facts(sandbox):
    readme, snippet = sandbox
    assert readme_facts.main(["--fix"]) == 0
    assert "has 419 cases" in readme.read_text(encoding="utf-8")
    assert "rev: v0.6.0" in readme.read_text(encoding="utf-8")
    assert "claim-check@v0.6.0" in readme.read_text(encoding="utf-8")
    assert "rev: v0.6.0" in snippet.read_text(encoding="utf-8")


def test_fix_leaves_surrounding_prose_untouched(sandbox):
    readme, _ = sandbox
    readme_facts.main(["--fix"])
    text = readme.read_text(encoding="utf-8")
    assert "covering things." in text
    assert text.startswith("Checks whether the test count")


def test_fix_is_idempotent(sandbox):
    readme, snippet = sandbox
    readme_facts.main(["--fix"])
    after_first = (readme.read_text(encoding="utf-8"), snippet.read_text(encoding="utf-8"))
    readme_facts.main(["--fix"])
    assert (readme.read_text(encoding="utf-8"), snippet.read_text(encoding="utf-8")) == after_first


def test_check_mode_reports_drift_and_exits_nonzero(sandbox, capsys):
    assert readme_facts.main([]) == 1
    err = capsys.readouterr().err
    assert "test-case count" in err, "the failure must name which fact drifted"
    assert readme_facts.FIX_COMMAND in err, "the failure must name the command that fixes it"


def test_check_mode_passes_once_synced(sandbox):
    readme_facts.main(["--fix"])
    assert readme_facts.main([]) == 0


def test_fix_refuses_entirely_when_an_anchor_is_missing(sandbox, capsys):
    # A fixer that cannot find a fact must not guess where it belongs, and
    # must not quietly fix the others while one is unlocatable.
    readme, _ = sandbox
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("has 1 cases", "the count sentence is gone"),
        encoding="utf-8",
    )
    before = readme.read_text(encoding="utf-8")
    assert readme_facts.main(["--fix"]) == 2
    assert readme.read_text(encoding="utf-8") == before, "refusal must not modify the file"
    assert "readme_facts:" in capsys.readouterr().err



def test_the_collected_count_is_read_from_the_summary_not_from_a_test_name(monkeypatch):
    # Found smoke-testing 0.7.0 before release: a test named after the input
    # "3 tests collected in 0.01s" made the first match in --collect-only
    # output 3, and --fix wrote "has 3 cases" into the README. The check did
    # not object, because the README and the check read the same wrong
    # number. The real count is the summary line, which pytest prints last.
    import subprocess

    listing = (
        "tests/test_evidence.py::test_x[python -m pytest -q --co-3 tests collected in 0.01s\nold]\n"
        "tests/test_evidence.py::test_y\n"
        "\n"
        "553 tests collected in 0.16s\n"
    )

    class Done:
        stdout = listing

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    assert readme_facts.collected_test_count() == 553
