# claim-check

Verify test-count claims in commit messages against real pytest output, as a pre-commit hook, a Claude Code hook, or a standalone CLI.

## The problem

Across a whole session of building two other small Python and TypeScript packages, the exact test count drifted repeatedly as fixes landed. More than once, a stated or hand-derived number was simply wrong, a "22 passed" that was actually 21, a golden reference value computed by hand that turned out to be off, until it got manually re-verified. That happened to careful, deliberate work. It happens far more easily in a commit message dashed off at the end of a long change, by a human or by an AI coding agent, "22 tests pass," "17/17 passing," "all tests pass," with nothing actually re-running the suite to check.

This tool does exactly that check, mechanically, every time: it looks for a test-count claim in a commit message, and if one is there, it verifies it against pytest's own summary line before the commit lands. If there's no claim, it does nothing, checking a claim that was never made is exactly the kind of false positive that gets a tool uninstalled.

## Tested against exact output, not just shape

[`tests/`](tests/) has 223 cases across the claim parser, the pytest-output parser, the shell-command parser, the comparison policy, the subprocess runner, the evidence store, all four entry points, and the plugin manifest. One example, from the comparison core:

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
| A claim repeated with conflicting numbers, whether restated in the same phrasing or a different one (`"14 passed"` later corrected to `"15/15 tests pass"`) | Only one claim in the entire message is checked: the most specific kind present (`n_of_m` > `all_pass` > `n_passed`), and among claims tied on specificity, the last one in the text. This is the "stale count restated later" scenario this project exists for; checking both would flag an honest correction as a contradiction, since one pytest run can't make two different counts both true |
| A later, less specific claim next to an earlier, more specific (and false) one (`"50/50 tests pass"` followed later by the technically-true-but-vaguer `"All tests pass now."`) | The more specific `n_of_m` claim wins regardless of position, not whichever claim happens to be last in the text; a real 3-passed/0-failed run does not verify the false `50/50` claim just because a weaker, later sentence is also true of that run |
| `"not 22 passed"`, `"not 15/15 tests passing"` (negation directly adjacent to a bare-count or ratio claim, not just `"all tests pass"`) | Never registers as a claim, same guard as the `all_pass` negation case, applied to every claim kind |
| pytest's real summary line, in every shape it takes (plain, with a failure, with a warning, with skips/xfails/xpasses, `no tests ran`, ANSI-colored) | Parsed from pytest's own authoritative tally, never re-derived by counting individual result lines |
| Real captured pytest-xdist output | Confirmed against an actual `pytest -n 2` run, not a guess: xdist prints exactly one final aggregate summary line, never per-worker partials |
| Multiple summary lines from separate invocations piped together (`pytest tests/unit && pytest tests/integration`) | Aggregated into a combined total, not "last one wins" - a claim like "all 100 tests pass" refers to the sum, not whichever suite happened to run last |
| A `no tests ran` line, which the generic summary regex also matches at the same position | De-duplicated explicitly; without it, aggregation counted that one line twice |
| An `INTERNALERROR>` crash with no summary line at all | Structurally distinct from "0 tests ran"; fails open, does not block the commit |
| The exact `git commit -m "$(cat <<'EOF' ... EOF)"` heredoc pattern this project's own commits use | Verified end to end: extracted correctly, and a wrong claim inside it is caught |
| Unbalanced quotes, an unresolved `$VAR` inside an unquoted heredoc, `git commit` as a substring inside an unrelated `echo` | All fail open (or are correctly ignored), a parse failure is not evidence a claim is wrong |
| A full-suite claim checked against a deliberately scoped run (one file passed via `pytest_args`, or a `-k` filter that deselects a failing test) | Documents the real, confirmed partial-run limitation described at the top of Usage, this is expected, not a bug |
| `--command` pointed at a wrapper (`poetry run pytest`) that doesn't exist on the machine | Fails open with the specific missing command named, never an unhandled crash |
| A hanging test run | Killed at the configured `--timeout`, fails open with a clear reason instead of blocking a commit (or an interactive rebase) forever |
| A bare `"22 passed"` claim against a real run that also reported a collection or fixture-setup error | Flagged as a mismatch, not silently matched: an error means some test never got a chance to pass or fail, so the true denominator is unknown even though the passed count is technically accurate |
| `claim-check verify-tests MSG --cwd X --command Y` (claim-check's own flags placed *after* the positional message) | Confirmed correctly parsed regardless of position; a prior version of this parser using `argparse.REMAINDER` silently swallowed everything after the message into pytest passthrough args instead |
| A test suite output containing a byte invalid in the platform's default text encoding (confirmed on Windows with `pytest -s`, where the locale default is often `cp1252`, not UTF-8) | Decoded as UTF-8 with invalid bytes replaced, not left to crash the whole process; the default (non-`-s`) case is already safe, since pytest's own capture machinery re-encodes captured output before ever printing it |
| A commit message file containing a byte invalid in UTF-8 (a pasted binary character, a stray `cp1252` quote mark from Word) | Read with invalid bytes replaced rather than crashing the CLI outright |
| `--pytest-output` pointed at a file that doesn't exist (a CI pipeline where the step that should have produced it failed) | Fails open with a specific warning naming the missing file, not an unhandled traceback |
| `claim-check-precommit` invoked manually with a nonexistent or unreadable commit-message path | Fails open with a clear message, not an unhandled `FileNotFoundError` |
| `claim-check-claude-hook` invoked manually in an interactive terminal, with no piped input | Exits immediately instead of hanging on `stdin.read()`; never triggers under real Claude Code use, since a piped stdin reports `isatty() == False` |
| A ratio claim spelled `"22/22 passed"` rather than `"22/22 tests pass"` | Both are the same claim and both check the denominator; the bare-count regex matches the contained substring `"22 passed"` at a later offset, and last-start-wins used to pick it, silently dropping the total |
| `"not 22/22 passed"`, `"never 9/9 passed"` (negation before a ratio spelled `passed`) | Never registers as a claim; enclosed matches are discarded before the negation filter runs, or the contained bare-count match survives a negation that applied to the phrase as a whole |
| `"1,022 passed"` (a grouped thousands separator) | Not read as a claim of 22; the lookbehind blocks `,` alongside `.`, `#` and digits |
| A summary line printed by a test and replayed in pytest's failure report | Cannot forge the tally on its own: the last summary line *per session* is authoritative, and a test's output is always replayed before its own session's summary. A test that also forges a session header can still do it; see "What this is not" |
| A line shaped like `"deploy finished in 1.2.3s"` in captured output | Not a summary line, and never a crash; the duration pattern is a strict number rather than any run of digits and dots |
| A 60,000-character separator line in captured output | Parsed in linear time; the previous pattern backtracked quadratically and took 12 seconds inside the commit-msg hook, which `--timeout` does not cover |
| `git -C <path> commit`, `git --no-pager commit` | Verified; git's own global options are skipped when locating the subcommand |
| A nonexistent `--cwd`, an unbalanced quote in `--command`, a malformed Claude Code hook payload | All fail open with a specific reason; every entry point also has a catch-all so no internal error can ever block a commit |

## Usage

> **Read this before you install it, or it will falsely block a correct commit and you'll (reasonably) rip it out.**
>
> claim-check can only ever compare a claim against whatever pytest actually collects for the exact invocation it runs, in the exact directory and with the exact arguments it's given. It has no way to know what "the full suite" means to you.
>
> If you write "all 150 tests pass" because you genuinely ran the complete suite yourself, but claim-check's own run is narrower, because a pre-commit "only test changed files" setup passed it one file, a `pytest.ini`/`pyproject.toml` `testpaths` restriction scoped discovery to a subdirectory, a sharded CI job only ran its own slice, or you invoked the CLI with extra `pytest` args, it will see far fewer tests than 150, and it will flag your entirely honest claim as a mismatch. This is confirmed, reproducible behavior (see `test_scoping_pytest_args_to_one_file_produces_a_false_mismatch_against_a_full_suite_claim` in the test suite), not a hypothetical edge case.
>
> The same risk runs the other way too, and is easier to miss: a `-k` filter doesn't just narrow what's checked, it changes what "all tests pass" means. `pytest -k test_a` on a file with a passing `test_a` and a failing `test_b` reports `1 passed, 1 deselected`, a genuinely correct "all tests pass" for that scope, even though the real suite has a failure. Deselected tests are tracked separately and correctly excluded from the total (that part is right), but that's exactly what makes this spoofable: if claim-check's own invocation ends up narrower than the claim, through a stray `-k`, not just a path, it will happily confirm a claim that a full run would have contradicted. Confirmed directly (see `test_k_flag_deselection_produces_a_false_match_against_an_all_pass_claim`). Same rule applies: make sure claim-check's own invocation matches the scope the claim is actually about.
>
> **Always run claim-check against the same scope your claim actually refers to.** If your commit message claims something about the full suite, make sure claim-check's own invocation (its `cwd`, any extra `pytest` args, and your project's own `pytest.ini`/`pyproject.toml` test-discovery config) actually covers the full suite too, not a subset.

**Standalone CLI:**

```bash
claim-check verify-tests path/to/COMMIT_EDITMSG
# or check a literal message directly:
claim-check verify-tests "22 passed"
# reuse output you already captured instead of re-running the suite:
claim-check verify-tests path/to/COMMIT_EDITMSG --pytest-output pytest_output.txt
# claim-check's own flags (--cwd, --command, --timeout, --pytest-output) can go
# before or after the message; anything else is passed through to pytest itself:
claim-check verify-tests "22 passed" --cwd backend/ -k "not slow"
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

**Claude Code plugin** (recommended — see [`PLUGIN.md`](PLUGIN.md)):

```bash
claude plugin marketplace add bhumik154/claim-check
claude plugin install claim-check@claim-check
```

Then restart `claude`; hooks load only at session start, and until you restart the plugin is installed but silently inert. The plugin bundles its own source and runs it with any Python 3.9+ it can find, so **no `pip install` is required** — claim-check has zero runtime dependencies. If no usable interpreter exists, the hook exits silently and every tool call proceeds untouched.

**Claude Code hook, wired by hand** (if you'd rather not install a plugin — see [`examples/claude-code-settings-snippet.json`](examples/claude-code-settings-snippet.json)), in `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "claim-check-claude-hook", "if": "Bash(git commit *)" }] }
    ]
  }
}
```

This form needs `claim-check` on your `PATH`, so it does require installing the package.

### If claim-check's own environment isn't your project's environment

`language: system` (above) fixes the case where the hook runs in pre-commit's isolated virtualenv instead of yours. There's a deeper version of the same problem even outside pre-commit: by default, claim-check runs `<the Python it's installed under> -m pytest`. If claim-check itself is installed separately from your project, a pipx-style global install, or a Claude Code hook running under a different interpreter than your `poetry`/`hatch`/`pipenv`-managed project, that command can point at a Python with no pytest, or a pytest with none of your project's actual dependencies. Confirmed directly: in that situation this fails open silently, "could not verify... allowing commit", exactly as if no verification tool were installed at all, with no error to tip you off.

All three entry points take a `--command` override for this: point it at whatever actually runs your tests in your real project environment.

```bash
claim-check verify-tests COMMIT_EDITMSG --command "poetry run pytest"
claim-check-precommit COMMIT_EDITMSG --command "hatch run test"
claim-check-claude-hook --command "poetry run pytest"   # add as extra args after the hook command in settings.json
```

If you're not sure whether this applies to you: run `claim-check verify-tests "22 passed"` (or any dummy claim) from the same shell you actually run `git commit` from. If it prints a `WARNING - could not verify (...)` with a reason mentioning a missing module or command, that's this problem, and `--command` is the fix.

### Timeouts

Every pytest invocation is killed after 120 seconds by default (`--timeout`, in seconds, on all three entry points), and fails open with a clear reason if it fires. This exists specifically for the `commit-msg` hook: it runs synchronously on every commit, including every commit replayed during an interactive rebase, so a hung or unexpectedly slow test run doesn't silently block one commit, it blocks the whole rebase indefinitely with no visible cause. 120 seconds is deliberately generous rather than tuned to a fast unit suite, a large but normal integration suite can legitimately take minutes, and a timeout that's too short just makes this tool silently verify nothing for every such project. Lower it if your suite is fast and you'd rather fail loudly sooner:

```bash
claim-check-precommit COMMIT_EDITMSG --timeout 30
```

`--timeout` must be greater than zero; `0` and negative values are rejected with an error rather than accepted. This is a deliberate exception to this tool's fail-open rule: a non-positive timeout kills every run the instant it starts and then fails open, so the tool prints "could not verify ... allowing commit" on every commit forever while looking like it is working. Silently verifying nothing is worse than a loud error about a mistyped flag.

## Install

```bash
pip install claim-check
```

Zero runtime dependencies.

## What this is not

- **This tool verifies truthfulness, not test success.** It checks whether a claim matches the actual result, not whether the actual result is good. If you write "14/15 passing" and the suite genuinely has 14 passed and 1 failed, that claim is accurate, so this exits 0 and the commit proceeds, even though a test is failing. Most people reasonably assume a hook running pytest blocks a commit on any failure; this one only blocks on a claim that doesn't match reality. Confirmed directly: `verify_tests("WIP: 14/15 passing, one test is currently broken", ...)` against a real 14-passed-1-failed result returns a match, not a mismatch. If you want commits blocked on red builds specifically, use a standard pre-push hook (or CI) for that; pair it with this one rather than expecting this one to cover it.
- Not aware of what "the full suite" means to you, only what pytest actually collects for its own invocation. A scoped run (changed-files-only, `testpaths` restrictions, sharded CI) compared against a full-suite claim produces a false mismatch; see the warning at the top of Usage before you install this.
- Not NLP. It can't fully resolve tense or discourse ("was at 15 passed, now 22/22" is genuinely ambiguous to a regex); only one claim in the entire message is checked - the most specific kind present (`n_of_m` > `all_pass` > `n_passed`), falling back to the last one in the text among claims tied on specificity - which handles the common case (a stale count corrected later, in the same or different phrasing, or a vaguer restatement alongside a specific one) but not every one (a later sentence can still describe an earlier state).
- pytest only in v0.1. Other runners (vitest, jest, cargo test) are a real, documented gap, not a silently unsupported one.
- The Claude Code hook only covers commits issued through the **Bash** tool specifically, since that's what `tool_input.command` requires to parse. A commit issued via a PowerShell tool call would be unchecked by this entry point. In practice this hasn't been a gap for this project's own workflow: every commit across the session this tool came out of went through Bash, including in a PowerShell-primary environment, because the heredoc pattern for multi-line commit messages is itself bash syntax.
- The `pre-commit` hook is client-side and bypassable with `git commit --no-verify`. Pair it with the CLI invoked in CI for actual enforcement, not just a local nudge.
- Not proof against a test that deliberately forges a whole pytest session. Output printed by a test is replayed inside pytest's failure report, which always precedes that session's real summary line, so the last-line-per-session rule defeats the realistic case. The remaining gap is ordering-dependent, and only one order defeats it: a forged summary line printed *before* a forged `test session starts` header is believed, because the forged header closes the segment immediately after the forged summary, making it that segment's last line and adding its count to the real one. The reverse order is safe: a forged header printed *before* a forged summary leaves both the forged and the real summary inside the same segment, where the real one still comes last and wins. Both orderings are pinned by tests in `tests/test_pytest_parser.py`. Anything able to make a test print output in that specific order can already edit the test suite directly.
- Still flags an honest claim that names an error alongside its count. `"22 passed, 1 error"` against a real 22-passed-1-error run is reported as a mismatch, because an error means some test never ran and the true denominator is unknown. The strictness is deliberate: it is the guarantee that makes a bare count meaningful at all.
- Only `git commit` invoked as `git [global options] commit`. A leading wrapper (`sudo git commit`, `env FOO=1 git commit`) is not recognised and goes unverified.

## Where this came from

Extracted from the same discipline used building [`recurring-free-slots`](https://github.com/bhumik154/recurring-free-slots) and [`spiral-galaxy-ic`](https://github.com/bhumik154/spiral-galaxy-ic): verify a claim computationally before it ships, rather than trust it because it sounds right. This project targets the exact failure mode that kept showing up while building those two, a stated test count, or a hand-derived reference value, that turned out to be wrong until someone actually re-ran the check.

## License

MIT
