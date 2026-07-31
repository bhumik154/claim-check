# claim-check

[![CI](https://github.com/bhumik154/claim-check/actions/workflows/ci.yml/badge.svg)](https://github.com/bhumik154/claim-check/actions/workflows/ci.yml)

Checks whether the test count in your commit message is actually true.

If a commit message says "22 passed" or "all tests pass", claim-check runs pytest and compares the numbers. If they don't match, the commit is blocked. If the message doesn't mention tests at all, nothing happens.

Works as a pre-commit hook, a Claude Code plugin, or a plain CLI. No runtime dependencies.

## Install

Pick whichever fits how you work. They all use the same checking logic.

### As a Claude Code plugin

```bash
claude plugin marketplace add bhumik154/claim-check
claude plugin install claim-check@claim-check
```

Then restart `claude`. Hooks only load when a session starts, so until you restart it's installed but doing nothing, with no warning either way.

You don't need `pip install` for this one. The plugin ships its own source and runs it with any Python 3.9+ it can find. If it can't find one, the hook exits quietly and your tool calls carry on as normal. See [PLUGIN.md](PLUGIN.md) for the details.

### As a pre-commit hook

```yaml
repos:
  - repo: https://github.com/bhumik154/claim-check
    rev: v0.2.0
    hooks:
      - id: claim-check
```

Two things that will otherwise bite you:

The `commit-msg` stage isn't part of pre-commit's default install. Run `pre-commit install --hook-type commit-msg`, or put `default_install_hook_types: [pre-commit, commit-msg]` in your config. Without that the hook installs and then silently never runs.

The hook uses `language: system` rather than `language: python`, on purpose. A `python` hook runs inside pre-commit's own isolated virtualenv, which has none of your test dependencies and often not even pytest. That doesn't error; it just prints "could not verify... allowing commit" forever, because a runner crash fails open by design. So `claim-check` and your test dependencies need to be installed in the same environment you actually run `git commit` from.

### As a CLI

Not on PyPI yet, so install from the repo:

```bash
pip install git+https://github.com/bhumik154/claim-check@v0.2.0
```

```bash
claim-check verify-tests path/to/COMMIT_EDITMSG

# or check a message directly
claim-check verify-tests "22 passed"

# reuse output you already have instead of running the suite again
claim-check verify-tests path/to/COMMIT_EDITMSG --pytest-output pytest_output.txt

# claim-check's own flags can go before or after the message.
# anything else gets passed through to pytest.
claim-check verify-tests "22 passed" --cwd backend/ -k "not slow"
```

### Wiring the Claude Code hook by hand

If you'd rather not install a plugin, put this in `.claude/settings.json` (also in [examples/claude-code-settings-snippet.json](examples/claude-code-settings-snippet.json)):

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "claim-check-claude-hook", "if": "Bash(git commit *)" }] }
    ]
  }
}
```

This needs `claim-check` on your `PATH`, so it does require installing the package.

## Read this before you rely on it

**claim-check can only compare your claim against whatever pytest collects when *it* runs.** It has no idea what "the full suite" means to you.

Say you write "all 150 tests pass" because you genuinely ran everything. If claim-check's own run is narrower, because a "test only changed files" setup handed it one file, or `testpaths` in your config scoped discovery to a subdirectory, or a sharded CI job ran one slice, it sees far fewer than 150 tests and flags your honest claim as wrong. That's reproducible, not hypothetical (see `test_scoping_pytest_args_to_one_file_produces_a_false_mismatch_against_a_full_suite_claim`).

The same problem runs the other way, and it's easier to miss. A `-k` filter doesn't just narrow what gets checked, it changes what "all tests pass" even means. Run `pytest -k test_a` on a file with a passing `test_a` and a failing `test_b` and you get `1 passed, 1 deselected`, which is a perfectly correct "all tests pass" for that scope while the real suite is red. Deselected tests are tracked separately and kept out of the total, which is the right behaviour, and it's also exactly what makes this spoofable.

**So: run claim-check against the same scope your claim is about.** Check its `cwd`, any extra pytest args, and your project's own test-discovery config.

## Configuration

### When claim-check's environment isn't your project's

By default claim-check runs `<the Python it's installed under> -m pytest`. If claim-check lives somewhere else, say a pipx-style global install, or a Claude Code hook running under a different interpreter than your poetry or hatch project, that command can land on a Python with no pytest, or a pytest without your dependencies.

When that happens it fails open silently: "could not verify... allowing commit", on every commit, looking exactly like a tool that isn't installed.

Every entry point takes `--command` for this. Point it at whatever actually runs your tests:

```bash
claim-check verify-tests COMMIT_EDITMSG --command "poetry run pytest"
claim-check-precommit COMMIT_EDITMSG --command "hatch run test"
claim-check-claude-hook --command "poetry run pytest"
```

Not sure if this affects you? Run `claim-check verify-tests "22 passed"` from the same shell you commit from. If it prints `WARNING - could not verify (...)` mentioning a missing module or command, that's this, and `--command` fixes it.

### Timeouts

Every pytest run is killed after 120 seconds by default and fails open with a reason. That's mostly for the `commit-msg` hook, which runs synchronously on every commit including every one replayed during an interactive rebase. A hung suite there doesn't block one commit, it blocks the whole rebase with no visible cause.

120 seconds is deliberately generous. A large but perfectly normal integration suite can take minutes, and a timeout set too short just means the tool quietly verifies nothing. Lower it if your suite is fast and you'd rather fail loudly:

```bash
claim-check-precommit COMMIT_EDITMSG --timeout 30
```

`--timeout` has to be greater than zero. Zero and negative values are rejected outright, which is a deliberate exception to the fail-open rule everywhere else. A non-positive timeout kills every run the instant it starts, then fails open, so you get "could not verify... allowing commit" forever while everything looks fine. Verifying nothing silently is worse than a loud error about a typo.

## What it's tested against

[`tests/`](tests/) has 236 cases covering the claim parser, the pytest-output parser, the shell-command parser, the comparison policy, the subprocess runner, the evidence store, all four entry points, and the plugin manifest.

One example, from the comparison core:

```python
def test_all_tests_pass_claim_with_zero_collected_tests_is_flagged_not_silently_matched():
    # Vacuously true if you only check failed == 0; almost certainly not
    # what was meant by "all tests pass".
    verdict = compare_claims([_all_pass()], _counts(passed=0, failed=0))
    assert verdict.status == "mismatch"
```

The cases worth knowing about:

| Scenario | Why it's there |
|---|---|
| No claim in the message | Never flagged, whatever the tests did. The one rule that doesn't bend |
| `"N passed"`, `"N/M tests"`, `"N/M passing"`, `"all tests pass"`, `"all N tests pass"` | Every claim shape it detects, by regex, not NLP |
| A decimal (`"0.22"`) or an issue reference (`"#22"`) beside the word "passed" | Guarded so they're never read as counts |
| `"1,022 passed"` | Not read as 22. The lookbehind blocks `,` along with `.`, `#` and digits |
| `"not all tests pass yet"`, `"not 22 passed"`, `"not 15/15 tests passing"`, `"never 9/9 passed"` | Negation next to any claim shape, not just `"all tests pass"`. None of them register |
| A count restated with a different number (`"14 passed"` corrected later to `"15/15 tests pass"`) | Only one claim gets checked: the most specific kind present (`n_of_m` > `all_pass` > `n_passed`), then the last one in the text among ties. Checking both would flag an honest correction, since one run can't make two counts true |
| A vague later claim beside a specific false one (`"50/50 tests pass"` then `"All tests pass now."`) | The specific claim wins regardless of position. A real 3-passed run doesn't get to verify the false `50/50` just because a weaker sentence is also true |
| `"22/22 passed"` rather than `"22/22 tests pass"` | Same claim, same denominator check. The bare-count regex used to match the `"22 passed"` inside it and quietly drop the total |
| pytest's summary line in every shape (plain, failures, warnings, skips, xfails, xpasses, `no tests ran`, ANSI colour) | Read from pytest's own tally, never recounted from individual result lines |
| Real captured pytest-xdist output | Checked against an actual `pytest -n 2` run, not guessed. xdist prints one final aggregate line, no per-worker partials |
| Piped invocations (`pytest tests/unit && pytest tests/integration`) | Totals combined rather than last-one-wins, since "all 100 tests pass" means the sum |
| `INTERNALERROR>` with no summary line | Different from "0 tests ran". Fails open, commit proceeds |
| The `git commit -m "$(cat <<'EOF' ... EOF)"` heredoc pattern | Extracted correctly end to end, and a wrong claim inside one is caught |
| Unbalanced quotes, an unresolved `$VAR` in an unquoted heredoc, `git commit` inside an unrelated `echo` | All fail open or are ignored. A parse failure isn't evidence a claim is wrong |
| `git -C <path> commit`, `git --no-pager commit` | Checked. git's own global options are skipped when finding the subcommand |
| A full-suite claim against a deliberately scoped run | Pins the partial-run limitation described above. Expected, not a bug |
| A summary line printed by a test and replayed in the failure report | Can't forge the tally on its own. The last summary line *per session* wins, and a test's output always gets replayed before its own summary. See "What this is not" for the case that does get through |
| A line like `"deploy finished in 1.2.3s"` in captured output | Not a summary line, and not a crash. The duration pattern is a strict number, not any run of digits and dots |
| A 60,000-character separator line | Parsed in linear time. The old pattern backtracked quadratically and took 12 seconds inside the hook, which `--timeout` doesn't cover |
| `"22 passed"` against a run that also reported a collection or fixture error | Flagged, not matched. An error means some test never got to run, so the real denominator is unknown even if 22 did pass |
| A hanging test run | Killed at `--timeout` and fails open, rather than blocking a commit or an entire rebase indefinitely |
| `--command` pointing at a wrapper that isn't installed | Fails open naming the missing command, never an unhandled crash |
| Flags placed after the positional message (`verify-tests MSG --cwd X`) | Parsed correctly wherever they sit. An earlier version using `argparse.REMAINDER` swallowed them into pytest's args |
| Output containing a byte that's invalid in the platform encoding (real on Windows with `pytest -s`, where the default is often cp1252) | Decoded as UTF-8 with bad bytes replaced instead of crashing |
| A commit message file with invalid UTF-8 (a pasted binary character, a stray quote mark from Word) | Read with bad bytes replaced rather than crashing the CLI |
| `--pytest-output` pointing at a file that isn't there | Fails open naming the file, not a traceback |
| `claim-check-precommit` run by hand with a bad path | Fails open with a clear message, not a `FileNotFoundError` |
| `claim-check-claude-hook` run bare in a terminal with nothing piped in | Exits immediately instead of hanging on `stdin.read()` |
| A nonexistent `--cwd`, an unbalanced quote in `--command`, a malformed hook payload | All fail open with a specific reason. Every entry point also has a catch-all, so no internal error can block a commit |

## What this is not

**It checks truthfulness, not whether your tests pass.** If you write "14/15 passing" and the suite really is 14 passed and 1 failed, that's accurate, so the commit goes through with a test failing. Most people assume a hook that runs pytest blocks red builds; this one only blocks claims that don't match reality. If you want red builds blocked, use a pre-push hook or CI alongside this, not instead of it.

**It doesn't know what "the full suite" means to you**, only what pytest collects when it runs. See the warning above.

**It isn't NLP.** It can't resolve tense or discourse. "Was at 15 passed, now 22/22" is genuinely ambiguous to a regex. Only one claim per message gets checked: the most specific kind, then the last one among ties. That covers a stale count corrected later, but a later sentence describing an earlier state can still slip through.

**pytest only, for now.** vitest, jest and cargo test are a real gap, documented rather than silently unsupported.

**The Claude Code hook only sees commits made through the Bash tool**, since that's what carries a parseable command string. A commit through some other tool goes unchecked. In practice this hasn't been a gap, because the heredoc pattern for multi-line commit messages is bash syntax anyway.

**Only `git commit` in the form `git [global options] commit`.** A leading wrapper like `sudo git commit` or `env FOO=1 git commit` isn't recognised and goes unchecked.

**The pre-commit hook is client-side and `--no-verify` skips it.** Pair it with the CLI in CI if you want actual enforcement rather than a local nudge.

**A test that forges a whole pytest session can still fool it.** Output printed by a test gets replayed in the failure report, which always comes before that session's real summary, so the last-line-per-session rule handles the realistic case. The gap that remains depends on ordering. A forged summary line printed *before* a forged `test session starts` header is believed, because the forged header closes the segment right after the forged line and its count gets added to the real one. The reverse order is safe: both summaries land in the same segment and the real one still comes last. Both orderings are pinned by tests. Anything that can make a test print output in that exact order can already edit the test suite directly.

**An honest claim that mentions an error still gets flagged.** `"22 passed, 1 error"` against a real 22-passed-1-error run is reported as a mismatch, because an error means some test never ran and the true denominator is unknown. That strictness is what makes a bare count mean anything.

## Where this came from

The same habit used building [recurring-free-slots](https://github.com/bhumik154/recurring-free-slots) and [spiral-galaxy-ic](https://github.com/bhumik154/spiral-galaxy-ic): check a claim computationally before shipping it, rather than trusting it because it sounds right.

Building those two, the test count drifted constantly as fixes landed. More than once a stated number was simply wrong, a "22 passed" that was really 21, or a golden reference value computed by hand that turned out to be off, and it stayed wrong until someone re-ran the check. That happened to careful, deliberate work. It happens far more easily in a commit message written at the end of a long change, by a person or by an AI agent, with nothing actually re-running the suite.

## License

MIT
