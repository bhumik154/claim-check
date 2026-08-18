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
