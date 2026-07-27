# claim-check

Verify test-count claims in commit messages against real pytest output, as a pre-commit hook, a Claude Code hook, or a standalone CLI.

## The problem

Across a whole session of building two other small Python and TypeScript packages, the exact test count drifted repeatedly as fixes landed. More than once, a stated or hand-derived number was simply wrong, a "22 passed" that was actually 21, a golden reference value computed by hand that turned out to be off, until it got manually re-verified. That happened to careful, deliberate work. It happens far more easily in a commit message dashed off at the end of a long change, by a human or by an AI coding agent, "22 tests pass," "17/17 passing," "all tests pass," with nothing actually re-running the suite to check.

This tool does exactly that check, mechanically, every time: it looks for a test-count claim in a commit message, and if one is there, it verifies it against pytest's own summary line before the commit lands. If there's no claim, it does nothing, checking a claim that was never made is exactly the kind of false positive that gets a tool uninstalled.

## Tested against exact output, not just shape

[`tests/`](tests/) has 78 cases across the claim parser, the pytest-output parser, the shell-command parser, the comparison policy, and all three entry points. One example, from the comparison core:

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
| pytest's real summary line, in every shape it takes (plain, with a failure, with a warning, with skips/xfails/xpasses, `no tests ran`, ANSI-colored, multiple partial lines from a plugin) | Parsed from pytest's own authoritative tally, never re-derived by counting individual result lines |
| An `INTERNALERROR>` crash with no summary line at all | Structurally distinct from "0 tests ran"; fails open, does not block the commit |
| The exact `git commit -m "$(cat <<'EOF' ... EOF)"` heredoc pattern this project's own commits use | Verified end to end: extracted correctly, and a wrong claim inside it is caught |
| Unbalanced quotes, an unresolved `$VAR` inside an unquoted heredoc, `git commit` as a substring inside an unrelated `echo` | All fail open (or are correctly ignored), a parse failure is not evidence a claim is wrong |

## Usage

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

- Not NLP. It can't fully resolve tense or discourse ("was at 15 passed, now 22/22" is genuinely ambiguous to a regex); the last matching occurrence of a given claim kind wins, which handles the common case but not every one.
- pytest only in v0.1. Other runners (vitest, jest, cargo test) are a real, documented gap, not a silently unsupported one.
- The Claude Code hook only covers commits issued through the **Bash** tool specifically, since that's what `tool_input.command` requires to parse. A commit issued via a PowerShell tool call would be unchecked by this entry point. In practice this hasn't been a gap for this project's own workflow: every commit across the session this tool came out of went through Bash, including in a PowerShell-primary environment, because the heredoc pattern for multi-line commit messages is itself bash syntax.
- The `pre-commit` hook is client-side and bypassable with `git commit --no-verify`. Pair it with the CLI invoked in CI for actual enforcement, not just a local nudge.

## Where this came from

Extracted from the same discipline used building [`recurring-free-slots`](https://github.com/bhumik154/recurring-free-slots) and [`spiral-galaxy-ic`](https://github.com/bhumik154/spiral-galaxy-ic): verify a claim computationally before it ships, rather than trust it because it sounds right. This project targets the exact failure mode that kept showing up while building those two, a stated test count, or a hand-derived reference value, that turned out to be wrong until someone actually re-ran the check.

## License

MIT
