# claim-check

Verify test-count claims in commit messages against real pytest output, as a pre-commit hook, a Claude Code hook, or a standalone CLI.

## The problem

Across a whole session of building two other small Python and TypeScript packages, the exact test count drifted repeatedly as fixes landed. More than once, a stated or hand-derived number was simply wrong, a "22 passed" that was actually 21, a golden reference value computed by hand that turned out to be off, until it got manually re-verified. That happened to careful, deliberate work. It happens far more easily in a commit message dashed off at the end of a long change, by a human or by an AI coding agent, "22 tests pass," "17/17 passing," "all tests pass," with nothing actually re-running the suite to check.

This tool does exactly that check, mechanically, every time: it looks for a test-count claim in a commit message, and if one is there, it verifies it against pytest's own summary line before the commit lands. If there's no claim, it does nothing, checking a claim that was never made is exactly the kind of false positive that gets a tool uninstalled.

## Tested against exact output, not just shape

[`tests/`](tests/) has 81 cases across the claim parser, the pytest-output parser, the shell-command parser, the comparison policy, and all three entry points. One example, from the comparison core:

```python
def test_all_tests_pass_claim_with_zero_collected_tests_is_flagged_not_silently_matched():
    # Vacuously true if you only check failed == 0; almost certainly not
    # what was meant by "all tests pass".
    verdict = compare_claims([_all_pass()], _counts(passed=0, failed=0))
    assert verdict.status == "mismatch"
```

Other things worth knowing about, by module:

| Scenario | Why it's there |
|---|---|
| No claim in the message | Never flagged, regardless of the actual test outcome, this is the one non-negotiable rule |
| `"N passed"`, `"N/M tests"`/`"N/M passing"` (equal or unequal), `"all tests pass"`, `"all N tests pass"` | The full set of claim shapes detected, by regex, not NLP |
| A decimal number (`"0.22"`) or an issue reference (`"#22"`) next to the word "passed" | Guarded explicitly so they're never misread as a count |
| `"not all tests pass yet"` | Negation guard: never registers as a claim |
| The same kind of claim repeated with conflicting numbers | The last occurrence in the text wins, the exact "stale count restated later" scenario this project exists for |
| pytest's real summary line, in every shape it takes (plain, with a failure, with a warning, with skips/xfails/xpasses, `no tests ran`, ANSI-colored) | Parsed from pytest's own authoritative tally, never re-derived by counting individual result lines |
| Real captured pytest-xdist output | Confirmed against an actual `pytest -n 2` run, not a guess: xdist prints exactly one final aggregate summary line, never per-worker partials |
| Multiple summary lines from separate invocations piped together (`pytest tests/unit && pytest tests/integration`) | Aggregated into a combined total, not "last one wins" - a claim like "all 100 tests pass" refers to the sum, not whichever suite happened to run last |
| A `no tests ran` line, which the generic summary regex also matches at the same position | De-duplicated explicitly; without it, aggregation counted that one line twice |
| An `INTERNALERROR>` crash with no summary line at all | Structurally distinct from "0 tests ran"; fails open, does not block the commit |
| The exact `git commit -m "$(cat <<'EOF' ... EOF)"` heredoc pattern this project's own commits use | Verified end to end: extracted correctly, and a wrong claim inside it is caught |
| Unbalanced quotes, an unresolved `$VAR` inside an unquoted heredoc, `git commit` as a substring inside an unrelated `echo` | All fail open (or are correctly ignored), a parse failure is not evidence a claim is wrong |
| A full-suite claim checked against a deliberately scoped run (one file passed via `pytest_args`) | Documents the real, confirmed partial-run limitation described at the top of Usage, this is expected, not a bug |

## Usage

> **Read this before you install it, or it will falsely block a correct commit and you'll (reasonably) rip it out.**
>
> claim-check can only ever compare a claim against whatever pytest actually collects for the exact invocation it runs, in the exact directory and with the exact arguments it's given. It has no way to know what "the full suite" means to you.
>
> If you write "all 150 tests pass" because you genuinely ran the complete suite yourself, but claim-check's own run is narrower, because a pre-commit "only test changed files" setup passed it one file, a `pytest.ini`/`pyproject.toml` `testpaths` restriction scoped discovery to a subdirectory, a sharded CI job only ran its own slice, or you invoked the CLI with extra `pytest` args, it will see far fewer tests than 150, and it will flag your entirely honest claim as a mismatch. This is confirmed, reproducible behavior (see `test_scoping_pytest_args_to_one_file_produces_a_false_mismatch_against_a_full_suite_claim` in the test suite), not a hypothetical edge case.
>
> **Always run claim-check against the same scope your claim actually refers to.** If your commit message claims something about the full suite, make sure claim-check's own invocation (its `cwd`, any extra `pytest` args, and your project's own `pytest.ini`/`pyproject.toml` test-discovery config) actually covers the full suite too, not a subset.

**Standalone CLI:**

```bash
claim-check verify-tests path/to/COMMIT_EDITMSG
# or check a literal message directly:
claim-check verify-tests "22 passed"
# reuse output you already captured instead of re-running the suite:
claim-check verify-tests path/to/COMMIT_EDITMSG --pytest-output pytest_output.txt
```

**pre-commit** (see [`examples/pre-commit-config-snippet.yaml`](examples/pre-commit-config-snippet.yaml)):

```yaml
repos:
  - repo: https://github.com/bhumik154/claim-check
    rev: v0.1.0
    hooks:
      - id: claim-check
```

The `commit-msg` stage isn't enabled by pre-commit's default install. Run `pre-commit install --hook-type commit-msg`, or add `default_install_hook_types: [pre-commit, commit-msg]` to your own config, or this hook installs but silently never fires.

This hook runs as `language: system`, deliberately, not `language: python`: a `python`-language hook runs inside pre-commit's own isolated virtualenv, which has none of your project's actual test dependencies (or even pytest itself). Confirmed directly: that misconfiguration doesn't error, it silently prints "could not verify... allowing commit" on every commit, since the runner crash fails open by design. Because it's `system`, **`claim-check` (and your project's own test dependencies) need to be installed in the same environment you actually run `git commit` from**, not a separate one, or the hook has nothing to run against.

**Claude Code hook** (see [`examples/claude-code-settings-snippet.json`](examples/claude-code-settings-snippet.json)), in `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "claim-check-claude-hook", "if": "Bash(git commit *)" }] }
    ]
  }
}
```

## Install

```bash
pip install claim-check
```

Zero runtime dependencies.

## What this is not

- **This tool verifies truthfulness, not test success.** It checks whether a claim matches the actual result, not whether the actual result is good. If you write "14/15 passing" and the suite genuinely has 14 passed and 1 failed, that claim is accurate, so this exits 0 and the commit proceeds, even though a test is failing. Most people reasonably assume a hook running pytest blocks a commit on any failure; this one only blocks on a claim that doesn't match reality. Confirmed directly: `verify_tests("WIP: 14/15 passing, one test is currently broken", ...)` against a real 14-passed-1-failed result returns a match, not a mismatch. If you want commits blocked on red builds specifically, use a standard pre-push hook (or CI) for that; pair it with this one rather than expecting this one to cover it.
- Not aware of what "the full suite" means to you, only what pytest actually collects for its own invocation. A scoped run (changed-files-only, `testpaths` restrictions, sharded CI) compared against a full-suite claim produces a false mismatch; see the warning at the top of Usage before you install this.
- Not NLP. It can't fully resolve tense or discourse ("was at 15 passed, now 22/22" is genuinely ambiguous to a regex); the last matching occurrence of a given claim kind wins, which handles the common case but not every one.
- pytest only in v0.1. Other runners (vitest, jest, cargo test) are a real, documented gap, not a silently unsupported one.
- The Claude Code hook only covers commits issued through the **Bash** tool specifically, since that's what `tool_input.command` requires to parse. A commit issued via a PowerShell tool call would be unchecked by this entry point. In practice this hasn't been a gap for this project's own workflow: every commit across the session this tool came out of went through Bash, including in a PowerShell-primary environment, because the heredoc pattern for multi-line commit messages is itself bash syntax.
- The `pre-commit` hook is client-side and bypassable with `git commit --no-verify`. Pair it with the CLI invoked in CI for actual enforcement, not just a local nudge.

## Where this came from

Extracted from the same discipline used building [`recurring-free-slots`](https://github.com/bhumik154/recurring-free-slots) and [`spiral-galaxy-ic`](https://github.com/bhumik154/spiral-galaxy-ic): verify a claim computationally before it ships, rather than trust it because it sounds right. This project targets the exact failure mode that kept showing up while building those two, a stated test count, or a hand-derived reference value, that turned out to be wrong until someone actually re-ran the check.

## License

MIT
