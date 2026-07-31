# claim-check Parser Hardening Implementation Plan

**Goal:** Fix fifteen confirmed defects in claim-check v0.1.0, two that let a false test-count claim pass verification, three that block honest commits (two with a raw traceback), without changing the tool's public interface.

**Architecture:** Six root-cause fixes across six modules. The two structural ones: `claims.py` gains a containment-resolution stage that must run *before* negation filtering, and `pytest_parser.py` moves from whole-text regex scanning to per-line classification segmented on pytest's session header, taking the last summary line per segment. The rest are fail-open hardening at the process boundaries.

**Tech Stack:** Python >=3.9, stdlib only (`re`, `shlex`, `subprocess`, `argparse`, `pathlib`), pytest for tests, hatchling build.

## Global Constraints

- **Zero runtime dependencies.** stdlib only. Do not add a package to `dependencies` in `pyproject.toml`.
- **Python >=3.9.** No `match` statements, no `X | Y` unions in evaluated annotations, no possessive quantifiers or atomic groups in regexes (3.11+ only). CI runs 3.9, 3.11, 3.12.
- **All 110 pre-existing tests must continue to pass.** If one breaks, stop and report it as a design question. Do not amend an existing test to make a new implementation pass.
- **No public interface changes.** Every name in `__init__.py`'s `__all__` keeps its signature.
- **`--help` must exit 0** on all three entry points; CI asserts this.
- **Fail open is the default everywhere** except explicit configuration errors (`--timeout <= 0`, unknown flags), which fail loudly and deliberately.
- Commit messages must not state a test count unless it has been verified against real pytest output in that same step.

---

### Task 1: Containment resolution in the claim parser

Fixes findings 1, 2, 3, 11, 12. The bug: `max(candidates, key=lambda c: c.span[0])` lets a match contained *inside* another beat its enclosing match. For `"22/22 passed"`, `_N_OF_M_RE` matches at span `(0,12)` and `_N_PASSED_RE` matches the contained substring `"22 passed"` at span `(3,12)`; last-start-wins picks the contained one and discards the denominator.

**Files:**
- Modify: `src/claim_check/claims.py`
- Test: `tests/test_claims.py`

**Interfaces:**
- Consumes: `Claim` from `claim_check.models` (unchanged).
- Produces: `extract_claims(message: str) -> list[Claim]`, signature unchanged; behavior corrected.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claims.py`:

```python
def test_ratio_claim_spelled_passed_is_not_degraded_to_a_bare_count_claim():
    # Confirmed bypass: "22/22 passed" produced n_passed(22) instead of
    # n_of_m(22, 22), because _N_PASSED_RE matches the contained substring
    # "22 passed" at a LATER start offset than the enclosing _N_OF_M_RE
    # match, and last-start-wins picked the contained one. The denominator
    # was silently discarded, so a real run of 22 passed + 1 failed
    # verified this claim as true and let the commit through.
    claims = extract_claims("22/22 passed")
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 22
    assert claims[0].claimed_total == 22


def test_ratio_claim_numerator_is_the_pass_count_not_the_denominator():
    # The same overlap read the DENOMINATOR as the pass count: "22/25
    # passed" parsed as n_passed(25). That both false-accepted a lie
    # (real 25 passed -> "match") and false-blocked an honest claim
    # (real 22 passed -> 'claimed "25 passed" but 22 actually passed').
    claims = extract_claims("22/25 passed")
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 22
    assert claims[0].claimed_total == 25


def test_negated_ratio_claim_spelled_passed_is_not_treated_as_a_claim():
    # The negation guard was defeated by the same overlap. The n_of_m match
    # at offset 4 was correctly dropped as negated, but the contained
    # "22 passed" starts at offset 7, where the 20-character lookback window
    # is "not 22/" - no adjacent negation - so it survived and registered a
    # claim. A message asserting the OPPOSITE of a claim then blocked an
    # honest commit. Containment resolution must run BEFORE the negation
    # filter for this to work.
    assert extract_claims("not 22/22 passed") == []


def test_negated_ratio_claim_with_never_is_not_treated_as_a_claim():
    assert extract_claims("never 9/9 passed") == []


def test_thousands_separator_is_not_misread_as_a_smaller_count():
    # "1,022 passed" parsed as 22 (from the substring "022 passed"): the
    # lookbehind blocked ".", "#" and digits, but not ",". An honest claim
    # about a 1022-test suite was flagged as a mismatch.
    claims = extract_claims("1,022 passed")
    assert claims == []


def test_negation_expressed_as_a_contraction_is_honoured():
    # The "n't" branch of the negation regex was unreachable: \b never holds
    # before the "n" of "doesn't"/"didn't"/"isn't", since both neighbours
    # are word characters.
    assert extract_claims("it doesn't 22 passed") == []
    assert extract_claims("that isn't 22 passed") == []


def test_comma_before_a_genuine_claim_still_registers():
    # Guard against over-correcting the thousands-separator fix: a comma
    # separated by whitespace is ordinary prose, not a digit grouping.
    claims = extract_claims("took 3.5s, 22 passed")
    assert len(claims) == 1
    assert claims[0].claimed_passed == 22
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_claims.py -v -k "ratio or thousands or contraction or comma_before"`

Expected: FAIL. `test_ratio_claim_spelled_passed_...` fails with `assert 'n_passed' == 'n_of_m'`; `test_negated_ratio_claim_spelled_passed_...` fails with a non-empty list; `test_thousands_separator_...` fails with a claim of 22; the contraction test fails with a registered claim. `test_comma_before_a_genuine_claim_still_registers` should PASS already, it is a regression guard, not a bug reproduction.

- [ ] **Step 3: Replace the body of `claims.py` below the imports**

Replace lines 12-97 of `src/claim_check/claims.py` (everything from `_NOT_A_CLAIM_DIGIT_PREFIX` to the end) with:

```python
# (?<![.\d#,]) blocks four false-positive shapes in one lookbehind: a digit
# preceded by another digit (a partial match into a larger number), by "."
# (the tail of a decimal like "0.22"), by "#" (an issue reference like
# "#22"), or by "," (the tail of a grouped number like "1,022", which
# otherwise parsed as a claim of 22). None of these are test-count claims.
_NOT_A_CLAIM_DIGIT_PREFIX = r"(?<![.\d#,])"

_N_PASSED_RE = re.compile(
    _NOT_A_CLAIM_DIGIT_PREFIX + r"\b(\d+)\s+passed\b",
    re.IGNORECASE,
)
_N_OF_M_RE = re.compile(
    _NOT_A_CLAIM_DIGIT_PREFIX + r"\b(\d+)\s*/\s*(\d+)\s+(?:tests?\s+)?pass(?:ed|ing)?\b",
    re.IGNORECASE,
)
_ALL_PASS_RE = re.compile(
    r"\ball\s+(?:(\d+)\s+)?tests?\s+pass(?:ed|ing)?\b",
    re.IGNORECASE,
)
# "n't" deliberately carries no \b prefix: in a real contraction ("doesn't",
# "isn't") both neighbours of the "n" are word characters, so \b never holds
# there and the branch was dead code.
_NEGATION_BEFORE_RE = re.compile(r"(?:\bnot|\bnever|n't)\s*$", re.IGNORECASE)

# When two matches cover the exact same span, the kind carrying more
# information wins. Not currently reachable given the three regexes above;
# guarded so a future regex change degrades predictably rather than
# arbitrarily by list order.
_KIND_SPECIFICITY = {"n_of_m": 2, "all_pass": 1, "n_passed": 0}


def _is_negated(message: str, match_start: int) -> bool:
    window = message[max(0, match_start - 20) : match_start]
    return bool(_NEGATION_BEFORE_RE.search(window))


def _collect_candidates(message: str) -> list[Claim]:
    candidates: list[Claim] = []

    for m in _N_PASSED_RE.finditer(message):
        candidates.append(
            Claim(
                kind="n_passed",
                claimed_passed=int(m.group(1)),
                claimed_total=None,
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    for m in _N_OF_M_RE.finditer(message):
        candidates.append(
            Claim(
                kind="n_of_m",
                claimed_passed=int(m.group(1)),
                claimed_total=int(m.group(2)),
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    for m in _ALL_PASS_RE.finditer(message):
        candidates.append(
            Claim(
                kind="all_pass",
                claimed_passed=None,
                claimed_total=int(m.group(1)) if m.group(1) else None,
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    return candidates


def _encloses(outer: Claim, inner: Claim) -> bool:
    if outer.span[0] > inner.span[0] or outer.span[1] < inner.span[1]:
        return False
    if outer.span != inner.span:
        return True
    return _KIND_SPECIFICITY[outer.kind] > _KIND_SPECIFICITY[inner.kind]


def _drop_enclosed(candidates: list[Claim]) -> list[Claim]:
    """Discards any candidate wholly contained inside another.

    "22/22 passed" matches both _N_OF_M_RE (span 0-12) and, on the
    substring "22 passed", _N_PASSED_RE (span 3-12). They describe the same
    phrase, not two claims, and the enclosing match is the one that read it
    correctly - confirmed directly: keeping the contained match discarded
    the denominator entirely, so a real 22-passed-1-failed run verified
    "22/22 passed" as true, while the identical lie written "22/22 tests
    pass" was correctly blocked.
    """
    return [
        candidate
        for candidate in candidates
        if not any(_encloses(other, candidate) for other in candidates if other is not candidate)
    ]


def extract_claims(message: str) -> list[Claim]:
    """Returns at most one Claim: the last one found in the message, by
    starting position, regardless of kind. compare.py checks every returned
    claim against a single pytest run, so two claims with different kinds
    or numbers can't both be true at once - confirmed directly, "14 passed.
    Fixed the bug, now 15/15 tests pass!" is a stale count restated and
    corrected using different phrasing (n_passed then n_of_m), not a real
    contradiction, and returning both flagged an honest correction as a
    mismatch. Keeping only the last claim in the text is the same
    last-occurrence-wins principle as before, just applied globally instead
    of per kind. This does not resolve genuine tense/discourse ambiguity
    (a later sentence describing an earlier state is still possible); see
    the README.

    The three stages run in this order, and the order is load-bearing:
    enclosed matches are dropped BEFORE the negation filter. "not 22/22
    passed" negates the n_of_m match at offset 4, but the contained
    "22 passed" starts at offset 7, where the lookback window is "not 22/"
    and reads as un-negated. Filtering negation first leaves that contained
    match alive and registers a claim from a message that explicitly denies
    one. Do not reorder these.
    """
    candidates = _drop_enclosed(_collect_candidates(message))
    candidates = [c for c in candidates if not _is_negated(message, c.span[0])]

    if not candidates:
        return []

    return [max(candidates, key=lambda c: c.span[0])]
```

- [ ] **Step 4: Run the full claims test file**

Run: `python -m pytest tests/test_claims.py -v`

Expected: PASS, all tests including the 18 pre-existing ones.

- [ ] **Step 5: Run the whole suite to check for collateral damage**

Run: `python -m pytest -q`

Expected: PASS, no failures. If `test_partial_ratio_claim_with_unequal_numerator_and_denominator_is_still_extracted` or `test_multiple_claims_of_different_kinds_only_the_last_in_text_is_kept` fails, stop and report, those encode deliberate policy and must not be edited.

- [ ] **Step 6: Commit**

```bash
git add src/claim_check/claims.py tests/test_claims.py
git commit -m "Fix ratio claims spelled 'passed' silently degrading to a bare-count check"
```

---

### Task 2: Session-segmented pytest output parsing

Fixes findings 4, 5, 6. Three defects share one cause: the parser scans the entire captured stream with a whole-text regex and aggregates every summary-shaped line it finds.

**Files:**
- Modify: `src/claim_check/pytest_parser.py`
- Create: `tests/fixtures/pytest_outputs/forged_summary_in_failure_report.txt`
- Test: `tests/test_pytest_parser.py`

**Interfaces:**
- Consumes: `PytestCounts` from `claim_check.models` (unchanged).
- Produces: `parse_summary_line(pytest_output: str) -> Optional[PytestCounts]` and `strip_ansi(text: str) -> str`, signatures unchanged.

- [ ] **Step 1: Create the forged-summary fixture**

Create `tests/fixtures/pytest_outputs/forged_summary_in_failure_report.txt`:

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
collected 22 items

tests/test_suite.py .....................F                               [100%]

=================================== FAILURES ==================================
_________________________________ test_broken _________________________________

    def test_broken():
        print("==== 1 passed in 0.01s ====")
>       assert False
E       assert False

tests/test_suite.py:6: AssertionError
----------------------------- Captured stdout call ----------------------------
==== 1 passed in 0.01s ====
=========================== short test summary info ===========================
FAILED tests/test_suite.py::test_broken - assert False
========================= 1 failed, 21 passed in 0.09s ========================
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_pytest_parser.py`:

```python
def test_a_summary_line_printed_by_a_test_cannot_forge_the_count():
    # Confirmed spoof, reproduced end to end: a failing test that prints
    # "==== 1 passed in 0.01s ====" gets that line echoed back by pytest's
    # own failure report (captured stdout is replayed there), and the old
    # parser aggregated it with the real summary - turning a true 21-passed
    # result into 22 and verifying a false "22 passed" claim as correct.
    # A test's output is always replayed BEFORE its session's real summary
    # line, so taking the last summary line per session defeats this.
    counts = parse_summary_line(_read("forged_summary_in_failure_report.txt"))
    assert counts is not None
    assert counts.passed == 21
    assert counts.failed == 1
    assert counts.total == 22


def test_a_version_shaped_duration_does_not_crash_the_parser():
    # The duration pattern was "[\\d.]+", which accepts "1.2.3" and then
    # raised ValueError inside float(). Reachable in ordinary use: pytest
    # replays a failing test's captured stdout, so a test printing a deploy
    # or build log line was enough to abort the commit with a traceback.
    output = (
        "============================= test session starts =============================\n"
        "==================== deploy finished in 1.2.3s ====================\n"
        "========================= 1 passed in 0.10s ========================\n"
    )
    counts = parse_summary_line(output)
    assert counts is not None
    assert counts.passed == 1
    assert counts.duration_s == 0.10


def test_a_version_shaped_duration_alone_is_not_a_summary_line():
    assert parse_summary_line("==== deploy finished in 1.2.3s ====") is None


def test_a_very_long_separator_line_parses_in_linear_time():
    # The old regex ("^=+\\s*(?P<body>.*?)\\s+in\\s+...") backtracked
    # quadratically when "=+" and ".*?" competed for the same characters:
    # 40k "=" took ~5s, and a test printing "=" * 60000 made the commit-msg
    # hook take 12 seconds. --timeout guards the subprocess, not parsing.
    import time

    payload = "=" * 200000
    started = time.perf_counter()
    assert parse_summary_line(payload) is None
    assert time.perf_counter() - started < 1.0


def test_summary_lines_within_one_session_take_the_last_not_the_sum():
    output = (
        "============================= test session starts =============================\n"
        "==== 5 passed in 1.00s ====\n"
        "==== 7 passed in 2.00s ====\n"
    )
    counts = parse_summary_line(output)
    assert counts.passed == 7
    assert counts.duration_s == 2.00


def test_output_with_no_session_header_is_treated_as_a_single_segment():
    # Guards the real_xdist_output.txt fixture, captured mid-stream with no
    # header, and any trimmed CI log fed through --pytest-output.
    counts = parse_summary_line("========== 18 passed, 4 deselected in 0.10s ==========")
    assert counts.passed == 18
    assert counts.deselected == 4


def test_non_string_input_returns_none_instead_of_raising():
    assert parse_summary_line(None) is None
    assert parse_summary_line(12345) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_pytest_parser.py -v -k "forge or version_shaped or linear_time or take_the_last or non_string"`

Expected: FAIL. The forge test fails with `assert 22 == 21`; both version-shaped tests fail with `ValueError: could not convert string to float: '1.2.3'`; the linear-time test fails on the elapsed-time assertion (roughly 100+ seconds, if you do not want to wait, confirm the failure at `"=" * 40000` first); `test_summary_lines_within_one_session_take_the_last_not_the_sum` fails with `assert 12 == 7`; the non-string test fails with `TypeError`.

- [ ] **Step 4: Rewrite `pytest_parser.py`**

Replace the entire contents of `src/claim_check/pytest_parser.py` with:

```python
"""Parses pytest's own summary line(s) into PytestCounts.

Never re-derives a count by counting individual test-result lines; the
summary line is pytest's own authoritative tally and is what a human or an
agent reading pytest's output would actually be quoting from.

Scanning is line-based and segmented on pytest's session header rather than
regex-scanning the whole stream at once. That is what makes the result
trustworthy: a test's own stdout is replayed inside pytest's failure report,
which always precedes that session's real summary line, so taking the last
summary line per session means test output can never forge the tally.
"""

import re
from typing import Optional

from .models import PytestCounts

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Opens a new run. Real piped output ("pytest tests/unit && pytest
# tests/integration") carries one of these per invocation, which is what
# lets separate runs be aggregated without trusting arbitrary stream text.
_SESSION_HEADER_RE = re.compile(r"^=+\s*test session starts\s*=+$", re.IGNORECASE)

# Applied to a line already stripped of its "=" padding, so there is no
# "=+" / ".*?" ambiguity for the engine to backtrack through. The duration
# is a strict number: "[\d.]+" also matched "1.2.3", which then raised
# ValueError inside float() and aborted the commit with a traceback.
_DURATION = r"(?P<duration>\d+(?:\.\d+)?)s(?:\s*\(\d+:\d+:\d+\))?"
_NO_TESTS_BODY_RE = re.compile(r"^no tests ran\s+in\s+" + _DURATION + r"$", re.IGNORECASE)
_SUMMARY_BODY_RE = re.compile(r"^(?P<body>.*?)\s+in\s+" + _DURATION + r"$")

_COUNT_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<label>passed|failed|skipped|xfailed|xpassed|errors?|warnings?|deselected)\b"
)
_LABEL_MAP = {
    "passed": "passed",
    "failed": "failed",
    "skipped": "skipped",
    "xfailed": "xfailed",
    "xpassed": "xpassed",
    "error": "errors",
    "errors": "errors",
    "warning": "warnings",
    "warnings": "warnings",
    "deselected": "deselected",
}
_COUNT_FIELDS = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors", "warnings", "deselected")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _classify_line(line: str):
    """Returns (kind, body, duration_s, raw_line) or None.

    "no tests ran" is checked first: the generic summary form also matches
    that line, and classifying each line exactly once is what removes the
    double-counting the previous span-overlap filter existed to handle.
    """
    stripped = line.strip()
    if not stripped.startswith("="):
        return None

    core = stripped.strip("=").strip()
    if not core:
        return None

    match = _NO_TESTS_BODY_RE.match(core)
    kind = "no_tests"
    if match is None:
        match = _SUMMARY_BODY_RE.match(core)
        kind = "summary"
    if match is None:
        return None

    try:
        duration = float(match.group("duration"))
    except ValueError:
        # Unreachable given the stricter duration pattern; kept so a future
        # loosening of that pattern degrades to "not a summary line" rather
        # than to an unhandled crash inside a commit-msg hook.
        return None

    body = match.group("body") if kind == "summary" else ""
    return kind, body, duration, stripped


def parse_summary_line(pytest_output: str) -> Optional[PytestCounts]:
    """Returns None if no summary line (and no "no tests ran" line) is found
    at all - that means pytest crashed before reaching normal reporting
    (an INTERNALERROR, or an invocation error like a missing path), which is
    a materially different situation from a summary that says 0 tests ran.

    Output is split into segments at each "test session starts" header, and
    the LAST summary line in each segment is the one that counts; segment
    totals are then summed. A single invocation only ever produces one
    authoritative summary line - confirmed directly against real
    pytest-xdist output, which prints exactly one final aggregate line and
    no per-worker partials - so more than one segment means more than one
    real run happened, and a claim like "all 100 tests pass" refers to their
    combined total.

    Taking the last line per segment rather than summing every match is
    what stops a test from forging the tally: pytest replays a failing
    test's captured stdout inside the FAILURES report, which is printed
    before the summary, so a line a test prints can never be last.

    Output with no session header anywhere is treated as one segment, which
    covers a log captured mid-stream or trimmed before being handed to
    --pytest-output.
    """
    if not isinstance(pytest_output, str):
        return None

    text = strip_ansi(pytest_output)

    segments = [[]]
    for line in text.splitlines():
        if _SESSION_HEADER_RE.match(line.strip()):
            segments.append([])
            continue
        classified = _classify_line(line)
        if classified is not None:
            segments[-1].append(classified)

    finals = [segment[-1] for segment in segments if segment]
    if not finals:
        return None

    totals = {field: 0 for field in _COUNT_FIELDS}
    total_duration = 0.0
    raw_lines = []

    for kind, body, duration, raw in finals:
        raw_lines.append(raw)
        total_duration += duration
        if kind == "summary":
            for count_match in _COUNT_RE.finditer(body):
                label = _LABEL_MAP[count_match.group("label")]
                totals[label] += int(count_match.group("count"))
        # "no_tests" contributes zero to every field; nothing to add.

    return PytestCounts(
        duration_s=total_duration,
        raw_summary_line=" | ".join(raw_lines),
        **totals,
    )
```

- [ ] **Step 5: Run the parser tests**

Run: `python -m pytest tests/test_pytest_parser.py -v`

Expected: PASS, all tests including the 15 pre-existing ones. Pay particular attention to `test_multiple_summary_lines_from_piped_suites_are_aggregated_not_last_wins` (must still yield 100) and `test_no_tests_ran_line_is_not_double_counted_by_the_generic_summary_regex` (duration must still be 0.01).

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/claim_check/pytest_parser.py tests/test_pytest_parser.py tests/fixtures/pytest_outputs/forged_summary_in_failure_report.txt
git commit -m "Take the last summary line per session so test output cannot forge the tally"
```

---

### Task 3: Runner fails open on every misconfiguration

Fixes findings 7, 8, 10. `shlex.split` sits outside the `try`, only `FileNotFoundError` is caught (Windows raises `NotADirectoryError` for a bad `cwd`), and a non-positive `--timeout` silently turns the tool into a no-op that green-lights every commit.

**Files:**
- Create: `src/claim_check/_args.py`
- Modify: `src/claim_check/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces: `claim_check._args.positive_timeout(raw: str) -> float`, an `argparse` `type=` callable raising `argparse.ArgumentTypeError` for values `<= 0` or unparseable input. Tasks 4 imports it in all three entry points.
- Produces: `run_pytest(cwd, pytest_args=(), timeout_s=DEFAULT_TIMEOUT_S, command=None) -> RunResult` and `result_from_captured_output(returncode, stdout, stderr="") -> RunResult`, signatures unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runner.py`:

```python
from claim_check._args import positive_timeout
import argparse


def test_nonexistent_working_directory_fails_open_naming_the_directory(tmp_path):
    # Only FileNotFoundError was caught. Windows raises NotADirectoryError
    # for a bad cwd, so this crashed with an unhandled traceback - and in
    # the commit-msg hook a traceback means a nonzero exit, which aborts an
    # entirely honest commit. On Linux it was caught but blamed the wrong
    # thing: "could not find or run the test command: '<python>'".
    missing = tmp_path / "no-such-dir"
    result = run_pytest(missing, timeout_s=20)
    assert result.counts is None
    assert "no-such-dir" in result.parse_error


def test_working_directory_that_is_a_file_fails_open(tmp_path):
    target = tmp_path / "notadir.txt"
    target.write_text("x", encoding="utf-8")
    result = run_pytest(target, timeout_s=20)
    assert result.counts is None
    assert result.parse_error is not None


def test_none_working_directory_falls_back_to_the_current_directory(tmp_path):
    _write_passing_test(tmp_path)
    result = run_pytest(None, pytest_args=[str(tmp_path)], timeout_s=60)
    assert result.counts is not None
    assert result.counts.passed == 2


def test_unbalanced_quote_in_command_fails_open_instead_of_crashing(tmp_path):
    # shlex.split sat outside the try block, so a plain quoting typo in
    # --command raised ValueError: No closing quotation and blocked the
    # commit. The README itself tells people to quote this flag.
    result = run_pytest(tmp_path, command='poetry "run pytest', timeout_s=20)
    assert result.counts is None
    assert "run pytest" in result.parse_error or "quot" in result.parse_error.lower()


def test_whitespace_only_command_fails_open_instead_of_crashing(tmp_path):
    # shlex.split("   ") returns [], and subprocess.run([]) raised
    # OSError [WinError 87]; base_command[0] in the error handler would
    # have raised IndexError on top of it.
    result = run_pytest(tmp_path, command="   ", timeout_s=20)
    assert result.counts is None
    assert result.parse_error is not None


def test_positive_timeout_rejects_zero_and_negative_values():
    # --timeout 0 silently killed every run instantly and failed open,
    # reporting "did not finish within 0s" - a tool that verifies nothing
    # while looking like it works is the worst possible outcome here, so
    # this is a deliberate exception to the fail-open rule.
    assert positive_timeout("30") == 30.0
    for bad in ("0", "-5"):
        with pytest.raises(argparse.ArgumentTypeError):
            positive_timeout(bad)


def test_positive_timeout_rejects_unparseable_values():
    with pytest.raises(argparse.ArgumentTypeError):
        positive_timeout("abc")


def test_captured_output_helper_tolerates_non_string_input():
    assert result_from_captured_output(0, None).counts is None
    assert result_from_captured_output(0, b"==== 1 passed in 1.0s ====").counts.passed == 1
```

`tests/test_runner.py` currently imports only `run_pytest`. Replace that import line with:

```python
from claim_check.runner import result_from_captured_output, run_pytest
```

It already imports `pytest` at the top, so `pytest.raises` needs no new import.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_runner.py -v -k "working_directory or unbalanced or whitespace_only or positive_timeout or non_string"`

Expected: FAIL. The `_args` import fails first with `ModuleNotFoundError: No module named 'claim_check._args'`, that is the expected starting failure. After creating the module in Step 3, the remaining tests fail with `NotADirectoryError`, `ValueError: No closing quotation`, `OSError [WinError 87]`, and `TypeError`.

- [ ] **Step 3: Create `src/claim_check/_args.py`**

```python
"""Shared argparse helpers for the three entry points.

Kept out of runner.py so the runner stays free of CLI concerns, and out of
each entry point so the three don't drift apart.
"""

import argparse


def positive_timeout(raw: str) -> float:
    """argparse `type=` callable for --timeout.

    Rejects zero and negative values deliberately, breaking this project's
    otherwise-universal fail-open rule. A non-positive timeout kills every
    run the instant it starts and then fails open, so the tool reports
    "could not verify ... allowing commit" forever while looking like it is
    working - silently verifying nothing is a worse outcome than a loud
    error about a flag the user typed wrong.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"invalid float value: {raw!r}")
    if value <= 0:
        raise argparse.ArgumentTypeError(
            f"--timeout must be greater than 0 (got {value}); a non-positive timeout "
            "kills every run instantly and silently verifies nothing"
        )
    return value
```

- [ ] **Step 4: Rewrite `run_pytest` and `result_from_captured_output` in `runner.py`**

Replace lines 26-82 of `src/claim_check/runner.py` (from `def run_pytest(` to the end) with:

```python
def _failed_run(reason: str) -> RunResult:
    return RunResult(returncode=-1, stdout="", stderr="", counts=None, parse_error=reason)


def run_pytest(
    cwd: Union[str, Path],
    pytest_args: Sequence[str] = (),
    timeout_s: Optional[float] = DEFAULT_TIMEOUT_S,
    command: Optional[str] = None,
) -> RunResult:
    """command, if given, overrides the default `sys.executable -m pytest`
    invocation - e.g. "poetry run pytest", "hatch run test", or
    "docker-compose exec web pytest" - for projects where the caller's own
    Python environment isn't the one with the project's real test
    dependencies installed (confirmed directly: a claim-check installed
    into its own separate environment, pipx-style, produces a
    ModuleNotFoundError for pytest itself when the project's actual tests
    live in a different poetry/hatch/pipenv-managed environment - silently
    failing open on every commit, exactly as if no verification tool were
    installed at all).

    Every failure path returns a RunResult with counts=None rather than
    raising: this runs inside a commit-msg hook, where an unhandled
    exception means a nonzero exit, which aborts the commit. A
    misconfiguration is not evidence any claim is wrong.
    """
    try:
        base_command = shlex.split(command) if command else [sys.executable, "-m", "pytest"]
    except ValueError as exc:
        # An unbalanced quote in --command. The README tells people to quote
        # this flag, so a typo here is an ordinary user mistake, not a bug.
        return _failed_run(f"could not parse the test command {command!r}: {exc}")

    if not base_command:
        return _failed_run(f"the test command {command!r} is empty")

    try:
        cwd_path = Path(cwd) if cwd is not None else Path(".")
    except TypeError:
        return _failed_run(f"invalid working directory: {cwd!r}")

    if not cwd_path.is_dir():
        # Checked up front so the reason names the directory. Left to
        # subprocess, this surfaces as FileNotFoundError on Linux (reported
        # as a missing *command*, blaming the interpreter) and as an
        # uncaught NotADirectoryError on Windows.
        return _failed_run(f"working directory not found: {str(cwd_path)!r}")

    try:
        proc = subprocess.run(
            [*base_command, *pytest_args],
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            returncode=-1,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            counts=None,
            parse_error=f"pytest did not finish within {timeout_s}s and was killed",
        )
    except OSError as exc:
        # Covers FileNotFoundError (a --command wrapper like "poetry" that
        # isn't installed), PermissionError, and the Windows-only
        # NotADirectoryError / WinError 87 shapes. Anything here would
        # otherwise crash this process with an unhandled traceback instead
        # of failing open like every other "couldn't verify" case.
        return _failed_run(f"could not find or run the test command: {base_command[0]!r} ({exc})")

    return result_from_captured_output(proc.returncode, proc.stdout, proc.stderr)


def result_from_captured_output(returncode: int, stdout, stderr: str = "") -> RunResult:
    """stdout is coerced rather than trusted: this is a public, exported
    helper, and CI callers hand it whatever their pipeline captured."""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    elif not isinstance(stdout, str):
        stdout = "" if stdout is None else str(stdout)

    counts = parse_summary_line(stdout)
    parse_error = None if counts is not None else "no pytest summary line found in the captured output"
    return RunResult(returncode=returncode, stdout=stdout, stderr=stderr, counts=counts, parse_error=parse_error)
```

- [ ] **Step 5: Run the runner tests**

Run: `python -m pytest tests/test_runner.py -v`

Expected: PASS, including the 6 pre-existing tests. `test_bad_command_fails_open_with_descriptive_error_instead_of_crashing` must still pass, the command name stays in the message.

- [ ] **Step 6: Run the whole suite and commit**

Run: `python -m pytest -q`

Expected: PASS.

```bash
git add src/claim_check/_args.py src/claim_check/runner.py tests/test_runner.py
git commit -m "Fail open on a bad cwd, an unparseable command, and reject a non-positive timeout"
```

---

### Task 4: Structural fail-open guarantee at the entry points

Fixes finding 9 and the general class of crash-blocks-commit. The targeted fixes in Tasks 1-3 remove the crashes found; this adds the backstop the design already claimed to have, so no future internal defect can block a commit.

**Files:**
- Modify: `src/claim_check/entrypoints/precommit.py`
- Modify: `src/claim_check/entrypoints/claude_hook.py`
- Modify: `src/claim_check/cli.py`
- Test: `tests/test_precommit_entrypoint.py`, `tests/test_claude_hook_entrypoint.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `claim_check._args.positive_timeout` from Task 3.
- Produces: `precommit.main(argv=None) -> int`, `claude_hook.main(stdin_text=None, argv=None) -> int`, `cli.main(argv=None) -> int`, signatures unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_hook_entrypoint.py`:

```python
import json


def test_malformed_tool_input_shapes_all_allow_instead_of_crashing():
    # The hook did payload.get("tool_input", {}).get("command", "") with no
    # type guard: tool_input=null raised AttributeError, and a non-string
    # command raised TypeError inside the shell parser. The module docstring
    # promises it "fails open at every uncertain step"; it did not.
    from claim_check.entrypoints import claude_hook

    payloads = [
        {"tool_name": "Bash", "tool_input": None},
        {"tool_name": "Bash", "tool_input": "git commit -m '22 passed'"},
        {"tool_name": "Bash", "tool_input": [1, 2]},
        {"tool_name": "Bash", "tool_input": {"command": None}},
        {"tool_name": "Bash", "tool_input": {"command": 42}},
        {"tool_name": "Bash", "tool_input": {"command": ["git", "commit"]}},
    ]
    for payload in payloads:
        assert claude_hook.main(stdin_text=json.dumps(payload), argv=[]) == 0


def test_null_or_missing_cwd_falls_back_instead_of_crashing(tmp_path):
    # payload.get("cwd", ".") returns None when the key is present and null,
    # which reached subprocess as the literal directory "None".
    from claim_check.entrypoints import claude_hook

    payload = {
        "tool_name": "Bash",
        "cwd": None,
        "tool_input": {"command": "git commit -m 'chore: no claim here'"},
    }
    assert claude_hook.main(stdin_text=json.dumps(payload), argv=[]) == 0


def test_nonexistent_cwd_allows_instead_of_crashing():
    from claim_check.entrypoints import claude_hook

    payload = {
        "tool_name": "Bash",
        "cwd": "Z:/definitely/not/here",
        "tool_input": {"command": "git commit -m 'chore: no claim here'"},
    }
    assert claude_hook.main(stdin_text=json.dumps(payload), argv=[]) == 0


def test_an_unexpected_internal_error_still_allows(monkeypatch):
    # The backstop: no internal defect, present or future, may block.
    from claim_check.entrypoints import claude_hook

    def boom(*a, **kw):
        raise RuntimeError("synthetic internal failure")

    monkeypatch.setattr(claude_hook, "extract_commit_message", boom)
    payload = {"tool_name": "Bash", "tool_input": {"command": "git commit -m '22 passed'"}}
    assert claude_hook.main(stdin_text=json.dumps(payload), argv=[]) == 0
```

Append to `tests/test_precommit_entrypoint.py`:

```python
def test_an_unexpected_internal_error_allows_the_commit(tmp_path, monkeypatch):
    # A commit-msg hook exits nonzero to abort the commit, so any unhandled
    # exception aborts an honest commit with a raw traceback. Confirmed end
    # to end before this guard existed: a test printing a line shaped like
    # "deploy finished in 1.2.3s" blocked the commit.
    from claim_check.entrypoints import precommit

    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("22 passed", encoding="utf-8")

    def boom(*a, **kw):
        raise RuntimeError("synthetic internal failure")

    monkeypatch.setattr(precommit, "run_pytest", boom)
    assert precommit.main([str(msg)]) == 0


def test_non_positive_timeout_is_rejected_loudly(tmp_path):
    from claim_check.entrypoints import precommit

    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("22 passed", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        precommit.main([str(msg), "--timeout", "0"])
    assert excinfo.value.code == 2
```

`tests/test_precommit_entrypoint.py` currently imports only `sys` and `main`; add `import pytest` at the top for `pytest.raises`.

Append to `tests/test_cli.py`:

```python
def test_pytest_output_pointing_at_a_directory_fails_open(tmp_path, capsys):
    # Only FileNotFoundError was caught; a directory raises PermissionError
    # on Windows and IsADirectoryError on Linux.
    from claim_check.cli import verify_tests

    assert verify_tests("22 passed", pytest_output_file=tmp_path) == 0
    assert "could not verify" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_claude_hook_entrypoint.py tests/test_precommit_entrypoint.py tests/test_cli.py -v -k "malformed or null_or_missing or nonexistent_cwd or internal_error or non_positive or directory_fails_open"`

Expected: FAIL with `AttributeError: 'NoneType' object has no attribute 'get'`, `TypeError: expected string or bytes-like object`, `RuntimeError: synthetic internal failure`, and `PermissionError`.

- [ ] **Step 3: Rewrite `claude_hook.py` below the imports**

Add `from pathlib import Path` and `from .._args import positive_timeout` to the imports, then replace `_parse_args` and `main` with:

```python
def _parse_args(argv: Optional[Sequence[str]]):
    parser = argparse.ArgumentParser(prog="claim-check-claude-hook")
    parser.add_argument(
        "--command",
        default=None,
        help=(
            "Override the test-runner command (default: '<python> -m pytest'). "
            "Needed when this hook's own environment isn't the one with the "
            "project's real test dependencies, e.g. --command \"poetry run pytest\"."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=positive_timeout,
        default=DEFAULT_TIMEOUT_S,
        help=f"Kill the test run and fail open after this many seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    return parser.parse_args(argv if argv is not None else sys.argv[1:])


def _resolve_cwd(payload: dict) -> str:
    """payload.get("cwd", ".") returns None when the key is present and
    explicitly null, which reached subprocess as the literal directory
    "None"."""
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and Path(cwd).is_dir():
        return cwd
    return "."


def _run(stdin_text: Optional[str], args) -> int:
    if stdin_text is None and sys.stdin.isatty():
        # Claude Code always pipes the hook JSON in (confirmed: a piped
        # stdin reports isatty() == False), so this never fires in real
        # use. It only catches a developer running the command bare in an
        # interactive terminal, which would otherwise hang indefinitely
        # waiting for input that's never coming.
        return 0

    raw = stdin_text if stdin_text is not None else sys.stdin.read()

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return 0

    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0

    # Every field below is type-guarded rather than trusted. Claude Code
    # sends a well-formed payload today, but an unhandled AttributeError or
    # TypeError here contradicts this module's whole contract.
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0

    message = extract_commit_message(command)
    if message is None:
        return 0

    claims = extract_claims(message)
    if not claims:
        return 0

    run_result = run_pytest(_resolve_cwd(payload), timeout_s=args.timeout, command=args.command)
    verdict = compare_claims(claims, run_result.counts)

    if verdict.status == "mismatch":
        print(_deny_json(verdict.message))
        return 0

    if verdict.status == "runner_error":
        print(f"claim-check: WARNING - could not verify ({run_result.parse_error}); allowing commit", file=sys.stderr)

    # match, no_claim, or runner_error (fails open per the resolved crash
    # policy) all allow: no stdout JSON means Claude Code proceeds normally.
    return 0


def main(stdin_text: Optional[str] = None, argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        return _run(stdin_text, args)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all backstop
        # Nothing this hook can hit internally is evidence a claim is wrong.
        # Argument parsing is deliberately left outside this guard: a
        # mistyped flag is a configuration error, and a hook that silently
        # ignores its own misconfiguration verifies nothing forever.
        print(
            f"claim-check: WARNING - could not verify (internal error: {exc!r}); allowing commit",
            file=sys.stderr,
        )
        return 0
```

- [ ] **Step 4: Rewrite `precommit.py`'s `main`**

Add `from .._args import positive_timeout` to the imports, change the `--timeout` argument to `type=positive_timeout`, and split the body:

```python
def _verify(message: str, args) -> int:
    claims = extract_claims(message)
    if not claims:
        return RESULT_SUCCESS

    run_result = run_pytest(args.cwd, timeout_s=args.timeout, command=args.command)
    verdict = compare_claims(claims, run_result.counts)

    if verdict.status == "mismatch":
        print(f"claim-check: {verdict.message}")
        return RESULT_FAIL

    if verdict.status == "runner_error":
        print(f"claim-check: WARNING - could not verify ({run_result.parse_error}); allowing commit")

    return RESULT_SUCCESS


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="claim-check-precommit")
    parser.add_argument("input", help="A file containing a git commit message")
    parser.add_argument("--cwd", default=".", help="Directory to run pytest in")
    parser.add_argument(
        "--command",
        default=None,
        help=(
            "Override the test-runner command (default: '<python> -m pytest'). "
            "Needed when this hook's own environment isn't the one with the "
            "project's real test dependencies, e.g. --command \"poetry run pytest\"."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=positive_timeout,
        default=DEFAULT_TIMEOUT_S,
        help=f"Kill the test run and fail open after this many seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.input, encoding="utf-8") as f:
            message = f.read()
    except (UnicodeDecodeError, OSError):
        # UnicodeDecodeError: corrupted bytes, not evidence a claim is wrong.
        # OSError (covers FileNotFoundError, PermissionError, etc.): git and
        # pre-commit always pass a real COMMIT_EDITMSG path, but a developer
        # manually testing this hook from a terminal with a typo'd or
        # nonexistent path shouldn't get an unhandled traceback for it.
        print("claim-check: commit message file missing or not valid UTF-8; skipping verification")
        return RESULT_SUCCESS

    try:
        return _verify(message, args)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all backstop
        # This runs as a commit-msg hook, where any nonzero exit aborts the
        # commit. No internal defect may ever do that: a crash is not
        # evidence a claim is wrong. Argument parsing stays outside the
        # guard on purpose - see _args.positive_timeout.
        print(f"claim-check: WARNING - could not verify (internal error: {exc!r}); allowing commit")
        return RESULT_SUCCESS
```

- [ ] **Step 5: Harden `cli.py`**

Add `from ._args import positive_timeout` to the imports and change the `--timeout` argument in `main` to `type=positive_timeout`. Then replace the whole `verify_tests` function (lines 14-57) with a thin wrapper plus the existing body, so the catch-all covers the message read as well as the run:

```python
def verify_tests(
    path_or_message: str,
    cwd: Path = Path("."),
    pytest_args: Sequence[str] = (),
    pytest_output_file: Optional[Path] = None,
    command: Optional[str] = None,
    timeout_s: Optional[float] = DEFAULT_TIMEOUT_S,
) -> int:
    """Returns a process exit code: 0 for match/no_claim/runner_error
    (runner_error fails open - see compare.py), 1 for mismatch.

    Every internal failure returns 0 with a warning rather than raising.
    This shares its policy with the two hook entry points: a crash is not
    evidence a claim is wrong, and the CLI is also invoked from hooks and
    CI wrappers where a traceback would read as a verification failure.
    """
    try:
        return _verify_tests(
            path_or_message, cwd, pytest_args, pytest_output_file, command, timeout_s
        )
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all backstop
        print(f"claim-check: WARNING - could not verify (internal error: {exc!r}); allowing commit")
        return 0


def _verify_tests(
    path_or_message: str,
    cwd: Path,
    pytest_args: Sequence[str],
    pytest_output_file: Optional[Path],
    command: Optional[str],
    timeout_s: Optional[float],
) -> int:
    if os.path.isfile(path_or_message):
        message = Path(path_or_message).read_text(encoding="utf-8", errors="replace")
    else:
        message = path_or_message

    claims = extract_claims(message)
    if not claims:
        print("claim-check: no test-count claim found; nothing to verify.")
        return 0

    if pytest_output_file is not None:
        try:
            captured = Path(pytest_output_file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            # Not just FileNotFoundError: a directory path raises
            # PermissionError on Windows and IsADirectoryError on Linux.
            print(
                f"claim-check: WARNING - could not verify (pytest output file "
                f"{pytest_output_file} not readable); allowing commit"
            )
            return 0
        run_result = result_from_captured_output(0, captured)
    else:
        run_result = run_pytest(cwd, pytest_args, timeout_s=timeout_s, command=command)

    verdict = compare_claims(claims, run_result.counts)

    if verdict.status == "match":
        print(f"claim-check: OK - {verdict.message}")
        return 0
    if verdict.status == "runner_error":
        print(f"claim-check: WARNING - could not verify ({run_result.parse_error}); allowing commit")
        return 0
    print(f"claim-check: MISMATCH - {verdict.message}")
    return 1
```

The existing CLI tests assert only that `"WARNING"` appears in the output, never the exact wording, so changing "not found" to "not readable" is safe.

- [ ] **Step 6: Run the entry-point tests**

Run: `python -m pytest tests/test_cli.py tests/test_precommit_entrypoint.py tests/test_claude_hook_entrypoint.py -v`

Expected: PASS, including all pre-existing tests in those three files.

- [ ] **Step 7: Verify `--help` still exits 0 on all three entry points**

Run:

```bash
claim-check verify-tests --help > /dev/null && claim-check-precommit --help > /dev/null && claim-check-claude-hook --help > /dev/null && echo "ALL THREE OK"
```

Expected: `ALL THREE OK`. CI asserts this; a catch-all that swallowed `SystemExit(0)` would break it.

- [ ] **Step 8: Run the whole suite and commit**

Run: `python -m pytest -q`

```bash
git add src/claim_check/cli.py src/claim_check/entrypoints/ tests/
git commit -m "Guarantee every entry point fails open on any internal error"
```

---

### Task 5: Shell parser coverage and placeholder leak

Fixes findings 13, 14. `git commit` must currently be tokens 0 and 1, so `git -C path commit` is silently unverified; and a heredoc with surrounding text leaks the internal `\x00HEREDOC1\x00` sentinel into the extracted message.

**Files:**
- Modify: `src/claim_check/shell_parser.py`
- Test: `tests/test_shell_parser.py`

**Interfaces:**
- Produces: `extract_commit_message(command: str) -> Optional[str]`, signature unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shell_parser.py`:

```python
def test_git_global_flags_before_the_subcommand_are_skipped():
    # _find_git_commit_segment required segment[0] == "git" and
    # segment[1] == "commit", so any of git's own global options in between
    # meant the commit went completely unverified - silently, since the
    # parser fails open. "git -C <path> commit" is what a script or an
    # agent driving git from another directory actually writes.
    assert extract_commit_message('git -C /repo commit -m "22 passed"') == "22 passed"
    assert extract_commit_message('git --no-pager commit -m "22 passed"') == "22 passed"
    assert extract_commit_message('git -c user.name=x commit -m "22 passed"') == "22 passed"
    assert extract_commit_message('git --git-dir=/r/.git commit -m "22 passed"') == "22 passed"


def test_git_word_without_a_commit_subcommand_is_still_ignored():
    assert extract_commit_message('git -C /repo status') is None
    assert extract_commit_message('echo "git commit -m \\"22 passed\\""') is None


def test_heredoc_surrounded_by_text_resolves_instead_of_leaking_a_placeholder():
    # The heredoc body was only substituted when the token matched a
    # placeholder exactly, so any surrounding text left the internal
    # sentinel "\x00HEREDOC1\x00" in the message handed to the claim parser.
    command = 'git commit -m "prefix $(cat <<\'EOF\'\n22 passed\nEOF\n) suffix"'
    message = extract_commit_message(command)
    assert message is not None
    assert "\x00" not in message
    assert "22 passed" in message
    assert "prefix" in message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_shell_parser.py -v -k "global_flags or without_a_commit or surrounded_by_text"`

Expected: FAIL. The global-flag assertions return `None`; the heredoc test fails on `assert "\x00" not in message`. `test_git_word_without_a_commit_subcommand_is_still_ignored` should PASS already, it is a guard against the fix over-reaching.

- [ ] **Step 3: Replace `_find_git_commit_segment` in `shell_parser.py`**

Add above it:

```python
# git's own global options, which may appear between "git" and the
# subcommand. Only the ones that take a separate value need the two-token
# skip; the "--flag=value" forms are a single token.
_GIT_GLOBAL_FLAGS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--exec-path", "--namespace"}
_GIT_GLOBAL_BOOLEAN_FLAGS = {
    "--no-pager",
    "--paginate",
    "--bare",
    "--literal-pathspecs",
    "--no-replace-objects",
}
```

Replace the function with:

```python
def _find_git_commit_segment(segments: list):
    """Returns the token list starting at "commit", or None.

    Skips git's own global options first: requiring "commit" to be
    literally the second token meant "git -C /repo commit" - the ordinary
    way to drive git from another directory - went entirely unverified.
    A leading wrapper ("sudo git commit", "env FOO=1 git commit") is still
    unsupported and documented as such; this only relaxes git's own flags.
    """
    for segment in segments:
        if not segment or segment[0] != "git":
            continue

        i = 1
        while i < len(segment):
            token = segment[i]
            if token in _GIT_GLOBAL_FLAGS_WITH_VALUE:
                i += 2
                continue
            if token in _GIT_GLOBAL_BOOLEAN_FLAGS:
                i += 1
                continue
            if token.startswith("--") and "=" in token and token.split("=", 1)[0] in _GIT_GLOBAL_FLAGS_WITH_VALUE:
                i += 1
                continue
            break

        if i < len(segment) and segment[i] == "commit":
            return segment[i:]

    return None
```

- [ ] **Step 4: Replace the placeholder resolution at the end of `extract_commit_message`**

Replace the `resolved` loop with:

```python
    resolved = []
    for value in values:
        if any(key in value for key in heredocs):
            if unresolved:
                # This heredoc's body relies on shell expansion we can't
                # perform; extracting it verbatim could produce a message
                # that doesn't match what git will actually receive.
                return None
            # Substring substitution, not exact-token equality: a heredoc
            # with text around it ("-m \"prefix $(cat <<'EOF' ...)\"")
            # otherwise left the internal sentinel in the message.
            for key, body in heredocs.items():
                value = value.replace(key, body)
        resolved.append(value)

    return "\n\n".join(resolved)
```

- [ ] **Step 5: Run the shell parser tests and the whole suite**

Run: `python -m pytest tests/test_shell_parser.py -v && python -m pytest -q`

Expected: PASS. The pre-existing heredoc tests must still pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/claim_check/shell_parser.py tests/test_shell_parser.py
git commit -m "Cover git's global flags and stop leaking the heredoc placeholder"
```

---

### Task 6: Vacuity consistency in the comparison policy

Fixes finding 15. `all tests pass` against 0 collected tests is correctly flagged, but `0/0 tests pass` against 0 collected currently matches, the same vacuous situation treated two different ways.

**Files:**
- Modify: `src/claim_check/compare.py`
- Test: `tests/test_compare.py`

**Interfaces:**
- Produces: `compare_claims(claims, counts) -> Verdict`, signature unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_compare.py`. That file builds `Claim` objects directly through its own `_n_of_m(n, m)` and `_counts(...)` helpers rather than going through the claim parser, follow that pattern, and add no new imports:

```python
def test_zero_of_zero_claim_with_zero_collected_tests_is_flagged_like_all_pass_is():
    # "all tests pass" against 0 collected is already flagged as vacuous.
    # "0/0 tests pass" describes the identical situation and was matching,
    # so the same claim passed or failed depending only on phrasing.
    verdict = compare_claims([_n_of_m(0, 0)], _counts(passed=0, failed=0))
    assert verdict.status == "mismatch"
    assert "0 tests were collected" in verdict.message
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_compare.py -v -k zero_of_zero`

Expected: FAIL with `assert 'match' == 'mismatch'`.

- [ ] **Step 3: Add the guard to the `n_of_m` branch of `_mismatch_reason`**

In `src/claim_check/compare.py`, change the `n_of_m` branch to:

```python
    if claim.kind == "n_of_m":
        if counts.total == 0:
            # Same vacuity guard the all_pass branch already applies: a
            # ratio claim against a run that collected nothing is not
            # something a pytest run can make true.
            return f'claimed "{claim.raw_text}" but 0 tests were collected'
        if claim.claimed_passed != counts.passed or claim.claimed_total != counts.total:
            return f'claimed "{claim.raw_text}" but actual result is {counts.passed}/{counts.total}'
        return None
```

- [ ] **Step 4: Run the compare tests and the whole suite**

Run: `python -m pytest tests/test_compare.py -v && python -m pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/claim_check/compare.py tests/test_compare.py
git commit -m "Treat a zero-denominator ratio claim as vacuous, like all_pass already does"
```

---

### Task 7: End-to-end reverification and documentation

Success criteria 2 and 4 from the spec. Every fix so far is unit-tested; this proves the original reproductions are actually dead against a real git repository, then updates the README, including its own test-count claim, which this repo's hook checks.

**Files:**
- Create: `scripts/smoke_e2e.sh`
- Modify: `README.md`

- [ ] **Step 1: Create the end-to-end reverification script**

Create `scripts/smoke_e2e.sh`:

```bash
#!/usr/bin/env bash
# Reverifies the six end-to-end reproductions that motivated the hardening
# pass. Usage: scripts/smoke_e2e.sh <scratch-dir>
set -u
ROOT="${1:?usage: smoke_e2e.sh <scratch-dir>}"
rm -rf "$ROOT"; mkdir -p "$ROOT"; cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1

git init -q .; git config user.email s@t.local; git config user.name Smoke
printf '__pycache__/\n' > .gitignore
mkdir -p tests
cat > .git/hooks/commit-msg <<'HOOK'
#!/usr/bin/env bash
exec claim-check-precommit "$1"
HOOK
chmod +x .git/hooks/commit-msg
git add -A >/dev/null; git commit -q -m "chore: init" >/dev/null 2>&1

FAILURES=0
try() {  # try <label> <ACCEPT|BLOCK> <msg>
  local label="$1" expect="$2" msg="$3"
  out=$(git commit --allow-empty -m "$msg" 2>&1)
  if [ $? -eq 0 ]; then got="ACCEPT"; else got="BLOCK"; fi
  if [ "$got" = "$expect" ]; then mark="ok  "; else mark="FAIL"; FAILURES=$((FAILURES+1)); fi
  printf "%s  %-6s expected=%-6s  %s\n" "$mark" "$got" "$expect" "$label"
  echo "$out" | grep -E 'claim-check|Traceback' | head -2 | sed 's/^/         /'
}

echo "=== 22 passing + 1 failing ==="
cat > tests/test_suite.py <<'PY'
import pytest
@pytest.mark.parametrize("i", range(22))
def test_ok(i): assert True
def test_broken(): assert False
PY
try "ratio lie spelled 'passed'"      BLOCK  "x

22/22 passed"
try "ratio lie spelled 'tests pass'"  BLOCK  "x

22/22 tests pass"
try "negated ratio claim"             ACCEPT "x

not 22/22 passed"

echo "=== forged summary line from a failing test ==="
cat > tests/test_suite.py <<'PY'
import pytest
@pytest.mark.parametrize("i", range(21))
def test_ok(i): assert True
def test_broken():
    print("==== 1 passed in 0.01s ====")
    assert False
PY
try "forged count"                    BLOCK  "x

22 passed"

echo "=== version-shaped duration in captured output ==="
cat > tests/test_suite.py <<'PY'
def test_broken():
    print("==================== deploy finished in 1.2.3s ====================")
    assert False
def test_ok(): assert True
PY
try "poisoned duration, honest claim" ACCEPT "x

1 passed"

echo "=== 60k-character separator line ==="
cat > tests/test_suite.py <<'PY'
def test_broken():
    print("=" * 60000)
    assert False
def test_ok(): assert True
PY
S=$(date +%s)
try "long separator, honest claim"    ACCEPT "x

1 passed"
E=$(date +%s)
echo "         hook wall time: $((E-S))s (was 12s before the fix)"

echo
if [ $FAILURES -eq 0 ]; then echo "ALL E2E SCENARIOS PASS"; else echo "$FAILURES E2E SCENARIO(S) FAILED"; exit 1; fi
```

- [ ] **Step 2: Run the end-to-end script**

Run: `bash scripts/smoke_e2e.sh /tmp/claim-check-e2e`

Expected: `ALL E2E SCENARIOS PASS`, and the reported hook wall time under 2 seconds. `claim-check` must be installed in the active environment first (`pip install -e .`).

- [ ] **Step 3: Get the real, current test count**

Run: `python -m pytest -q 2>&1 | tail -1`

Expected: a line like `NNN passed in X.XXs`. **Record the exact number.** Do not estimate it, and do not reuse 110, the README states this count, and this repo's own hook verifies it.

- [ ] **Step 4: Update the README**

Make these edits to `README.md`:

1. Line 13: replace `110 cases` with the exact number recorded in Step 3.
2. In the scenario table, add these rows:

```markdown
| A ratio claim spelled `"22/22 passed"` rather than `"22/22 tests pass"` | Both are the same claim and both check the denominator; the bare-count regex matches the contained substring `"22 passed"` at a later offset, and last-start-wins used to pick it, silently dropping the total |
| `"not 22/22 passed"`, `"never 9/9 passed"` (negation before a ratio spelled `passed`) | Never registers as a claim; enclosed matches are discarded before the negation filter runs, or the contained bare-count match survives a negation that applied to the phrase as a whole |
| `"1,022 passed"` (a grouped thousands separator) | Not read as a claim of 22; the lookbehind blocks `,` alongside `.`, `#` and digits |
| A summary line printed by a test and replayed in pytest's failure report | Cannot forge the tally: the last summary line *per session* is authoritative, and a test's output is always replayed before its own session's summary |
| A line shaped like `"deploy finished in 1.2.3s"` in captured output | Not a summary line, and never a crash; the duration pattern is a strict number rather than any run of digits and dots |
| A 60,000-character separator line in captured output | Parsed in linear time; the previous pattern backtracked quadratically and took 12 seconds inside the commit-msg hook, which `--timeout` does not cover |
| `git -C <path> commit`, `git --no-pager commit` | Verified; git's own global options are skipped when locating the subcommand |
| A nonexistent `--cwd`, an unbalanced quote in `--command`, a malformed Claude Code hook payload | All fail open with a specific reason; every entry point also has a catch-all so no internal error can ever block a commit |
```

3. In the `### Timeouts` section, after the existing paragraph, add:

```markdown
`--timeout` must be greater than zero; `0` and negative values are rejected with an error rather than accepted. This is a deliberate exception to this tool's fail-open rule: a non-positive timeout kills every run the instant it starts and then fails open, so the tool prints "could not verify ... allowing commit" on every commit forever while looking like it is working. Silently verifying nothing is worse than a loud error about a mistyped flag.
```

4. In `## What this is not`, add these bullets:

```markdown
- Not proof against a test that deliberately forges a whole pytest session. Output printed by a test is replayed inside pytest's failure report, which always precedes that session's real summary line, so the last-line-per-session rule defeats the realistic case. A test that emits a complete fake `test session starts` header *and* a trailing summary, or a wrapper that appends summary-shaped text after pytest exits, can still be believed. Anything that can do that can already edit the test suite.
- Still flags an honest claim that names an error alongside its count. `"22 passed, 1 error"` against a real 22-passed-1-error run is reported as a mismatch, because an error means some test never ran and the true denominator is unknown. The strictness is deliberate: it is the guarantee that makes a bare count meaningful at all.
- Only `git commit` invoked as `git [global options] commit`. A leading wrapper (`sudo git commit`, `env FOO=1 git commit`) is not recognised and goes unverified.
```

- [ ] **Step 5: Verify the README's own count claim with the tool itself**

Run:

```bash
claim-check verify-tests "$(grep -o '[0-9]\+ cases' README.md | head -1 | sed 's/ cases/ passed/')"
```

Expected: `claim-check: OK - All test-count claims match the actual results.`

If it reports a mismatch, the number in the README is wrong, fix the README, not the test suite.

- [ ] **Step 6: Run the whole suite one final time**

Run: `python -m pytest -q`

Expected: PASS, with the count matching what the README now states.

- [ ] **Step 7: Commit**

```bash
git add README.md scripts/smoke_e2e.sh
git commit -m "Document the hardened guarantees and add the end-to-end reverification script"
```

---

## Verification checklist

Before considering this plan complete:

- [ ] `python -m pytest -q` passes, and the count is at least 110 + the new tests.
- [ ] `bash scripts/smoke_e2e.sh <dir>` prints `ALL E2E SCENARIOS PASS`.
- [ ] `claim-check verify-tests --help`, `claim-check-precommit --help`, `claim-check-claude-hook --help` all exit 0.
- [ ] The README's stated case count matches real pytest output, verified with claim-check itself.
- [ ] No file in `src/claim_check/` imports anything outside the standard library.
- [ ] `git log --oneline` shows one commit per task, each with its tests.
