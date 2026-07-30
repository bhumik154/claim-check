# claim-check hardening: parser correctness and fail-open guarantees

Date: 2026-07-28
Status: approved, ready for implementation planning
Branch: `fix/parser-hardening`

## Problem

A rigorous smoke test of v0.1.0 (110/110 of the project's own tests passing) found
fifteen defects, five of them high severity, all reproduced end to end against a real
git repository with a real `commit-msg` hook installed.

Two of them defeat the tool's core purpose: a false test-count claim passes
verification. Three of them do the opposite and worse: an honest commit is blocked,
in two cases by an unhandled traceback, which is precisely the "false positive that
gets a tool uninstalled" failure this project exists to avoid.

The fifteen findings collapse into six root causes.

### Confirmed findings

| # | Severity | Finding |
|---|---|---|
| 1 | HIGH | `"N/M passed"` silently degrades to a bare pass-count check; the denominator is discarded. Real 22 passed + 1 failed, message `"22/22 passed"` → commit accepted. The same lie as `"22/22 tests pass"`, which is correctly blocked. |
| 2 | HIGH | The same overlap reads the *denominator* as the pass count. `"22/25 passed"` parses as `n_passed(25)`. False-accepts a lie (real 25p/3f → match) and false-blocks an honest claim (real 22p/3f → `claimed "25 passed" but 22 actually passed`). |
| 3 | HIGH | Negation guard defeated by the same overlap. `"not 22/22 passed"` and `"never 9/9 passed"` register claims. A message asserting the *opposite* of a claim blocks an honest commit. |
| 4 | HIGH | Unhandled `ValueError` in the summary parser. The duration pattern `[\d.]+` accepts `1.2.3`, then `float()` raises. Reachable because pytest echoes a failing test's captured stdout: a test printing `deploy finished in 1.2.3s` blocks the commit with a raw traceback. |
| 5 | HIGH | Captured test output can forge the count. Every summary-shaped line in the stream is aggregated. 21 truly passed + 1 failed, the failing test printed `==== 1 passed in 0.01s ====`, claim `"22 passed"` → accepted. |
| 6 | MED-HIGH | Quadratic backtracking (ReDoS) in the summary regex. 5k/10k/20k/40k `=` chars → 77/310/1238/4969 ms. A test printing `"=" * 60000` made the commit-msg hook take 12 seconds. `--timeout` covers only the subprocess, not parsing. |
| 7 | MED | Bad `--cwd` crashes instead of failing open. Only `FileNotFoundError` is caught; Windows raises `NotADirectoryError`. On Linux it is caught but reported as `could not find or run the test command: '<python>'`, naming the wrong thing. CI is ubuntu-only, which is why this never surfaced. |
| 8 | MED | `--command` crashes on unbalanced quotes (`shlex.split` sits outside the `try`) or whitespace-only input (`subprocess.run([])`). |
| 9 | MED | Claude hook crashes on any payload shape other than `{"command": <str>}`: `tool_input: null` → `AttributeError`, `command` null/int/list → `TypeError`, `cwd` present-but-null → `NotADirectoryError`. An unknown CLI flag exits 2, which blocks the tool call. |
| 10 | MED | `--timeout 0` or negative silently disables all verification and reports success forever. |
| 11 | LOW | `"1,022 passed"` parses as `22`. The lookbehind blocks `.`/`#`/digit but not `,`. |
| 12 | LOW | The `n't` branch of the negation regex is unreachable: `\b` never holds before the `n` in `doesn't`/`didn't`/`isn't`. |
| 13 | LOW | `git commit` must be tokens 0 and 1. `git -C path commit`, `git --no-pager commit`, `sudo git commit` are silently unverified. |
| 14 | LOW | Heredoc with surrounding text leaks the internal sentinel: `-m "prefix $(cat <<'EOF'…)"` yields `'prefix \x00HEREDOC1\x00'`. |
| 15 | INFO | `0/0 tests pass` with 0 collected matches, while `all tests pass` with 0 collected is correctly flagged. |

### Verified as sound, not changing

- No resource leaks: 15x `run_pytest` leaves handle count flat, no `ResourceWarning`s, timeout kills the child with no orphaned processes.
- No information leak: a planted secret in a failing test's output never reaches
  `verdict.message` or the PreToolUse deny JSON.
- The whole happy path: honest claims accepted, obvious lies blocked, no-claim
  messages never flagged, decimal/issue-ref guards working.

## Explicitly out of scope

**The errors-gate false positive stays.** An honest claim naming the error
(`"22 passed, 1 error"`) is still flagged as a mismatch. The strictness is
load-bearing: an error means some test never ran, so the true denominator is
genuinely unknown. Loosening it to accommodate one honest phrasing would trade a
real guarantee for a cosmetic annoyance. Documented in the README instead.

## Design

### 1. `claims.py` — containment resolution

Fixes findings 1, 2, 3, 11, 12.

The bug is one line: `max(candidates, key=lambda c: c.span[0])` lets a match
*contained inside* another beat its enclosing match. For `"22/22 passed"`,
`_N_OF_M_RE` matches at span 0 and `_N_PASSED_RE` matches the contained substring
`"22 passed"` at span 3; last-start-wins picks the contained match and throws the
denominator away.

Replace with a three-stage pipeline:

```
collect all matches (all three kinds, unfiltered)
  -> drop any match strictly contained in another    [NEW]
  -> drop negated matches
  -> last-start-wins
```

**The ordering is the fix.** Containment must run *before* negation. For
`"not 22/22 passed"`: the inner `22 passed` is dropped as contained, then the outer
`n_of_m` is dropped as negated, giving correctly no claim. Running negation first
leaves the inner match alive, which is today's bug.

Containment rule: candidate A is dropped if some candidate B exists with
`B.start <= A.start and B.end >= A.end` and B's span is not identical to A's. For
identical spans across kinds (not currently reachable, guarded anyway) prefer the
richer kind: `n_of_m` > `all_pass` > `n_passed`.

Two regex repairs alongside:

- Lookbehind `(?<![.\d#])` becomes `(?<![.\d#,])`, so `"1,022 passed"` no longer
  parses as `22`. Verified not to affect `"took 3.5s, 22 passed"`, where the digit
  is preceded by a space.
- `\b(not|n't|never)\s*$` becomes `(?:\bnot|\bnever|n't)\s*$`, making the
  contraction branch reachable.

### 2. `pytest_parser.py` — segmented, line-based scanning

Fixes findings 4, 5, 6.

Replace whole-text regex scanning with per-line classification, segmented on the
session header:

1. Split output into lines.
2. Open a new segment at each `=== test session starts ===` line.
3. Within each segment, classify lines as summary / no-tests-ran / neither.
4. Take the **last** such line in each segment.
5. Sum across segments.

If no session header appears anywhere, the whole text is one segment. This preserves
the `real_xdist_output.txt` fixture, which was captured mid-stream without a header.

**Why this defeats forgery.** A test's captured stdout is echoed in the FAILURES
report, which pytest prints *before* the final summary line. A line printed by a test
therefore always precedes its own session's real summary, so it can never be the last
line in its segment. The documented multi-invocation aggregation survives untouched,
because real piped output carries one session header per invocation — confirmed
against the `multi_suite_piped.txt` fixture.

**Why this kills the ReDoS.** The quadratic blowup came from `=+` and `.*?`
competing for the same characters across one long line. Stripping each line's `=`
padding before matching removes the ambiguity entirely; a 60,000-character line of
`=` reduces to an empty string and is skipped in linear time.

**Duration.** `(?P<duration>[\d.]+)` tightens to `(?P<duration>\d+(?:\.\d+)?)`, so
`in 1.2.3s` is simply not a summary line rather than crashing `float()`. A
`try/except ValueError` around the conversion stays as belt-and-braces.

**Deliberately not changed:** the trailing `=*` stays optional rather than becoming
required. Segmentation is what defeats the spoof; requiring the closing bracket adds
little and risks breaking trimmed CI logs fed through `--pytest-output`.

### 3. `runner.py` — fail open on every misconfiguration

Fixes findings 7, 8, 10.

- Move `shlex.split(command)` inside the `try` so `ValueError: No closing quotation`
  fails open.
- Guard empty or whitespace-only commands (`shlex.split` returning `[]`) before
  `subprocess.run` sees them.
- Broaden `except FileNotFoundError` to `except OSError`, which subsumes the Windows
  `NotADirectoryError` and `PermissionError`.
- Check `cwd` is a directory before invoking, so the failure reason names the working
  directory rather than blaming the interpreter.

`--timeout <= 0` becomes a hard argparse error on all three entry points. This
deliberately deviates from fail-open: a timeout of zero currently makes the tool a
no-op that green-lights every commit while reporting what looks like success. Silent
non-verification is the worst possible outcome for a verification tool, so a config
mistake should be loud.

### 4. Entry points — a structural fail-open guarantee

Fixes finding 9, and the general class.

Wrap each entry point's verification body in `try/except Exception` returning the
fail-open exit code with a warning. This is the important change: it means no
internal defect, present or future, can block a commit. The specific crashes found
also get targeted fixes; this is the backstop the design already claimed to have.

Additionally:

- `claude_hook`: type-guard `tool_input` (must be a dict), `command` (must be a str),
  and `cwd` (must be a str naming a real directory, else default to `.`).
- `cli`: catch `OSError` rather than only `FileNotFoundError` when reading
  `--pytest-output`, so a directory path fails open.
- `--help` must continue to exit 0 on all three entry points; CI asserts this.

### 5. `shell_parser.py`

Fixes findings 13, 14.

- When locating the `commit` subcommand, skip git's own global flags: `-C <path>`,
  `-c <cfg>`, `--no-pager`, `--paginate`, `--git-dir=…`, `--work-tree=…`,
  `--exec-path=…`. Leading wrappers such as `sudo` remain unsupported and documented.
- Substitute heredoc placeholders as substrings rather than only on exact token
  equality, so `\x00HEREDOC1\x00` stops leaking into extracted messages.

### 6. `runner.py` public API guard, and `compare.py` vacuity consistency

Two small changes:

- `result_from_captured_output` (public, exported in `__all__`) currently raises
  `TypeError` on non-`str` input (`None`, bytes, int). Guard it.
- Finding 15: in `compare.py`, an `n_of_m` claim with a claimed total of 0 against 0
  collected tests is flagged, matching how `all_pass` already treats the same
  vacuous situation.

## Testing

Strict TDD. Every finding gets a regression test written first and observed failing,
in the repo's existing descriptive naming style, added to the matching existing test
file:

- `test_claims.py` — findings 1, 2, 3, 11, 12
- `test_pytest_parser.py` — findings 4, 5, 6, plus segmentation and aggregation
- `test_runner.py` — findings 7, 8, 10
- `test_cli.py`, `test_precommit_entrypoint.py`, `test_claude_hook_entrypoint.py` — finding 9 and the structural fail-open guarantee
- `test_shell_parser.py` — findings 13, 14
- `test_compare.py` — finding 15

New fixtures for the spoofing and multi-session cases, following the existing
`tests/fixtures/pytest_outputs/` convention of real captured output.

**All 110 existing tests must continue to pass.** If any breaks, that is a design
signal to bring back for a decision, not something to quietly amend.

## Documentation

The README's stated case count ("110 cases") will change and must be re-verified
against real pytest output before commit — this repo's own hook checks that claim,
and getting it wrong would be the exact failure the project was built to catch.

README updates:

- New rows in the by-module scenario table for each new guarantee.
- Honest statement of the residual limits: a test that emits a complete fake session
  header plus summary can still forge a count, and the errors-gate false positive on
  an honest claim that names the error remains by design.
- `--timeout` documentation noting that zero and negative values are rejected.
- The `git -C` / wrapper-prefix coverage boundary in "What this is not".

## Success criteria

1. Every one of the fifteen findings has a regression test that fails before its fix
   and passes after.
2. The end-to-end smoke scenarios rerun clean: `22/22 passed` blocked against
   22p/1f, `not 22/22 passed` accepted, the forged-summary claim blocked, the
   poisoned-duration commit accepted, hook wall time on a 60k-character line under
   one second.
3. All 110 pre-existing tests still pass.
4. The README's case count matches real output.
