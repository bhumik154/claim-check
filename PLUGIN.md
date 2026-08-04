# claim-check as a Claude Code plugin

Installs claim-check as a `PreToolUse` hook, so a `git commit` issued through
the Bash tool is checked before it runs: if the commit message states a test
count and that count is wrong, the tool call is denied with the real numbers.

**If the message makes no test-count claim, nothing happens.** That rule is
non-negotiable and is what makes this safe to leave switched on.

## Install

```bash
claude plugin marketplace add bhumik154/claim-check
claude plugin install claim-check@claim-check
```

Then **restart `claude`**. See the next section, this is not optional.

### No `pip install` required

The plugin bundles its own source and runs it with whatever Python it can
find, because claim-check has zero runtime dependencies. The launcher looks
for an interpreter in this order, requiring 3.9+:

1. `$CLAIM_CHECK_PYTHON` (set this to pin a specific interpreter)
2. `py -3` (Windows)
3. `python3`, then `python`

If no usable interpreter is found, the hook exits silently and every tool call
proceeds untouched. A missing Python is never a reason to block your work.

## Hooks load only at session start

A newly installed or updated plugin is **silently inert** until you restart
`claude`. There is no error and no warning; the hook simply never fires.

To confirm it is actually loaded, make a commit with a deliberately wrong
count in a repo with a test suite and check that it gets denied:

```bash
git commit -m "9999 passed"
```

## What it checks

The commit message is scanned for a test-count claim, `"22 passed"`,
`"15/17 passing"`, `"all tests pass"`, `"all 22 tests pass"`, and if one is
found, pytest is run and the claim compared against its summary line.

| Situation | Result |
|---|---|
| No claim in the message | Allowed, always |
| Claim matches the real run | Allowed |
| Claim contradicts the real run | **Denied**, with the actual numbers |
| pytest crashed, timed out, or could not be run | Allowed, with a warning |
| The command is not a `git commit` | Allowed |

Everything uncertain resolves to "allowed". A parse failure is not evidence a
claim is wrong; it is evidence the claim could not be checked.

## What it records

The plugin also installs a `PostToolUse` hook that watches for pytest runs you
make through the Bash tool and records their results.

**It emits nothing, blocks nothing, and changes no behaviour.** It exists so a
future check can compare a claim against a run that actually happened, instead
of paying for a second full suite run to find out.

Recorded per run: the counts from the summary line, the exact command, the
working directory, the session id, and a cheap fingerprint of the source tree
(file paths, sizes and mtimes, never contents).

Two things about how that evidence is treated:

- **Staleness only ever downgrades evidence to "unknown".** If the tree
  changed, the record aged out, or the file is unreadable, the answer is "no
  usable evidence", never "the claim is false". A bug in the cache can cost
  you a missed catch; it cannot produce a false accusation.
- **Scope-narrowing runs are marked unusable for whole-suite claims.** A run
  with `-k`, `-m`, `-x`, `--lf`, or an explicit path reports a true tally for a
  *subset*. Treating that as whole-suite evidence would let `pytest -k thing`
  confirm "all tests pass" while the real suite is red, which is worse than
  not checking at all.

Evidence lives outside your repository, under `%LOCALAPPDATA%\claim-check\`
(Windows) or `~/.cache/claim-check/` (Unix), keyed by project path and session.
Keeping it out of the working tree is deliberate: inside the repo it would be a
file the agent under verification could simply edit.

Delete that directory at any time; it is a cache, and losing it only means the
next claim goes unverified rather than wrongly flagged.

## What it checks at the end of a turn

A `Stop` hook also checks claims made **in conversation**, which is the more
common case than a commit message: an agent writing "all tests pass" in a
summary, having run nothing.

**It reports and never blocks.** If the last thing the agent said contradicts
a real test run from earlier in the same session, you get a note. Nothing is
denied, nothing is retried.

**It never runs pytest.** It compares against what the observer already
recorded. Running a suite at the end of every turn would make this
unusable.

The bar for saying anything is deliberately high. All of these must hold:

- the final message asserts a count, after quoted output is stripped out
- fresh evidence exists from this same session
- that run covered the whole suite, not a `-k` filtered subset
- that run reported no errors
- and the claim actually contradicts it

Anything else is silence. Measured across 43 real sessions, claims appear on
1.8% of turns, so the quiet path is nearly all of them.

Quoted output is stripped first because agents paste pytest's summary line
constantly, and flagging someone for accurately quoting output would be the
worst kind of false positive. Measured across 5,724 real assistant turns,
that stripping removes 3% of raw detections and keeps the rest.

Set `CLAIM_CHECK_VERBOSE=1` if you also want a note when a claim could not be
checked at all. It is off by default because roughly half of claims have no
usable evidence, and a steady stream of notes you can do nothing about is how
a tool gets uninstalled.

## Configuration

The hook reads no config file. To change its behaviour, edit the `command`
in `hooks/hooks.json` and append flags:

```
"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd" claude_hook --command "poetry run pytest" --timeout 60
```

- `--command`, override the test runner. Needed when your project's
  dependencies live in an environment that is not the one the hook's Python
  can see (poetry, hatch, pipenv, a container). Without it, the hook may find
  no pytest at all and silently verify nothing.
- `--timeout`, seconds before the run is killed and the commit allowed
  through (default 120). Must be greater than zero; `0` and negative values
  are rejected, because a non-positive timeout kills every run instantly and
  silently verifies nothing forever.

If you change the timeout, keep the `timeout` field in `hooks.json` above it.
A hook killed at its own timeout produces no result at all.

## Verifying your setup

Run this from the same shell you use for `git commit`:

```bash
claim-check verify-tests "22 passed"
```

If it prints `WARNING - could not verify` with a reason naming a missing
module or command, the hook will have the same problem, and `--command` is
the fix.

## Known limits

These are real and documented rather than hidden. The
[README](README.md#what-this-is-not) covers all of them, but the two that
matter most for the plugin:

- **It only sees commits issued through the Bash tool.** A commit made
  through a different tool, or by you in a terminal outside Claude Code, is
  not checked by this hook. Pair it with the `pre-commit` integration for
  that.
- **It cannot know what "the full suite" means to you.** It compares your
  claim against whatever pytest collects for its own invocation, in its own
  directory. If your claim is about a broader scope than the hook's run, it
  will report a mismatch on an honest claim. Read
  [Read this before you rely on it](README.md#read-this-before-you-rely-on-it)
  before installing.

## Troubleshooting

**Nothing happens on a wrong count.** Restart `claude`. Hooks load only at
session start. If it still does nothing, check that a test-count claim is
actually present in the message, no claim means no check, by design.

**Every commit prints "could not verify".** The hook's Python cannot find
pytest, or cannot find your project's test dependencies. Use `--command`.

**Debugging the raw payload.** Set `CLAIM_CHECK_DEBUG_DUMP` to a directory to
write each hook payload there as it arrives.
