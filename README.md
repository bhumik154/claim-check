# claim-check

[![CI](https://github.com/bhumik154/claim-check/actions/workflows/ci.yml/badge.svg)](https://github.com/bhumik154/claim-check/actions/workflows/ci.yml)

Checks whether the test count in your commit message is actually true. pytest, vitest and jest.

If a commit message says "22 passed" or "all tests pass", claim-check runs your suite and compares the numbers. If they don't match, the commit is blocked. If the message doesn't mention tests at all, nothing happens.

As a Claude Code plugin it also checks claims made in conversation, by comparing them against test runs the agent actually made. That part reports and never blocks.

When it cannot check a claim it says so instead of going quiet, because a tool
that is silent when it verified and silent when it had nothing to check is
indistinguishable from one that is broken. The note names the cause and how
the session is going so far:

```
claim-check: could not verify "all tests pass". No usable whole-suite test run
for this claim: either none was recorded in this session, or the most recent
one is too old, unreadable, or recorded against a different state of the tree,
or runs in more than one project leave it unclear which one the claim is
about. (this session: 2 of 6 claims had a whole-suite run behind them; 3 had
no test run at all)
```

It says "had a whole-suite run behind them" rather than "verified" on purpose.
That count is reconstructed from the session transcript, which cannot see
whether the source tree changed after the run, so it is a weaker statement
than the one the commit gate makes. It only speaks up on a turn that states a
test-count claim; a turn that says nothing about tests produces no output.

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
    rev: v0.6.1
    hooks:
      - id: claim-check
```

Two things that will otherwise bite you:

The `commit-msg` stage isn't part of pre-commit's default install. Run `pre-commit install --hook-type commit-msg`, or put `default_install_hook_types: [pre-commit, commit-msg]` in your config. Without that the hook installs and then silently never runs.

The hook uses `language: system` rather than `language: python`, on purpose. A `python` hook runs inside pre-commit's own isolated virtualenv, which has none of your test dependencies and often not even pytest. That doesn't error; it just prints "could not verify... allowing commit" forever, because a runner crash fails open by design. So `claim-check` and your test dependencies need to be installed in the same environment you actually run `git commit` from.

### As a CLI

Not on PyPI yet, so install from the repo:

```bash
pip install git+https://github.com/bhumik154/claim-check@v0.6.1
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

## Scope: what it can and cannot know

**claim-check compares your claim against whatever your runner collected when *it* ran.** A claim is about a scope; the tool sees one invocation. Those can differ.

The awkward part is that they are indistinguishable from the numbers alone. "You wrote the wrong number" and "I ran a narrower scope" produce byte-identical output: a claim of 150 against a run collecting 40 looks the same either way. So scope cannot be recovered by arithmetic, and the tool does not try.

Instead it looks at the invocation it actually made. When the run was narrowed, by a `-k` or `-m` filter, an explicit path, `-x`, `--lf`, `--shard`, a vitest `-t`, a jest `--onlyChanged`, it says so and declines to judge rather than guessing:

```
claim-check: WARNING - could not verify "All 22 tests pass": this run saw 1
where the claim is about 22, and this run was narrowed by 'sub/', so it
covered less than the whole suite
```

That cuts both ways, and the second direction matters more than it looks. A narrowed run cannot **confirm** a whole-suite claim either. `pytest -k test_a` on a file whose `test_b` fails reports `1 passed, 1 deselected`, so "all tests pass" is perfectly true of what ran while the suite is red. Confirming there would launder a false claim into a verified one, so it abstains.

It also reads `deselected` straight out of pytest's own output, which catches filtering configured in `pytest.ini` rather than passed on the command line. That signal is pytest-only; vitest and jest never report it.

**None of this weakens the ordinary case.** A run that was not narrowed is judged exactly as strictly as before, so "all 22 tests pass" against a real 21 is still caught.

### What it still cannot see

- **Directory-level narrowing.** A `--cwd` into one package of a monorepo covers less than the repo, but a hook legitimately passes a `cwd` on every run, so treating that as narrowing would abstain always. Undetected on purpose.
- **`testpaths` and `--ignore` in your config**, when nothing is deselected. Those files are never collected, so there is no signal in the output at all.
- **A bare "all tests pass" with no number**, combined with either of the above.

If your setup hits these, run claim-check against the same scope your claim is about, and check its `cwd` and your project's own test-discovery config.

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

[`tests/`](tests/) has 496 cases covering the claim parser, the pytest and JavaScript output parsers, the shell-command parser, the comparison policy, the subprocess runner, the evidence store, all five entry points, and the plugin manifest.

This file is tested too. `scripts/readme_facts.py` pins the case count above, the version strings in the install instructions, and the supported-runner list, and it checks that the example messages below still regenerate from real code. Run `python scripts/readme_facts.py --fix` when a change makes one of them stale. It covers mechanical facts only: prose that quietly starts contradicting the code is still on a reviewer to catch.

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
| A full-suite claim against a deliberately scoped run | Abstains rather than judging, in both directions: a narrowed run can neither contradict nor confirm a claim about the whole suite |
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
| A claim in a chat message beside pasted pytest output | The quotation is stripped before anything is looked for. Measured across 5,724 real assistant turns, that removes 3% of raw detections, all of them the agent quoting output rather than asserting a number |
| A 20 MB session transcript | Only the last 256 KB is read, in under 2 ms. Reading the whole file at the end of every turn is not an option |
| vitest and jest tallies, captured from real runs | Both print a file-level tally *before* the test-level one (`Test Files`, `Test Suites`). Reading the first match reports a file count as a test count, so only the `Tests` line is ever used |
| A runner whose own stated total disagrees with the parts | Rejected outright. Both JS runners state their total, so an unrecognised label makes the arithmetic disagree, and disagreement returns nothing rather than an undercount |
| `npm test`, `yarn test`, `make test` | Marked scoped. They may run everything, but the runner and its arguments are hidden, and unknown scope is never usable as whole-suite evidence |
| `npx jest -t login`, `npx vitest run --changed`, `npx jest --onlyChanged` | Marked scoped, per each runner's own narrowing flags |
| `pytest -q; echo exit=$?`, `pytest -q && git status`, captured from real sessions | Recorded. A quiet summary followed by other output used to be dropped silently, about a quarter of real runs. It now counts when the command ran pytest and the output holds exactly one tally; several mean separate suites ran, and that is refused rather than guessed |
| `cd project && pytest`, then a claim made from the parent folder | The run is filed under `project` and found from a folder above or below it. Fresh runs in two projects under the same folder leave the claim unverified, never checked against the wrong suite. The commit hook also runs its suite in `project`: from the parent it used to collect every project there, hit collection errors, and deny a true claim |
| `pytest -q;echo`, `2>/dev/null` before pytest, a heredoc commit message containing an apostrophe | Scope still read correctly. Each of these used to make a whole-suite run look narrowed or unparseable |
| A nonexistent `--cwd`, an unbalanced quote in `--command`, a malformed hook payload | All fail open with a specific reason. Every entry point also has a catch-all, so no internal error can block a commit |

## What this is not

**It checks truthfulness, not whether your tests pass.** If you write "14/15 passing" and the suite really is 14 passed and 1 failed, that's accurate, so the commit goes through with a test failing. Most people assume a hook that runs pytest blocks red builds; this one only blocks claims that don't match reality. If you want red builds blocked, use a pre-push hook or CI alongside this, not instead of it.

**It still doesn't know what "the full suite" means to you.** It knows when its own run was narrowed and abstains there, but directory-level narrowing and `testpaths` restrictions leave no trace in the output, so those go unnoticed. See the scope section above.

**A claim written like output is ignored, including a true one.** Any line carrying both a count and a duration, `443 passed in 25.68s`, is treated as pasted runner output and dropped before anything is checked. So are fenced blocks, indented lines, blockquotes, and vitest or jest tallies. That is deliberate: quoting a summary line is not asserting it, and before the stripping existed roughly 3% of detections were an agent quoting its own test output back. The cost is real though. A genuine claim in that shape goes unchecked, and because the whole line is dropped, so does a real claim that happens to share a line with a pasted duration. It also means the cheapest way to slip a count past this tool is to format it like output. If you want a claim checked, write it as prose: "all 443 tests pass", not "443 passed in 25.68s".

**It isn't NLP.** It can't resolve tense or discourse. "Was at 15 passed, now 22/22" is genuinely ambiguous to a regex. Only one claim per message gets checked: the most specific kind, then the last one among ties. That covers a stale count corrected later, but a later sentence describing an earlier state can still slip through.

**pytest, vitest and jest only.** `cargo test`, `go test` and the rest are a real gap, documented rather than silently unsupported.

**The Claude Code hook only sees commits made through the Bash tool**, since that's what carries a parseable command string. A commit through some other tool goes unchecked. In practice this hasn't been a gap, because the heredoc pattern for multi-line commit messages is bash syntax anyway.

**Only `git commit` in the form `git [global options] commit`.** A leading wrapper like `sudo git commit` or `env FOO=1 git commit` isn't recognised and goes unchecked.

**The pre-commit hook is client-side and `--no-verify` skips it.** Pair it with the CLI in CI if you want actual enforcement rather than a local nudge.

**A test that forges a whole pytest session can still fool it.** Output printed by a test gets replayed in the failure report, which always comes before that session's real summary, so the last-line-per-session rule handles the realistic case. The gap that remains depends on ordering. A forged summary line printed *before* a forged `test session starts` header is believed, because the forged header closes the segment right after the forged line and its count gets added to the real one. The reverse order is safe: both summaries land in the same segment and the real one still comes last. Both orderings are pinned by tests. Anything that can make a test print output in that exact order can already edit the test suite directly.

**The end-of-turn check reports, it does not block.** When it lacks fresh, whole-suite, same-session evidence from a run the agent itself made, it cannot check the claim, and it says so rather than checking it - naming the reason and, when it can compute one, the session ratio. Roughly half of claims still go unchecked; what changed is that you now find out which ones. That is deliberate: unknown is never reported as wrong.

**An honest claim that mentions an error still gets flagged.** `"22 passed, 1 error"` against a real 22-passed-1-error run is reported as a mismatch, because an error means some test never ran and the true denominator is unknown. That strictness is what makes a bare count mean anything.

## Where this came from

The same habit used building [recurring-free-slots](https://github.com/bhumik154/recurring-free-slots) and [spiral-galaxy-ic](https://github.com/bhumik154/spiral-galaxy-ic): check a claim computationally before shipping it, rather than trusting it because it sounds right.

Building those two, the test count drifted constantly as fixes landed. More than once a stated number was simply wrong, a "22 passed" that was really 21, or a golden reference value computed by hand that turned out to be off, and it stayed wrong until someone re-ran the check. That happened to careful, deliberate work. It happens far more easily in a commit message written at the end of a long change, by a person or by an AI agent, with nothing actually re-running the suite.

## License

MIT
