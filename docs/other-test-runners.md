# Measured output formats from other test runners

Groundwork for supporting runners beyond pytest. Everything here was captured
from a real run, not read from documentation, because the last three times
something in this project was built on an assumed format the assumption was
wrong.

## vitest 2.1.9

Captured on Windows, Node 24.18.0.

### All passing

```
 Test Files  1 passed (1)
      Tests  3 passed (3)
   Start at  02:46:16
   Duration  577ms (transform 18ms, setup 0ms, collect 19ms, tests 1ms, ...)
```

### With a failure

```
 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 3 passed (4)
   Duration  559ms (...)
```

### No tests

```
No test files found, exiting with code 1
```

Not a tally at all. A prose sentence, structurally closer to pytest's
`INTERNALERROR` case than to `no tests ran`.

## How this differs from pytest, and why each difference matters

| | pytest | vitest |
|---|---|---|
| Tally lines | one | **two** |
| Separator | `, ` | `\|` |
| Total | implied by summing | explicit, in parentheses |
| Duration | on the tally line (`in 1.23s`) | on its own line |
| Nothing collected | `no tests ran in 0.01s` | a prose sentence |
| Colour | optional | heavy by default |

**Two tally lines is the trap.** `Test Files  1 passed (1)` counts *files*;
`Tests  3 passed (3)` counts tests. A parser that grabs the first match
reports 1 when the answer is 3. Any vitest support must pin to the `Tests`
line specifically, and a test should assert that a file count is never
reported as a test count.

**The explicit total is a genuine improvement.** pytest makes you sum
passed + failed + skipped + xfailed + xpassed and hope the label set is
complete. vitest states the denominator outright, which is exactly what an
`n_of_m` claim needs.

**Heavy default colour** means `strip_ansi` has to run first. It already
does, and it already handles these sequences.

## A live defect this measurement found

Before any of this was built, `conversation.py` already had a bug because of
it. Its quoted-output stripping was pytest-shaped: it required an
`in <duration>s` on the line. vitest's tally has no such duration, so:

- the `Tests` line was stripped, but only incidentally, because it happens to
  be indented six spaces and the indented-block rule caught it
- the `Test Files` line, indented by one space, survived

A pasted vitest tally therefore produced a claim of **"1 passed"** from the
file count, while three tests had actually passed. Someone quoting their own
output would have been told they were wrong, using a number that was never a
test count.

Fixed by stripping tallies from unsupported runners as well. There is nothing
claim-check could legitimately check in them, since it has no counts of its
own to compare against, so treating them as quotation is right regardless of
whether those runners are ever supported.

## jest 29.7.0

Captured on Windows, Node 24.18.0.

### All passing, with a skip and a todo

```
Test Suites: 1 passed, 1 total
Tests:       1 skipped, 1 todo, 3 passed, 5 total
Snapshots:   0 total
Time:        0.485 s
Ran all test suites.
```

### With a failure

```
Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 1 skipped, 1 todo, 3 passed, 6 total
Time:        0.503 s, estimated 1 s
```

### No tests

No tally line at all, just pattern diagnostics. Same meaning as vitest's
sentence: nothing to verify.

jest separates items with commas rather than vitest's `|`, and states the
total as a trailing `N total` rather than a parenthetical. `Test Suites` is
its file-level line and carries the same trap as vitest's `Test Files`.

A todo test does not run, exactly like a skipped one, so both are counted as
skipped. That is what makes the computed total match the total jest states:
`1 skipped + 1 todo + 3 passed = 5`.

## How this was built

Supported since 0.4.0.

- **`js_parser.py`** handles both runners; they differ only in separator and
  how the total is written.
- **`runners.py`** dispatches by content rather than by command string. A
  command can lie about what it runs; output cannot. Tests pin that each
  parser declines the other's output, so dispatch order cannot silently
  decide correctness.
- **The stated total is used as a checksum.** Both runners state their own
  total, so if the labels the parser recognises do not add up to it,
  something went unmapped and the whole result is discarded. An unrecognised
  label therefore costs a missed check rather than a quiet undercount.
- **The scope guard is per runner.** `evidence.py` carries a narrowing-flag
  set for each: pytest's `-k`/`-m`/`--lf`, vitest's `-t`/`--changed`/
  `--shard`, jest's `-t`/`--testPathPattern`/`--onlyChanged`. A runner it
  cannot identify, including anything hidden behind `npm test`, is scoped.
- **`PytestCounts` keeps its name** because it is in `__all__`. `TestCounts`
  is the same type under a name that aged better. The pytest-specific fields
  are zero for the JS runners.

Still unsupported: `cargo test`, `go test`, `mocha`, `ava`. Each needs its
own captured formats and its own narrowing flags before it can be trusted.
