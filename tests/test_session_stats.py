import json

from claim_check import session_stats as session_stats_module
from claim_check.session_stats import (
    NARROWED_ONLY, NO_RUN, STALE, SessionStats, ratio_suffix, summarize_session,
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
        _assistant("Confirmed, 22 passed."),                 # backed
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


def test_a_tool_use_with_a_non_dict_input_does_not_raise(tmp_path):
    # A tool_use block whose "input" serialises as a JSON string (or list)
    # rather than an object must not blow up `.get("command")` in
    # _register_commands. This is not Bash-specific: every tool_use block
    # is inspected, so one odd tool anywhere in the transcript is enough.
    record = {"type": "assistant", "timestamp": _T0, "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "SomeTool", "input": "not-a-dict"},
    ]}}
    p = _session(tmp_path, [record, _assistant("All tests pass.")])
    stats = summarize_session(p)
    assert stats == SessionStats(backed=0, unbacked=1, causes={NO_RUN: 1})


def test_ratio_suffix_renders_when_there_is_an_unbacked_claim(tmp_path):
    p = _session(tmp_path, [_assistant("All tests pass.")])
    assert ratio_suffix({"transcript_path": str(p)}) == (
        " (this session: 0 of 1 claims had a whole-suite run behind them; "
        "1 had no test run at all)"
    )


def test_ratio_suffix_is_suppressed_when_every_claim_this_session_was_backed(tmp_path):
    # The replay behind this ratio cannot see fingerprints, unlike the live
    # evidence check the warning it is attached to is based on. If the
    # replay says every claim, including this one, had a whole-suite run
    # behind it, printing that ratio next to "could not verify" would
    # contradict the very sentence it garnishes, so it must be suppressed.
    p = _session(tmp_path, [
        _bash("python -m pytest", "t1"),
        _result("===== 22 passed in 0.4s =====", "t1"),
        _assistant("All 22 tests pass."),
    ])
    assert ratio_suffix({"transcript_path": str(p)}) == ""


def test_ratio_suffix_is_empty_when_no_claims_were_seen(tmp_path):
    p = _session(tmp_path, [_assistant("Refactored the loop for clarity.")])
    assert ratio_suffix({"transcript_path": str(p)}) == ""


def test_ratio_suffix_survives_summarize_session_raising(monkeypatch):
    # The docstring's promise: a failure to compute statistics must never
    # suppress the warning this is appended to, even when the failure is an
    # exception rather than a None return.
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic summarize_session failure")

    monkeypatch.setattr(session_stats_module, "summarize_session", boom)
    assert ratio_suffix({"transcript_path": "irrelevant"}) == ""


def test_ratio_suffix_survives_render_raising(monkeypatch):
    # Same guarantee, but the exception comes from stats.render() instead of
    # from summarize_session() itself.
    class BoomStats:
        unbacked = 1

        def render(self):
            raise RuntimeError("synthetic render failure")

    monkeypatch.setattr(session_stats_module, "summarize_session", lambda *a, **k: BoomStats())
    assert ratio_suffix({"transcript_path": "irrelevant"}) == ""


def test_ratio_suffix_survives_summarize_session_returning_none(monkeypatch):
    monkeypatch.setattr(session_stats_module, "summarize_session", lambda *a, **k: None)
    assert ratio_suffix({"transcript_path": "irrelevant"}) == ""


def test_unreadable_existing_transcript_returns_none(tmp_path, monkeypatch):
    # A file that stats as present but cannot be opened (ACL denial, a
    # Windows byte-range lock held by a concurrent writer, ...) must be
    # reported as "no statistics", never as "zero claims".
    p = _session(tmp_path, [_assistant("All tests pass.")])

    import pathlib

    real_open = pathlib.Path.open

    def _blocked_open(self, *args, **kwargs):
        if self == p:
            raise PermissionError("simulated unreadable transcript")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", _blocked_open)
    assert summarize_session(p) is None


def test_a_quiet_run_followed_by_other_output_counts_as_a_run(tmp_path):
    # The same runs the evidence store used to drop: the session ratio read
    # them as "no test run at all" and reported the claim as unbacked.
    p = _session(tmp_path, [
        _bash("python -m pytest -q; echo exit=$?", "t1"),
        _result("....\n12 passed in 0.31s\nexit=0", "t1"),
        _assistant("All 12 tests pass."),
    ])
    stats = summarize_session(p)
    assert stats.backed == 1 and stats.unbacked == 0


def test_a_vitest_run_counts_as_a_run(tmp_path):
    p = _session(tmp_path, [
        _bash("npx vitest run", "t1"),
        _result(" Test Files  2 passed (2)\n      Tests  18 passed (18)\n", "t1"),
        _assistant("All 18 tests pass."),
    ])
    assert summarize_session(p).causes.get(NO_RUN, 0) == 0
