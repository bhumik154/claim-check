# Make the README's factual claims testable

## Context

This project exists to catch test-count claims that do not match reality.

v0.6.0 shipped a README claiming the suite had **370** cases when it had
**419**, and a bullet reading **"pytest only, for now"** long after vitest and
jest landed, contradicting the first line of the same file. The count had been
updated correctly in every prior release, 292 to 340 to 370, and was missed
once. Worse, the release process recorded it as "README count verified with
claim-check" when what had been verified was a number in a commit message, not
the sentence in the README.

Documentation drift has now happened seven times here:

| # | drift | caught by |
|---|---|---|
| 1 | GitHub repo description stale since v0.4.0 | nothing, shipped |
| 2 | README "pytest only, for now" | nothing, shipped |
| 3 | README case count 370 against a real 419 | nothing, shipped |
| 4 | README limitations bullet asserting the opposite of shipped behaviour | review |
| 5 | PLUGIN.md "Anything else is silence" | review |
| 6 | `claude_stop_hook.py` docstring saying the same | review |
| 7 | `transcript.py` docstring saying whole-file reads were impossible | review |

The split is not random. Every prose contradiction was caught by a reviewer.
Every mechanical fact that drifted, a number and a list, **shipped**.
Reviewers read prose carefully and do not recompute numbers.

So this targets the mechanical class specifically, and claims nothing more.

## What is checked

### Syncable, rewritten by `--fix`

| fact | source of truth | sites |
|---|---|---|
| `has N cases` | `pytest --collect-only -q` | README |
| version strings | `claim_check.__version__` | README x2, `examples/pre-commit-config-snippet.yaml` |
| supported-runner listing | the README tagline | README limitations bullet |

The count is **collected**, not passed. Both were 419 when this was written,
but they diverge the moment a test is skipped or xfailed, and "cases" means
what the suite contains.

The count comes from a subprocess rather than the running session's own
`testscollected`, because `pytest -k something` collects a handful. A check
that trusted the in-process number would be fooled by how its own run was
narrowed, which is the same scope problem the product itself solves.

### Assert-only

A mismatch in these may mean the code changed behaviour, which is a bug to
investigate rather than stale prose to overwrite. So they fail; they are never
rewritten.

- Both example messages regenerate from real code and compare
  whitespace-normalised, since the README hard-wraps.
- The embedded python block matches the real source of
  `test_all_tests_pass_claim_with_zero_collected_tests_is_flagged_not_silently_matched`,
  body and comments included.
- Every `claim-check*` command invoked in a bash block exists in
  `[project.scripts]`, and the plugin install identifier matches the two
  `.claude-plugin` manifests.

## Two implementations that looked right and were not

Both were found by watching a simpler version fail during design, not by
foresight. Both are pinned by tests so they cannot be "simplified" back.

**Fenced blocks need a line parser.** The obvious regex, a non-greedy match
between triple backticks, pairs a CLOSING fence with the next OPENING one and
returns the prose in between. It did exactly that on the first attempt.

**The runner list needs a canonical fragment, not a set of mentioned names.**
The bullet that shipped broken was:

> **pytest only, for now.** vitest, jest and cargo test are a real gap

That sentence MENTIONS all three runners. Any check that extracts the set of
runner names finds `{pytest, vitest, jest}`, compares it against the tagline,
agrees, and passes while the README contradicts itself. The working approach
derives the listing from the tagline and requires the exact string
`**pytest, vitest and jest only.**` at the bullet.

`test_a_set_of_mentioned_runner_names_would_NOT_have_caught_it` exists to
document this, so a later simplification has to argue with a failing test
rather than with a comment.

## Failure behaviour

**A missing anchor is a hard failure, never a skip.** Every check locates text
by pattern. If the README is reworded so a pattern stops matching, treating
that as "no drift" leaves something that looks like protection and is not,
which is the silence-indistinguishable-from-success problem this project
exists to attack. More than one match is also a failure: two matches mean one
would be checked and the other left free to rot.

**`--fix` resolves every anchor before writing anything**, and refuses
entirely if one is unlocatable. A fixer that cannot find a fact must not guess
where it belongs, and must not half-apply the others on the way to discovering
that.

**Failure messages name the remedy.** Whoever trips this is in the middle of
something else, usually adding a test. Both the script and the pytest
assertion print the resync command.

## Every check is proven to fail

Each fact has two tests: one that it holds today, one that it **fails**
against drift. The drift fixtures are the real historical strings, `370` and
the verbatim broken bullet, not invented ones.

Beyond unit tests, acceptance required mutating the real README to each
historical bad value, running the named test, and confirming it fails. Nine
cases, nine caught, working tree clean afterwards. A green suite alone proves
nothing here, since a check that cannot fail passes forever.

## Limits, recorded so nobody assumes more coverage than exists

- **This catches 2 of the 7 documented drift cases.** They are the two that
  shipped. The other five were prose, and prose remains a reviewer's job.
- **PLUGIN.md is out of reach.** It carries no version strings, no counts and
  no runner lists, so there is nothing mechanical to check. Both of its
  historical failures were prose contradictions.
- **Both runner sentences wrong together is undetectable.** With no runner-name
  registry in the code, the README is its own source of truth. Adding a fourth
  runner and updating neither sentence passes.
- **The version check proves internal consistency only.** Install instructions
  name a git tag; this compares them against `__version__`. The version is
  bumped in the release commit and tagged afterwards, so a correctly synced
  README legitimately names a tag that does not exist yet. That window is what
  produced a broken install instruction at v0.5.0. The drift report says this
  out loud rather than implying a guarantee it does not provide.
- **Every commit that adds a test turns the suite red** until `--fix` runs.
  That is the intended cost and the reason the count rotted, but it is real
  friction and it will be felt on every future task.

## Deliberately not built

- **A CLI flag check.** The README documents
  `claim-check verify-tests "22 passed" --cwd backend/ -k "not slow"`, where
  `-k` is deliberately not a claim-check flag but a documented pass-through to
  pytest. The check would flag correct documentation as broken. Zero
  historical failures justify it, and a false-positive-prone checker is
  self-defeating in a project whose first rule is never produce a false
  positive.
- **A runner-name registry in `runners.py`.** `_PARSERS` holds two parsers
  covering three names with no mapping, so there is nothing to sync from.
  Adding one is a production change made to serve documentation. The cost is
  the undetectable case listed above, accepted knowingly.
- **Executing the documented install commands.** It would make the suite
  depend on GitHub being reachable.
- **Checking the GitHub repo description**, which drifted the same way and is
  the one shipped failure this does not cover. It lives outside the repo and
  would need a network call and a token.
