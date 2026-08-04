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

## Notes toward supporting them properly

- The parse/policy split already holds. `compare.py` never sees raw output,
  only `PytestCounts`, so a second parser is additive rather than invasive.
- `PytestCounts` is misnamed the moment a second runner exists, but renaming
  it is a public API break. It is exported in `__all__`.
- Runner detection has to come from the command, not the output. The scope
  guard in `evidence.py` is pytest-flag-shaped (`-k`, `-m`, `--lf`) and every
  runner narrows scope differently: vitest uses `-t`, jest uses `-t` and
  `--testPathPattern`. Getting this wrong is how a filtered run confirms a
  whole-suite claim, so it needs the same conservative bias.
- jest output was not captured. The tests here use a plausible jest shape
  (`Tests:  3 passed, 3 total`) and it should be confirmed against a real run
  before anything depends on it.
