"""Checks the README's factual claims against the code, and can resync them.

This project catches test-count claims that do not match reality. In v0.6.0 it
shipped a README claiming 370 test cases against a real 419, and a bullet
reading "pytest only, for now" long after vitest and jest landed, contradicting
the first line of the same file. Reviewers read prose carefully and do not
recompute numbers, so mechanical facts are exactly the class that escapes human
review.

    python scripts/readme_facts.py           # report drift, exit 1 if any
    python scripts/readme_facts.py --fix     # rewrite the syncable facts

Two things here look like overengineering and are not. Both were arrived at by
watching a simpler version fail.

Fenced blocks are found by a line parser rather than a regex. The obvious
pattern, a non-greedy match between triple backticks, matches from a CLOSING
fence to the next OPENING one and happily returns prose.

The runner list is compared as a canonical fragment rather than as a set of
runner names mentioned in the sentence. The bullet that actually shipped broken
was "**pytest only, for now.** vitest, jest and cargo test are a real gap",
which MENTIONS all three runners. Any set-extraction check reads that as
{pytest, vitest, jest}, agrees with the tagline, and passes while the README
contradicts itself.

A missing anchor is a hard failure, never a skip. A check that quietly stops
matching is indistinguishable from a check that passes, which is the failure
this whole project exists to attack.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

README = REPO / "README.md"
PRECOMMIT_SNIPPET = REPO / "examples" / "pre-commit-config-snippet.yaml"
COMPARE_TESTS = REPO / "tests" / "test_compare.py"
PYPROJECT = REPO / "pyproject.toml"
PLUGIN_MANIFEST = REPO / ".claude-plugin" / "plugin.json"
MARKETPLACE_MANIFEST = REPO / ".claude-plugin" / "marketplace.json"

FIX_COMMAND = "python scripts/readme_facts.py --fix"

# Install instructions name a GIT TAG. This check compares them against
# __version__, which proves the repo agrees with itself and nothing more.
# The version is bumped in the release commit and tagged afterwards, so a
# synced README legitimately names a tag that does not exist yet. That
# window is what produced a broken install instruction at v0.5.0.
VERSION_CAVEAT = (
    "this compares against __version__ only; it cannot prove the tag exists "
    "on GitHub, and between a release bump and its tag it will name one that "
    "does not yet"
)

# The test the README quotes verbatim, body and comments included.
EMBEDDED_TEST_NAME = (
    "test_all_tests_pass_claim_with_zero_collected_tests_is_flagged_not_silently_matched"
)


class AnchorMissing(Exception):
    """The text a check needs is not where the check expects it.

    Raised rather than returning "no drift" on purpose. If the README is
    reworded so a pattern stops matching, treating that as success leaves
    something that looks like protection and is not.
    """


def _normalise(text: str) -> str:
    """Whitespace-collapsed, for comparing against hard-wrapped prose."""
    return " ".join(text.split())


# ---------------------------------------------------------------- anchors


def find_one(text: str, pattern: str, label: str) -> "re.Match":
    """The single match for `pattern`, or AnchorMissing.

    More than one match is also a failure. Two matches mean the check would
    silently pick one and leave the other free to rot, which is how a fact
    ends up correct in one place and wrong in another.
    """
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) == 1:
        return matches[0]
    found = "no match" if not matches else f"{len(matches)} matches"
    raise AnchorMissing(
        f"{label}: expected exactly one match for {pattern!r}, found {found}. "
        "The document was reworded, so this check can no longer locate the "
        "fact it verifies. Update the pattern in scripts/readme_facts.py."
    )


def fenced_blocks(text: str) -> List[Tuple[Optional[str], str]]:
    """Every fenced block as (language tag or None, body).

    A line parser, not a regex. See the module docstring for why.
    """
    blocks: List[Tuple[Optional[str], str]] = []
    lang: Optional[str] = None
    buf: List[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("```"):
            if inside:
                blocks.append((lang, "\n".join(buf)))
                buf, inside = [], False
            else:
                lang = line[3:].strip() or None
                inside = True
            continue
        if inside:
            buf.append(line)
    return blocks


def untagged_block_containing(text: str, needle: str, label: str) -> str:
    matches = [b for tag, b in fenced_blocks(text) if tag is None and needle in b]
    if len(matches) != 1:
        raise AnchorMissing(
            f"{label}: expected exactly one untagged fenced block containing "
            f"{needle!r}, found {len(matches)}."
        )
    return matches[0]


# ------------------------------------------------------- sources of truth


def collected_test_count() -> int:
    """How many tests the whole suite collects.

    A subprocess rather than the running session's own count, because
    `pytest -k something` collects a handful and this check must not be
    fooled by how its own run was narrowed. That is the same scope problem
    the product itself solves.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    # The summary is a line of its own, printed last. Searching the whole
    # output found "3 tests collected" inside a TEST NAME first, and --fix
    # wrote "has 3 cases" into the README while the check, reading the same
    # wrong number, agreed with it. The very failure this project exists to
    # catch: a quoted tally taken for the real one.
    counts = [
        int(found.group(1))
        for line in result.stdout.splitlines()
        for found in [re.match(r"^(\d+) tests? collected\b", line.strip())]
        if found
    ]
    if not counts:
        raise AnchorMissing(
            "could not read a collected count from `pytest --collect-only -q`. "
            f"stdout tail: {result.stdout.strip()[-200:]!r}"
        )
    return counts[-1]


def package_version() -> str:
    from claim_check import __version__

    return __version__


def tagline_listing(readme: str) -> str:
    """The supported-runner listing, taken from the README's opening line.

    The README is its own source of truth here. `_PARSERS` in runners.py holds
    two parsers covering three runner names with no mapping between them, so
    there is nothing in the code to derive this from. The consequence is
    recorded in docs/design/specs/2026-08-25-readme-self-verification-design.md:
    both sentences wrong together is undetectable.
    """
    match = find_one(
        readme,
        r"^Checks whether the test count.*?\. ([a-z, ]+? and [a-z]+)\.$",
        "runner listing (tagline)",
    )
    return match.group(1)


# --------------------------------------------------------- syncable facts


class Rule:
    """One fact that lives in a document and can be rewritten in place."""

    def __init__(
        self, name: str, path: Path, pattern: str, expected: str, note: Optional[str] = None
    ):
        self.name = name
        self.path = path
        self.pattern = pattern
        self.expected = expected
        # Printed alongside a drift report. For facts whose check is weaker
        # than it looks, so the reader is not left assuming more coverage
        # than exists.
        self.note = note

    def actual(self) -> str:
        text = self.path.read_text(encoding="utf-8")
        return find_one(text, self.pattern, self.name).group(1)

    def apply(self) -> bool:
        """Rewrites only the captured group. Returns True if anything changed."""
        text = self.path.read_text(encoding="utf-8")
        match = find_one(text, self.pattern, self.name)
        if match.group(1) == self.expected:
            return False
        start, end = match.span(1)
        self.path.write_text(text[:start] + self.expected + text[end:], encoding="utf-8")
        return True


def syncable_rules() -> List[Rule]:
    readme = README.read_text(encoding="utf-8")
    version = package_version()
    return [
        Rule("test-case count", README, r"has (\d+) cases", str(collected_test_count())),
        Rule("README pre-commit rev", README, r"rev: v([\d.]+)", version, note=VERSION_CAVEAT),
        Rule("README pip install ref", README, r"claim-check@v([\d.]+)", version, note=VERSION_CAVEAT),
        Rule("example snippet rev", PRECOMMIT_SNIPPET, r"rev: v([\d.]+)", version, note=VERSION_CAVEAT),
        Rule(
            "runner listing (limitations bullet)",
            README,
            r"^\*\*([a-z, ]+? and [a-z]+) only\.\*\*",
            tagline_listing(readme),
        ),
    ]


# ------------------------------------------------------ assert-only checks
#
# These compare rather than rewrite. A mismatch here may mean the code changed
# behaviour, which is a bug to investigate, not stale prose to overwrite.


def embedded_test_block() -> Tuple[str, str]:
    """(what the README shows, what the test file actually contains)."""
    readme = README.read_text(encoding="utf-8")
    blocks = [b for tag, b in fenced_blocks(readme) if tag == "python"]
    if len(blocks) != 1:
        raise AnchorMissing(
            f"embedded test: expected exactly one python block, found {len(blocks)}."
        )
    source = COMPARE_TESTS.read_text(encoding="utf-8")
    start = source.find(f"def {EMBEDDED_TEST_NAME}")
    if start == -1:
        raise AnchorMissing(
            f"embedded test: {EMBEDDED_TEST_NAME} no longer exists in "
            f"{COMPARE_TESTS.name}. The README quotes it verbatim."
        )
    end = source.find("\ndef ", start + 1)
    real = source[start:] if end == -1 else source[start:end]
    trim = lambda s: "\n".join(line.rstrip() for line in s.strip().splitlines())
    return trim(blocks[0]), trim(real)


def stop_hook_example() -> Tuple[str, str]:
    """(what the README shows, what the code really produces)."""
    from claim_check.entrypoints.claude_stop_hook import _cause_sentence
    from claim_check.session_stats import NO_RUN, STALE, SessionStats

    stats = SessionStats(backed=2, unbacked=4, causes={NO_RUN: 3, STALE: 1})
    real = (
        'claim-check: could not verify "all tests pass". '
        f"{_cause_sentence(None)} {stats.render()}"
    )
    shown = untagged_block_containing(
        README.read_text(encoding="utf-8"), "this session:", "stop hook example"
    )
    return _normalise(shown), _normalise(real)


def narrowed_run_example() -> Tuple[str, str]:
    """(what the README shows, what the code really produces)."""
    from claim_check.compare import compare_claims
    from claim_check.conversation import extract_claims
    from claim_check.models import PytestCounts, RunScope

    claim = extract_claims("All 22 tests pass")[0]
    counts = PytestCounts(
        passed=1, failed=0, skipped=0, xfailed=0, xpassed=0, errors=0,
        warnings=0, deselected=0, duration_s=0.1, raw_summary_line="1 passed in 0.1s",
    )
    scope = RunScope(
        narrowed=True,
        reason="this run was narrowed by 'sub/', so it covered less than the whole suite",
    )
    verdict = compare_claims([claim], counts, scope=scope)
    real = f"claim-check: WARNING - {verdict.message}"
    shown = untagged_block_containing(
        README.read_text(encoding="utf-8"), "WARNING", "narrowed run example"
    )
    return _normalise(shown), _normalise(real)


def documented_console_scripts() -> Tuple[set, set]:
    """(commands the README invokes, commands pyproject declares)."""
    declared = set(
        re.findall(r"^(claim-check[\w-]*) = ", PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE)
    )
    if not declared:
        raise AnchorMissing("console scripts: no [project.scripts] entries found in pyproject.toml")
    invoked = set()
    for tag, body in fenced_blocks(README.read_text(encoding="utf-8")):
        if tag != "bash":
            continue
        for line in body.splitlines():
            match = re.match(r"^(claim-check[\w-]*)\b", line.strip())
            if match:
                invoked.add(match.group(1))
    return invoked, declared


def plugin_install_identifier() -> Tuple[str, str]:
    """(identifier the README tells people to install, identifier the manifests define)."""
    plugin = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))["name"]
    marketplace = json.loads(MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))["name"]
    readme = README.read_text(encoding="utf-8")
    shown = find_one(
        readme, r"claude plugin install (\S+)", "plugin install identifier"
    ).group(1)
    return shown, f"{plugin}@{marketplace}"


# ------------------------------------------------------------------- main


def _drift() -> List[str]:
    problems = []
    for rule in syncable_rules():
        actual = rule.actual()
        if actual != rule.expected:
            problem = f"{rule.name}: says {actual!r}, should be {rule.expected!r}"
            if rule.note:
                problem += "\n    note: " + rule.note
            problems.append(problem)
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="readme_facts")
    parser.add_argument(
        "--fix", action="store_true", help="rewrite the syncable facts in place"
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    # The anchor is not resolved when a Rule is built, only when it is read or
    # written, so the guard has to cover the whole body. An earlier version
    # wrapped construction alone and let AnchorMissing escape as a traceback.
    try:
        rules = syncable_rules()
        if args.fix:
            # Resolve every anchor before writing anything. A fixer that
            # cannot find one fact must not guess where it belongs, and must
            # not half-apply the others on its way to discovering that.
            for rule in rules:
                rule.actual()
            changed = [rule.name for rule in rules if rule.apply()]
            for name in changed:
                print(f"readme_facts: updated {name}")
            if not changed:
                print("readme_facts: already in sync")
            return 0
        problems = _drift()
    except AnchorMissing as exc:
        print(f"readme_facts: {exc}", file=sys.stderr)
        return 2

    if problems:
        for problem in problems:
            print(f"readme_facts: {problem}", file=sys.stderr)
        print(f"readme_facts: run `{FIX_COMMAND}` to resync", file=sys.stderr)
        return 1
    print("readme_facts: README facts match the code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
