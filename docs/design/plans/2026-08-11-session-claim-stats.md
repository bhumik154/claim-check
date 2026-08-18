# Session Claim Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make unverified test-count claims visible, with the cause and a session ratio, instead of silently saying nothing.

**Architecture:** A new `session_stats` module replays the Claude Code transcript and classifies every claim turn in the session as backed by a whole-suite run or unbacked with a cause. Nothing is stored: the numbers are recomputed on demand, because a stale counter lies confidently and that is the failure this project exists to prevent. The full replay runs only on turns that both state a claim and are unbacked, measured at 1.5 times per session.

**Tech Stack:** Python 3.9+, standard library only, pytest. No new dependencies.

**Spec:** `docs/design/specs/2026-08-11-session-claim-stats-design.md`

## Global Constraints

- **Zero runtime dependencies.** Standard library only. Do not add anything to `pyproject.toml` dependencies.
- **Python 3.9 floor.** No `match`, no `X | Y` unions in annotations at runtime, no `dict[str, int]` bare builtins in evaluated positions. Use `typing.Optional`, `typing.Mapping`.
- **No em-dashes or en-dashes anywhere**, in code, comments, docs or commit messages. Use a hyphen with spaces. This is checked in Task 9.
- **The non-negotiable rule: no claim, no output, ever.** Nothing in this plan may cause any path to emit anything on a turn that states no test-count claim.
- **Never use the word "verified" in the rendered ratio.** The measured quantity is "had a whole-suite run behind them". Reconstruction cannot see fingerprint invalidation, so "verified" would be an overclaim.
- **No test may hard code the expected distribution of causes.** The TTL is changing in later work and the mix will shift. Assert that a stale session reports the stale cause; never assert that stale is common.
- **Fail open, always.** Any failure computing statistics results in `None` and the cause sentence printing alone. Never an exception, never a changed exit code.
- **ASCII only in printed strings.** This project has hit cp1252 console encoding twice.

---

### Task 1: Spike - confirm the PreToolUse payload carries `transcript_path`

No production code. This answers an open question that determines whether Task 7 is possible. If the answer is no, Task 7 is dropped and the commit path prints the cause sentence without a ratio.

**Files:**
- Create: `docs/design/m1-pretooluse-payload-findings.md`

- [ ] **Step 1: Enable payload dumping and restart**

The dump only happens when the environment variable is set at Claude Code start, because hooks inherit the environment of the parent process.

```bash
setx CLAIM_CHECK_DEBUG_DUMP "%LOCALAPPDATA%\claim-check-debug"
```

Then fully restart Claude Code. Confirm with a fresh terminal that `echo $CLAIM_CHECK_DEBUG_DUMP` is non-empty.

- [ ] **Step 2: Trigger a PreToolUse hook and a Stop hook**

In a Claude Code session, ask the agent to run any Bash command (this fires PreToolUse) and let the turn end (this fires Stop).

- [ ] **Step 3: Inspect the dumped payloads for the key**

```bash
python - <<'PY'
import json, os, pathlib
root = pathlib.Path(os.environ["CLAIM_CHECK_DEBUG_DUMP"])
for f in sorted(root.rglob("*.json"))[-10:]:
    try:
        d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        continue
    print(f.name, "|", d.get("hook_event_name"), "| transcript_path:", repr(d.get("transcript_path"))[:80])
PY
```

Expected: at least one record with `hook_event_name` of `PreToolUse`. Record whether `transcript_path` is present and whether the path exists on disk.

- [ ] **Step 4: Write the finding**

Create `docs/design/m1-pretooluse-payload-findings.md` stating: the date, the Claude Code version (`claude --version`), whether `transcript_path` was present on PreToolUse, whether the file existed, and the verdict "Task 7 proceeds" or "Task 7 dropped".

- [ ] **Step 5: Turn dumping back off**

```bash
setx CLAIM_CHECK_DEBUG_DUMP ""
```

- [ ] **Step 6: Commit**

```bash
git add docs/design/m1-pretooluse-payload-findings.md
git commit -m "Measure whether PreToolUse payloads carry transcript_path"
```

---

### Task 2: Extract `assistant_text` from `last_assistant_text`

Pure refactor, no behaviour change. `last_assistant_text` has the text-block extraction inline at `src/claim_check/transcript.py:56-63`. The replay needs the same logic, so it becomes a named function rather than being copied.

**Files:**
- Modify: `src/claim_check/transcript.py:56-63`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Produces: `transcript.assistant_text(record: dict) -> str` - the joined prose of an assistant record, `""` when the record has no text blocks or is not shaped like an assistant message.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_transcript.py`:

```python
from claim_check.transcript import assistant_text


def test_assistant_text_joins_only_text_blocks():
    record = {
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": "first"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "text", "text": "second"},
        ]},
    }
    assert assistant_text(record) == "first\nsecond"


def test_assistant_text_returns_empty_for_malformed_records():
    assert assistant_text({}) == ""
    assert assistant_text({"message": None}) == ""
    assert assistant_text({"message": {"content": "not a list"}}) == ""
    assert assistant_text({"message": {"content": [{"type": "text"}]}}) == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_transcript.py -k assistant_text -v`
Expected: FAIL with `ImportError: cannot import name 'assistant_text'`

- [ ] **Step 3: Add the function and call it from `last_assistant_text`**

In `src/claim_check/transcript.py`, add after `last_assistant_text`:

```python
def assistant_text(record: dict) -> str:
    """The joined prose of an assistant record, or "" if it carries none.

    Text blocks only. A record holding just tool calls contains no prose and
    therefore no claim.
    """
    if not isinstance(record, dict):
        return ""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    blocks = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(block for block in blocks if isinstance(block, str))
```

Then replace lines 49-63 of `last_assistant_text` (the `message = record.get("message")` block through `if text.strip(): return text`) with:

```python
        text = assistant_text(record)
        if text.strip():
            return text
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, count is the previous total plus 2. No existing test changes meaning.

- [ ] **Step 5: Commit**

```bash
git add src/claim_check/transcript.py tests/test_transcript.py
git commit -m "Extract assistant_text so the replay can reuse it"
```

---

### Task 3: Whole-file record iteration with a wall clock budget

**Files:**
- Modify: `src/claim_check/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Produces:
  - `transcript.ReplayAborted` - exception raised when the budget is exceeded.
  - `transcript.iter_records(path, budget_s: Optional[float] = None) -> Iterator[dict]` - yields every parseable record in file order. Skips blank and corrupt lines. Yields nothing at all if the file is missing or unreadable. Raises `ReplayAborted` if `budget_s` elapses mid-iteration.
  - `transcript.record_timestamp(record: dict) -> Optional[float]` - the record's `timestamp` field as a Unix float, or `None`.
  - `transcript.DEFAULT_REPLAY_BUDGET_S = 2.0`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_transcript.py`:

```python
import json
import pytest
from claim_check.transcript import (
    ReplayAborted, iter_records, record_timestamp,
)


def _write_jsonl(path, records):
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return path


def test_iter_records_yields_every_record_in_file_order(tmp_path):
    p = _write_jsonl(tmp_path / "t.jsonl", [{"i": 0}, {"i": 1}, {"i": 2}])
    assert [r["i"] for r in iter_records(p)] == [0, 1, 2]


def test_iter_records_skips_corrupt_and_blank_lines_without_stopping(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"i": 0}\n\nnot json at all\n{"i": 1}\n', encoding="utf-8")
    assert [r["i"] for r in iter_records(p)] == [0, 1]


def test_iter_records_skips_a_truncated_final_line(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"i": 0}\n{"i": 1', encoding="utf-8")
    assert [r["i"] for r in iter_records(p)] == [0]


def test_iter_records_yields_nothing_for_a_missing_file(tmp_path):
    assert list(iter_records(tmp_path / "nope.jsonl")) == []


def test_iter_records_yields_nothing_for_a_directory(tmp_path):
    # A directory raises PermissionError on Windows, IsADirectoryError on Linux.
    assert list(iter_records(tmp_path)) == []


def test_iter_records_yields_nothing_for_an_unusable_path_type():
    assert list(iter_records(None)) == []


def test_iter_records_raises_when_the_budget_is_exceeded(tmp_path):
    p = _write_jsonl(tmp_path / "t.jsonl", [{"i": n} for n in range(5000)])
    with pytest.raises(ReplayAborted):
        # A zero budget is already spent, so the first check trips it.
        list(iter_records(p, budget_s=0.0))


def test_iter_records_completes_within_a_generous_budget(tmp_path):
    p = _write_jsonl(tmp_path / "t.jsonl", [{"i": n} for n in range(5000)])
    assert len(list(iter_records(p, budget_s=30.0))) == 5000


def test_record_timestamp_parses_iso8601_with_z():
    ts = record_timestamp({"timestamp": "2026-08-11T05:03:41.000Z"})
    assert ts is not None and ts > 0


def test_record_timestamp_returns_none_for_missing_or_malformed():
    assert record_timestamp({}) is None
    assert record_timestamp({"timestamp": 12345}) is None
    assert record_timestamp({"timestamp": "not a date"}) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_transcript.py -k "iter_records or record_timestamp" -v`
Expected: FAIL with `ImportError: cannot import name 'ReplayAborted'`

- [ ] **Step 3: Implement**

In `src/claim_check/transcript.py`, add imports at the top:

```python
import datetime
import time
from typing import Iterator
```

Add near `DEFAULT_WINDOW_BYTES`:

```python
# The Stop hook has a 10 second budget and spawning the interpreter already
# costs roughly 300ms. 2 seconds covers a transcript of several hundred MB at
# the measured parse rate, so in practice the replay always finishes.
DEFAULT_REPLAY_BUDGET_S = 2.0

# Checking the clock on every line would cost more than the parse it guards.
_BUDGET_CHECK_EVERY = 512


class ReplayAborted(Exception):
    """The replay ran out of wall clock budget.

    Raised rather than returning a short iterator on purpose. A truncated
    replay produces a plausible but wrong count, and a confidently wrong
    number is the exact failure this design avoids. The caller turns this
    into "no statistics available".
    """
```

Add the two functions at the end of the module:

```python
def iter_records(path, budget_s: Optional[float] = None) -> Iterator[dict]:
    """Every parseable record in the file, in order.

    Unlike `last_assistant_text` this reads the whole file. That is only
    affordable because callers reach it on a small fraction of turns; see
    the module docstring.

    Blank lines, corrupt lines and a truncated final line are skipped rather
    than treated as the end of the file. A missing or unreadable file yields
    nothing, consistent with every other reader here: unreadable is unknown,
    never false.
    """
    try:
        target = Path(path)
    except TypeError:
        return

    deadline = None if budget_s is None else time.monotonic() + budget_s
    try:
        with target.open("r", encoding="utf-8", errors="replace") as stream:
            for index, line in enumerate(stream):
                if deadline is not None and index % _BUDGET_CHECK_EVERY == 0:
                    if time.monotonic() >= deadline:
                        raise ReplayAborted(f"replay exceeded {budget_s}s")
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def record_timestamp(record: dict) -> Optional[float]:
    """The record's timestamp as a Unix float, or None."""
    if not isinstance(record, dict):
        return None
    raw = record.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp()
```

- [ ] **Step 4: Correct the module docstring**

The current docstring at `src/claim_check/transcript.py:1-11` says reading the whole file "is not an option", which this task contradicts. Replace it with:

```python
"""Reads Claude Code transcripts.

Two readers with different jobs. `last_assistant_text` inspects only the tail
of the file, because it runs at the end of every turn and transcripts in this
project's own sessions reached 21 MB. `iter_records` reads the whole file, and
is affordable only because its callers reach it on a small fraction of turns:
measured at 1.5 times per session, against 85ms for the largest transcript on
disk.

Every failure resolves to nothing: a missing file, an unreadable one, a corrupt
line, a message older than the window. Callers read that as "no claim to check"
and stay silent. A transcript we cannot read is not evidence that anything is
wrong.
"""
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, previous total plus 10.

- [ ] **Step 6: Commit**

```bash
git add src/claim_check/transcript.py tests/test_transcript.py
git commit -m "Add whole-file transcript iteration with a wall clock budget"
```

---

### Task 4: `SessionStats` and its rendering

The value type and its wording, with no replay logic. Separated so the wording rules, which are the load-bearing part, get their own tests and their own review.

**Files:**
- Create: `src/claim_check/session_stats.py`
- Test: `tests/test_session_stats.py`

**Interfaces:**
- Produces:
  - `session_stats.NO_RUN = "no_run"`, `session_stats.NARROWED_ONLY = "narrowed_only"`, `session_stats.STALE = "stale"`
  - `session_stats.SessionStats(backed: int, unbacked: int, causes: Mapping[str, int])`, frozen dataclass
  - `SessionStats.render() -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_stats.py`:

```python
from claim_check.session_stats import (
    NARROWED_ONLY, NO_RUN, STALE, SessionStats,
)


def test_render_states_the_ratio_and_the_dominant_cause():
    stats = SessionStats(backed=2, unbacked=4, causes={NO_RUN: 3, STALE: 1})
    assert stats.render() == (
        "(this session: 2 of 6 claims had a whole-suite run behind them; "
        "3 had no test run at all)"
    )


def test_render_never_claims_anything_was_verified():
    # The statistics are reconstructed from the transcript and cannot see
    # fingerprint invalidation, so "verified" would be an overclaim.
    stats = SessionStats(backed=5, unbacked=1, causes={STALE: 1})
    assert "verif" not in stats.render().lower()


def test_render_reads_correctly_for_each_cause():
    for cause, phrase in [
        (NO_RUN, "had no test run at all"),
        (NARROWED_ONLY, "had only narrowed runs"),
        (STALE, "had a run too old to use"),
    ]:
        stats = SessionStats(backed=0, unbacked=1, causes={cause: 1})
        assert phrase in stats.render()


def test_render_breaks_cause_ties_deterministically():
    stats = SessionStats(backed=0, unbacked=2, causes={STALE: 1, NO_RUN: 1})
    assert stats.render() == SessionStats(
        backed=0, unbacked=2, causes={NO_RUN: 1, STALE: 1}
    ).render()


def test_render_omits_the_cause_clause_when_there_is_none():
    stats = SessionStats(backed=3, unbacked=0, causes={})
    assert stats.render() == (
        "(this session: 3 of 3 claims had a whole-suite run behind them)"
    )


def test_render_is_empty_when_no_claims_were_seen():
    assert SessionStats(backed=0, unbacked=0, causes={}).render() == ""


def test_render_ignores_unknown_cause_keys():
    # Forward compatibility: a cause added later must not crash an older render.
    stats = SessionStats(backed=0, unbacked=1, causes={"something_new": 1})
    assert stats.render() == (
        "(this session: 0 of 1 claims had a whole-suite run behind them)"
    )


def test_render_is_pure_ascii():
    # This project has hit cp1252 console encoding twice.
    stats = SessionStats(backed=1, unbacked=2, causes={NO_RUN: 2})
    stats.render().encode("ascii")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_session_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'claim_check.session_stats'`

- [ ] **Step 3: Implement**

Create `src/claim_check/session_stats.py`:

```python
"""Session-level statistics about test-count claims.

Answers one question for a single session: of the claims made, how many had a
whole-suite test run behind them, and for the rest, why not.

Nothing is stored. The numbers are recomputed from the transcript on demand,
because the alternative is a counter that can go stale and then lies
confidently, which is the failure this whole project exists to prevent.

The reported quantity is deliberately "had a whole-suite run behind them" and
never "verified". These statistics are reconstructed from the transcript,
while the live check additionally requires the source fingerprint to be
unchanged, and a transcript cannot show that. Reconstruction can therefore
count a claim as backed that the live check would have rejected as stale.
Naming the weaker thing that is actually measured keeps the sentence true.
"""

from dataclasses import dataclass
from typing import Mapping

NO_RUN = "no_run"
NARROWED_ONLY = "narrowed_only"
STALE = "stale"

# Fixed order, so equal counts always render the same way. Without this the
# message would depend on dict insertion order, which depends on the order
# turns happen to appear in the transcript.
_CAUSE_PHRASES = (
    (NO_RUN, "had no test run at all"),
    (NARROWED_ONLY, "had only narrowed runs"),
    (STALE, "had a run too old to use"),
)


@dataclass(frozen=True)
class SessionStats:
    backed: int
    unbacked: int
    causes: Mapping[str, int]

    def render(self) -> str:
        """The parenthetical appended to an unverified-claim warning.

        Empty when no claims were seen, so a caller can append it
        unconditionally.
        """
        total = self.backed + self.unbacked
        if total <= 0:
            return ""

        body = (
            f"this session: {self.backed} of {total} claims had a "
            "whole-suite run behind them"
        )

        dominant = self._dominant_cause()
        if dominant is None:
            return f"({body})"
        count, phrase = dominant
        return f"({body}; {count} {phrase})"

    def _dominant_cause(self):
        """The most common known cause as (count, phrase), or None.

        Unknown keys are ignored rather than rendered, so a cause added by a
        later version cannot produce a broken sentence here.
        """
        best = None
        for key, phrase in _CAUSE_PHRASES:
            count = self.causes.get(key, 0)
            if isinstance(count, int) and count > 0:
                if best is None or count > best[0]:
                    best = (count, phrase)
        return best
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_session_stats.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/claim_check/session_stats.py tests/test_session_stats.py
git commit -m "Add SessionStats and the wording rules for the session ratio"
```

---

### Task 5: `summarize_session` - the replay and classification

**Files:**
- Modify: `src/claim_check/session_stats.py`
- Test: `tests/test_session_stats.py`

**Interfaces:**
- Consumes: `transcript.iter_records`, `transcript.assistant_text`, `transcript.record_timestamp`, `transcript.ReplayAborted`, `transcript.DEFAULT_REPLAY_BUDGET_S` (Task 3); `conversation.extract_claims`; `pytest_parser.parse_summary_line`; `evidence.is_scoped`.
- Produces: `session_stats.summarize_session(transcript_path, ttl_s: float = evidence.DEFAULT_TTL_S, budget_s: Optional[float] = None) -> Optional[SessionStats]`

Classification, in order, for each turn that states a claim:

| condition | outcome |
|---|---|
| no parseable test run anywhere earlier in the session | `NO_RUN` |
| runs happened but none was whole-suite | `NARROWED_ONLY` |
| a whole-suite run happened but older than `ttl_s` | `STALE` |
| otherwise | backed |

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_session_stats.py`:

```python
import json
from claim_check.session_stats import summarize_session

_T0 = "2026-08-11T05:00:00.000Z"


def _assistant(text, ts=_T0):
    return {"type": "assistant", "timestamp": ts,
            "message": {"content": [{"type": "text", "text": text}]}}


def _bash(command, tool_id, ts=_T0):
    return {"type": "assistant", "timestamp": ts, "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Bash",
         "input": {"command": command}}]}}


def _result(stdout, tool_id, ts=_T0):
    return {"type": "user", "timestamp": ts,
            "toolUseResult": {"stdout": stdout},
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": tool_id}]}}


def _session(tmp_path, records, name="t.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


def test_claim_with_no_run_at_all_is_unbacked_as_no_run(tmp_path):
    p = _session(tmp_path, [_assistant("All tests pass now.")])
    stats = summarize_session(p)
    assert stats == SessionStats(backed=0, unbacked=1, causes={NO_RUN: 1})


def test_claim_after_a_whole_suite_run_is_backed(tmp_path):
    p = _session(tmp_path, [
        _bash("python -m pytest", "t1"),
        _result("===== 22 passed in 0.4s =====", "t1"),
        _assistant("All 22 tests pass."),
    ])
    stats = summarize_session(p)
    assert stats.backed == 1 and stats.unbacked == 0


def test_claim_after_only_a_narrowed_run_is_unbacked_as_narrowed(tmp_path):
    p = _session(tmp_path, [
        _bash("python -m pytest -k smoke", "t1"),
        _result("===== 3 passed, 19 deselected in 0.2s =====", "t1"),
        _assistant("All tests pass."),
    ])
    stats = summarize_session(p)
    assert stats == SessionStats(backed=0, unbacked=1, causes={NARROWED_ONLY: 1})


def test_claim_long_after_a_whole_suite_run_is_unbacked_as_stale(tmp_path):
    p = _session(tmp_path, [
        _bash("python -m pytest", "t1", ts="2026-08-11T05:00:00.000Z"),
        _result("===== 22 passed in 0.4s =====", "t1", ts="2026-08-11T05:00:00.000Z"),
        _assistant("All 22 tests pass.", ts="2026-08-11T06:00:00.000Z"),
    ])
    stats = summarize_session(p, ttl_s=900.0)
    assert stats == SessionStats(backed=0, unbacked=1, causes={STALE: 1})


def test_ttl_boundary_is_inclusive_on_both_sides(tmp_path):
    def at(offset_s):
        return _session(tmp_path, [
            _bash("python -m pytest", "t1", ts="2026-08-11T05:00:00.000Z"),
            _result("===== 1 passed in 0.1s =====", "t1", ts="2026-08-11T05:00:00.000Z"),
            _assistant("All tests pass.",
                       ts=f"2026-08-11T05:{offset_s // 60:02d}:{offset_s % 60:02d}.000Z"),
        ], name=f"t{offset_s}.jsonl")

    assert summarize_session(at(900), ttl_s=900.0).backed == 1
    assert summarize_session(at(901), ttl_s=900.0).backed == 0


def test_turns_without_a_claim_are_not_counted_at_all(tmp_path):
    p = _session(tmp_path, [
        _assistant("Refactored the loop for clarity."),
        _assistant("Renamed a variable."),
    ])
    assert summarize_session(p) == SessionStats(backed=0, unbacked=0, causes={})


def test_quoted_pytest_output_is_not_counted_as_a_claim(tmp_path):
    p = _session(tmp_path, [_assistant("Here is the output:\n\n```\n21 passed in 0.3s\n```")])
    assert summarize_session(p) == SessionStats(backed=0, unbacked=0, causes={})


def test_subagent_turns_are_not_counted_as_the_agent_claiming(tmp_path):
    record = _assistant("All tests pass.")
    record["isSidechain"] = True
    assert summarize_session(_session(tmp_path, [record])).unbacked == 0


def test_a_mixed_session_counts_each_turn_independently(tmp_path):
    p = _session(tmp_path, [
        _assistant("All tests pass."),                       # no run yet
        _bash("python -m pytest", "t1"),
        _result("===== 22 passed in 0.4s =====", "t1"),
        _assistant("All 22 tests pass."),                    # backed
        _assistant("Still 22 passing."),                     # backed
    ])
    stats = summarize_session(p)
    assert stats.backed == 2
    assert stats.unbacked == 1
    assert stats.causes == {NO_RUN: 1}


def test_missing_transcript_returns_none(tmp_path):
    assert summarize_session(tmp_path / "nope.jsonl") is None


def test_directory_path_returns_none(tmp_path):
    assert summarize_session(tmp_path) is None


def test_exceeding_the_budget_returns_none_rather_than_a_partial_count(tmp_path):
    p = _session(tmp_path, [_assistant("All tests pass.")] * 3000)
    assert summarize_session(p, budget_s=0.0) is None


def test_corrupt_lines_do_not_prevent_a_result(tmp_path):
    p = tmp_path / "t.jsonl"
    good = json.dumps(_assistant("All tests pass."))
    p.write_text(f"{good}\nnot json\n{good}\n", encoding="utf-8")
    assert summarize_session(p).unbacked == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_session_stats.py -k summarize -v`
Expected: FAIL with `ImportError: cannot import name 'summarize_session'`

- [ ] **Step 3: Implement**

Add to `src/claim_check/session_stats.py`. Extend the imports:

```python
import shlex
from pathlib import Path
from typing import Mapping, Optional

from . import evidence
from .conversation import extract_claims
from .pytest_parser import parse_summary_line
from .transcript import (
    DEFAULT_REPLAY_BUDGET_S, ReplayAborted, assistant_text, iter_records,
    record_timestamp,
)
```

Add at the end of the module:

```python
def summarize_session(
    transcript_path,
    ttl_s: float = evidence.DEFAULT_TTL_S,
    budget_s: Optional[float] = None,
) -> Optional[SessionStats]:
    """Statistics for one session, or None if they cannot be computed.

    None means "no statistics", never "no claims" and never "nothing was
    backed". Callers print their warning without the ratio rather than
    printing a number they are not sure of.
    """
    budget = DEFAULT_REPLAY_BUDGET_S if budget_s is None else budget_s

    commands = {}          # tool_use id -> the Bash command string
    saw_any_run = False
    saw_whole_suite = False
    whole_suite_at = None
    backed = 0
    causes = {}

    try:
        for record in iter_records(transcript_path, budget_s=budget):
            _register_commands(record, commands)

            if _is_test_run(record):
                saw_any_run = True
                if not _run_was_scoped(record, commands):
                    saw_whole_suite = True
                    whole_suite_at = record_timestamp(record) or whole_suite_at

            if record.get("type") != "assistant":
                continue
            if record.get("isSidechain") is True:
                # A subagent talking is not the main agent reporting.
                continue

            text = assistant_text(record)
            if not text.strip() or not extract_claims(text):
                continue

            cause = _classify(
                saw_any_run, saw_whole_suite, whole_suite_at,
                record_timestamp(record), ttl_s,
            )
            if cause is None:
                backed += 1
            else:
                causes[cause] = causes.get(cause, 0) + 1
    except ReplayAborted:
        return None

    if not _transcript_readable(transcript_path):
        return None

    return SessionStats(backed=backed, unbacked=sum(causes.values()), causes=causes)


def _transcript_readable(transcript_path) -> bool:
    """Distinguishes an empty session from an unreadable one.

    `iter_records` yields nothing in both cases by design, but "zero claims"
    and "could not read the file" must not produce the same answer here: the
    first is a real result, the second is no result.
    """
    try:
        return Path(transcript_path).is_file()
    except (TypeError, OSError):
        return False


def _register_commands(record: dict, commands: dict) -> None:
    """Records tool_use id -> command so a later result can be traced back.

    A tool_result links to its call through message.content[].tool_use_id.
    `sourceToolUseID` is null on those records and cannot be used.
    """
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        command = (block.get("input") or {}).get("command")
        if isinstance(command, str):
            commands[block.get("id")] = command


def _is_test_run(record: dict) -> bool:
    result = record.get("toolUseResult")
    if not isinstance(result, dict):
        return False
    stdout = result.get("stdout")
    if not isinstance(stdout, str):
        return False
    return parse_summary_line(stdout) is not None


def _run_was_scoped(record: dict, commands: dict) -> bool:
    """True unless the command is positively recognised as whole-suite.

    Uses `is_scoped`'s default bias, where an unrecognised runner counts as
    scoped. That is the correct direction here: this decides whether evidence
    may back a whole-suite claim, and treating an unknown command as
    whole-suite would let a narrow run confirm a broad claim.
    """
    message = record.get("message")
    command = ""
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                command = commands.get(block.get("tool_use_id"), "")
                break
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True
    if not tokens:
        return True
    return evidence.is_scoped(tokens)


def _classify(saw_any_run, saw_whole_suite, whole_suite_at, claim_at, ttl_s):
    """The cause this claim is unbacked, or None if it is backed."""
    if not saw_any_run:
        return NO_RUN
    if not saw_whole_suite:
        return NARROWED_ONLY
    if whole_suite_at is None or claim_at is None:
        # No usable clock. Staleness is unknown, and unknown is not evidence
        # the claim is backed.
        return STALE
    if claim_at - whole_suite_at > ttl_s:
        return STALE
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_session_stats.py -v`
Expected: PASS, 21 tests total in the file.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. No existing test changes meaning.

- [ ] **Step 6: Commit**

```bash
git add src/claim_check/session_stats.py tests/test_session_stats.py
git commit -m "Add summarize_session, replaying a transcript to classify claims"
```

---

### Task 6: The Stop hook speaks on unverified claims

**Files:**
- Modify: `src/claim_check/entrypoints/claude_stop_hook.py:89-103`
- Test: `tests/test_claude_stop_hook.py`

**Interfaces:**
- Consumes: `session_stats.summarize_session`, `SessionStats.render`.

Behaviour change: the branch that currently returns silently unless `CLAIM_CHECK_VERBOSE` is set now always emits a `systemMessage`. `CLAIM_CHECK_VERBOSE` is removed, since this is its only use.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_claude_stop_hook.py`:

```python
import json


def test_unverified_claim_is_reported_with_cause_and_ratio(tmp_path, capsys):
    from claim_check.entrypoints.claude_stop_hook import main

    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant", "timestamp": "2026-08-11T05:00:00.000Z",
        "message": {"content": [{"type": "text", "text": "All tests pass."}]},
    }) + "\n", encoding="utf-8")

    payload = json.dumps({
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "session_id": "s1",
    })
    assert main(stdin_text=payload) == 0
    out = json.loads(capsys.readouterr().out)["systemMessage"]
    assert "could not verify" in out.lower()
    assert "no test run" in out.lower()
    assert "1 of 1 claims" in out


def test_unverified_claim_still_reports_when_stats_are_unavailable(tmp_path, capsys, monkeypatch):
    from claim_check.entrypoints import claude_stop_hook

    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant", "timestamp": "2026-08-11T05:00:00.000Z",
        "message": {"content": [{"type": "text", "text": "All tests pass."}]},
    }) + "\n", encoding="utf-8")

    monkeypatch.setattr(claude_stop_hook, "summarize_session", lambda *a, **k: None)

    payload = json.dumps({
        "hook_event_name": "Stop", "transcript_path": str(transcript),
        "cwd": str(tmp_path), "session_id": "s1",
    })
    assert claude_stop_hook.main(stdin_text=payload) == 0
    out = json.loads(capsys.readouterr().out)["systemMessage"]
    assert "could not verify" in out.lower()
    assert "this session:" not in out


def test_a_turn_with_no_claim_still_produces_no_output_at_all(tmp_path, capsys):
    # The non-negotiable rule. Holds even though this session's ratio would
    # be terrible, because the claim gate runs before anything else.
    from claim_check.entrypoints.claude_stop_hook import main

    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in [
        {"type": "assistant", "timestamp": "2026-08-11T05:00:00.000Z",
         "message": {"content": [{"type": "text", "text": "All tests pass."}]}},
        {"type": "assistant", "timestamp": "2026-08-11T05:01:00.000Z",
         "message": {"content": [{"type": "text", "text": "Renamed a variable."}]}},
    ]) + "\n", encoding="utf-8")

    payload = json.dumps({
        "hook_event_name": "Stop", "transcript_path": str(transcript),
        "cwd": str(tmp_path), "session_id": "s1",
    })
    assert main(stdin_text=payload) == 0
    assert capsys.readouterr().out == ""


def test_claim_check_verbose_is_gone(tmp_path):
    import inspect
    from claim_check.entrypoints import claude_stop_hook
    assert "CLAIM_CHECK_VERBOSE" not in inspect.getsource(claude_stop_hook)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_claude_stop_hook.py -k "unverified or no_claim or verbose" -v`
Expected: FAIL. The first asserts output where there is none today.

- [ ] **Step 3: Implement**

In `src/claim_check/entrypoints/claude_stop_hook.py`, remove the `import os` line (it becomes unused) and add:

```python
from ..session_stats import summarize_session
```

Replace the block at lines 89-103, currently:

```python
    if record is None or record.get("scoped") is True:
        if os.environ.get("CLAIM_CHECK_VERBOSE"):
            print(_note(...))
        return 0
```

with:

```python
    if record is None or record.get("scoped") is True:
        # No usable evidence. Reported, never as a finding of falsehood: the
        # claim may be perfectly true and simply unchecked. The cause leads,
        # because the cause is the part the reader can act on.
        print(_note(
            f'claim-check: could not verify "{claim.raw_text}". '
            f"{_cause_sentence(record)}{_ratio_suffix(payload)}"
        ))
        return 0
```

Add the two helpers above `_run`:

```python
def _cause_sentence(record) -> str:
    if record is None:
        return "No whole-suite test run was recorded in this session against the current state of the tree."
    return "The only test run recorded in this session was narrowed, so it covered less than the whole suite."


def _ratio_suffix(payload: dict) -> str:
    """The session ratio in parentheses, or "" if it cannot be computed.

    The ratio is a garnish and the warning is the meal: a failure to compute
    statistics must never suppress the warning itself.
    """
    stats = summarize_session(payload.get("transcript_path"))
    if stats is None:
        return ""
    rendered = stats.render()
    return f" {rendered}" if rendered else ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_claude_stop_hook.py -v`
Expected: PASS. Existing tests in this file must be unchanged.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/claim_check/entrypoints/claude_stop_hook.py tests/test_claude_stop_hook.py
git commit -m "Report unverified claims at end of turn instead of staying silent"
```

---

### Task 7: The commit path carries the ratio

**Skip this task entirely if Task 1 found that `transcript_path` is absent from PreToolUse payloads.** Record the skip in the plan file and move to Task 8.

**Files:**
- Modify: `src/claim_check/entrypoints/claude_hook.py:110-114`
- Test: `tests/test_claude_hook_entrypoint.py`

**Interfaces:**
- Consumes: `session_stats.summarize_session`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_claude_hook_entrypoint.py`:

```python
import json


def test_abstention_warning_carries_the_session_ratio(tmp_path, capsys):
    from claim_check.entrypoints.claude_hook import main

    (tmp_path / "test_x.py").write_text(
        "def test_a():\n    assert True\n\ndef test_b():\n    assert True\n",
        encoding="utf-8",
    )
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant", "timestamp": "2026-08-11T05:00:00.000Z",
        "message": {"content": [{"type": "text", "text": "All 99 tests pass."}]},
    }) + "\n", encoding="utf-8")

    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -m "All 99 tests pass" -- test_x.py'},
        "cwd": str(tmp_path),
        "transcript_path": str(transcript),
        "session_id": "s1",
    })
    assert main(stdin_text=payload) == 0
    err = capsys.readouterr().err
    assert "could not verify" in err.lower()
    assert "this session:" in err


def test_missing_transcript_path_still_warns_without_a_ratio(tmp_path, capsys):
    from claim_check.entrypoints.claude_hook import main

    (tmp_path / "test_x.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -m "All 99 tests pass" -- test_x.py'},
        "cwd": str(tmp_path),
        "session_id": "s1",
    })
    assert main(stdin_text=payload) == 0
    err = capsys.readouterr().err
    assert "could not verify" in err.lower()
    assert "this session:" not in err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_claude_hook_entrypoint.py -k "session_ratio or without_a_ratio" -v`
Expected: FAIL on the first, because no ratio is printed today.

- [ ] **Step 3: Implement**

In `src/claim_check/entrypoints/claude_hook.py`, add the import:

```python
from ..session_stats import summarize_session
```

Replace the `unverifiable` branch at lines 110-114 with:

```python
    if verdict.status == "unverifiable":
        # The commit hook was invoked to gate, so "I did not gate" is
        # information the user needs. stderr, because stdout is parsed
        # as the hook's JSON response.
        stats = summarize_session(payload.get("transcript_path"))
        suffix = ""
        if stats is not None and stats.render():
            suffix = f" {stats.render()}"
        print(
            f"claim-check: WARNING - {verdict.message}; allowing commit{suffix}",
            file=sys.stderr,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_claude_hook_entrypoint.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/claim_check/entrypoints/claude_hook.py tests/test_claude_hook_entrypoint.py
git commit -m "Carry the session ratio on the commit path warning"
```

---

### Task 8: Retire the parallel implementation in the measurement script

Two steps, deliberately separated. The sidechain semantic change lands first with its own baseline, then the mechanical refactor must produce a zero diff. Combining them would make any difference unattributable.

**Files:**
- Modify: `scripts/measure_evidence_coverage.py`

- [ ] **Step 1: Capture the current baseline**

```bash
python scripts/measure_evidence_coverage.py > /tmp/baseline-before.txt
cat /tmp/baseline-before.txt
```

Record the numbers in the commit message later. Do not write them into any test: the corpus is this machine's live session history and it grows while you work.

- [ ] **Step 2: Add sidechain filtering to the existing script, unchanged otherwise**

In `scripts/measure_evidence_coverage.py`, inside the `for record in records:` loop, immediately after `if record.get("type") != "assistant": continue`, add:

```python
            if record.get("isSidechain") is True:
                # A subagent talking is not the main agent making a claim.
                # `transcript.last_assistant_text` has always skipped these;
                # this script did not, which is a real discrepancy rather
                # than a stylistic one.
                continue
```

- [ ] **Step 3: Measure the effect of that change alone**

```bash
python scripts/measure_evidence_coverage.py > /tmp/baseline-sidechain.txt
diff /tmp/baseline-before.txt /tmp/baseline-sidechain.txt
```

Expected: either no difference, or a reduction in claim counts. Any increase means the filter is inverted. Record the diff.

- [ ] **Step 4: Commit the semantic change on its own**

```bash
git add scripts/measure_evidence_coverage.py
git commit -m "Exclude subagent turns from the coverage measurement

The shipped transcript reader has always skipped isSidechain records, on
the grounds that a subagent talking is not the main agent making a claim.
This script never did, so the two disagreed about what counts as a claim.

Isolated in its own commit so the refactor that follows can require a zero
diff, rather than having a semantic change hidden inside it."
```

- [ ] **Step 5: Rewrite the script to call the shipped code**

Replace the private helpers `strip_quoted_output`, `load`, `command_index`, `command_for`, `assistant_text` and `timestamp_of` with imports. The script keeps its own reporting, but classification now comes from `session_stats`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claim_check import session_stats  # noqa: E402
from claim_check.session_stats import NARROWED_ONLY, NO_RUN, STALE  # noqa: E402

TTL_S = 900.0


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else Path.home() / ".claude" / "projects"
    if not root.is_dir():
        print(f"no transcripts directory at {root}")
        return 1

    sessions = backed = 0
    causes = {NO_RUN: 0, NARROWED_ONLY: 0, STALE: 0}

    for transcript in sorted(root.rglob("*.jsonl")):
        if "subagents" in transcript.parts:
            continue
        stats = session_stats.summarize_session(transcript, ttl_s=TTL_S, budget_s=None)
        if stats is None:
            continue
        sessions += 1
        backed += stats.backed
        for key, count in stats.causes.items():
            causes[key] = causes.get(key, 0) + count

    claims = backed + sum(causes.values())
    print("=" * 68)
    print("Would an end-of-turn claim check have anything to check against?")
    print("=" * 68)
    print(f"  sessions scanned          : {sessions}")
    print(f"  turns stating a claim     : {claims}")
    print()
    print(f"  backed by a whole-suite run within the {TTL_S/60:.0f}min TTL "
          f": {backed}/{claims}")
    print(f"     no test run at all     : {causes[NO_RUN]}")
    print(f"     only narrowed runs     : {causes[NARROWED_ONLY]}")
    print(f"     run too old to use     : {causes[STALE]}")
    print()
    print("  The real check also requires the tree to be unchanged since")
    print("  that run, which a transcript cannot show, so true coverage is")
    print("  at or below the backed figure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

Note the output format legitimately changes here, because the script now reports the cause split rather than the old evidence-tier split. The invariant to check in Step 6 is the claim total and the backed total, not the layout.

- [ ] **Step 6: Verify the refactor preserved the numbers**

```bash
python scripts/measure_evidence_coverage.py > /tmp/baseline-after.txt
cat /tmp/baseline-after.txt
```

Compare against `/tmp/baseline-sidechain.txt` by hand. Required: `turns stating a claim` is identical, and `backed` equals the old `...within the 15min TTL` figure. If either differs, the extraction changed behaviour and must be fixed before committing.

Both runs must happen back to back in the same sitting. The corpus grows while you work, so a comparison against numbers captured an hour ago proves nothing.

- [ ] **Step 7: Commit**

```bash
git add scripts/measure_evidence_coverage.py
git commit -m "Make the coverage script a caller of the shipped classifier

It carried private copies of strip_quoted_output, load and command_index.
strip_quoted_output has since been implemented properly in conversation.py,
so the script and the shipped code were two implementations of one idea
that could drift apart with nothing to notice.

Claim and backed totals verified identical against the previous commit,
run back to back on the same corpus."
```

---

### Task 9: Documentation, end to end scenario, and release

**Files:**
- Modify: `PLUGIN.md`, `README.md`, `scripts/smoke_e2e.sh`
- Modify: `.claude-plugin/plugin.json`, `pyproject.toml`, `src/claim_check/__init__.py`, `examples/pre-commit-config-snippet.yaml`

- [ ] **Step 1: Remove the CLAIM_CHECK_VERBOSE documentation**

In `PLUGIN.md`, delete the line at 187 beginning "Set `CLAIM_CHECK_VERBOSE=1`". Replace the surrounding paragraph with:

```markdown
When a claim cannot be checked, the end-of-turn hook now says so rather than
staying quiet, and names the reason: no test run happened, the only runs were
narrowed, or the last whole-suite run was too old to use. It still never
blocks and never reports a claim as false on the strength of missing evidence.
```

- [ ] **Step 2: Document the behaviour in the README**

In `README.md`, in the section describing the Claude Code plugin, after the paragraph beginning "As a Claude Code plugin it also checks claims made in conversation", add:

```markdown
When it cannot check a claim it says so instead of going quiet, because a tool
that is silent when it verified and silent when it had nothing to check is
indistinguishable from one that is broken. The note names the cause and how
the session is going so far:

```
claim-check: could not verify "all tests pass". No whole-suite test run was
recorded in this session against the current state of the tree. (this session:
2 of 6 claims had a whole-suite run behind them; 3 had no test run at all)
```

It says "had a whole-suite run behind them" rather than "verified" on purpose.
That count is reconstructed from the session transcript, which cannot see
whether the source tree changed after the run, so it is a weaker statement
than the one the commit gate makes.
```

- [ ] **Step 3: Add the end to end scenario**

In `scripts/smoke_e2e.sh`, add a scenario following the existing style: build a transcript containing one assistant turn claiming "All tests pass" and no test run, feed a Stop payload to the stop hook entry point, and assert the output contains both `could not verify` and `this session:`. Assert the exit code is 0.

- [ ] **Step 4: Run everything**

```bash
python -m pytest -q
bash scripts/smoke_e2e.sh /tmp/e2e-060
claim-check --help; claim-check-precommit --help; claim-check-claude-hook --help
```

Expected: all tests pass, `ALL E2E SCENARIOS PASS`, and all three console scripts exit 0.

- [ ] **Step 5: Check the no-dash rule across the repo**

```bash
python -c "
import subprocess, pathlib
files = subprocess.run(['git','ls-files'], capture_output=True, text=True).stdout.split()
bad = [f for f in files if any(c in pathlib.Path(f).read_text(encoding='utf-8', errors='replace') for c in ('—','–'))]
print('dashes:', bad or 'none')"
```

Expected: `dashes: none`

- [ ] **Step 6: Bump the version to 0.6.0**

Change `0.5.0` to `0.6.0` in `.claude-plugin/plugin.json`, `pyproject.toml`, `src/claim_check/__init__.py`. Change `rev: v0.5.0` to `rev: v0.6.0` in `examples/pre-commit-config-snippet.yaml`, and both `v0.5.0` references in `README.md`.

- [ ] **Step 7: Verify the claim in the commit message with the tool itself**

```bash
python -m pytest -q | tail -1
PYTHONPATH=src python -m claim_check.cli verify-tests "<the exact count from the line above>"
```

Expected: `claim-check: OK`

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "Document unverified-claim reporting and release 0.6.0"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: the outcome table and wording rules to Tasks 4 and 6; recompute-not-store to Tasks 3 and 5; the deadline replacing the size cap to Task 3; the `PreToolUse` open question to Task 1 gating Task 7; removing the parallel implementation to Task 8; `CLAIM_CHECK_VERBOSE` removal to Tasks 6 and 9; the non-negotiable no-claim rule to its own test in Task 6; the before/after oracle to Task 8.

**Constraint carried into the tests.** No test asserts a distribution of causes, only that a given session shape produces a given cause. The TTL change in later work can shift the mix without breaking anything here.

**Known gap, stated rather than hidden.** The spec's risk section notes the corpus is one developer's sessions and the cause split may not generalise. Nothing in this plan fixes that; it is a limit on the numbers, not on the code.
