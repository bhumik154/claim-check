"""Reading claims that a named command passed: "`make check` passes".

The commonest verification claim an agent makes is not a test count. Measured:
64 of 98 PR descriptions in mattabate/life-distributable end with one, and
about 100 of Bhumik's 794 end-of-turn messages carry one. The shapes below are
taken from those texts.
"""

import pytest

from claim_check.command_claims import extract_command_claims


def commands(text):
    return [c.commands for c in extract_command_claims(text)]


@pytest.mark.parametrize("text, expected", [
    ("`make check` passes.", [("make check",)]),
    ("`make check` and `make webcheck` pass.", [("make check", "make webcheck")]),
    ("`make check` and `make webcheck` pass on the staged tree.", [("make check", "make webcheck")]),
    ("`make check` passes, including both app builds.", [("make check",)]),
    ("`make check`: OK.", [("make check",)]),
    ("`go test -timeout 180s ./...` all pass", [("go test -timeout 180s ./...",)]),
    ("`go test ./internal/server/ ./internal/config/` passes and the race run is clean.",
     [("go test ./internal/server/ ./internal/config/",)]),
    ("`gofmt` and `go vet` clean", [("gofmt", "go vet")]),
    ("`npm run typecheck` and `npm run lint` are both clean, and Metro starts.",
     [("npm run typecheck", "npm run lint")]),
    ("`tsc --noEmit` passes", [("tsc --noEmit",)]),
    ("`tsc` clean, `eslint` clean, 153/153 tests passing.", [("tsc",), ("eslint",)]),
    ("`npx tsc --noEmit` now passes", [("npx tsc --noEmit",)]),
    ("`bash scripts/smoke_e2e.sh` passes", [("bash scripts/smoke_e2e.sh",)]),
    ("`cargo test` succeeded", [("cargo test",)]),
], ids=["passes", "and-pass", "pass-on", "passes-including", "colon-ok", "all-pass", "passes-and",
        "clean", "both-clean", "tsc", "two-separate", "npx-now", "script", "succeeded"])
def test_real_claim_shapes_are_read(text, expected):
    assert commands(text) == expected


@pytest.mark.parametrize("text", [
    "Its `--test` runs in `make check`.",
    "the session resolves conflicts and runs `make check`.",
    "`make check` should pass once this lands.",
    "Run it until `make check` passes.",
    "Make sure `make check` passes before you merge.",
    "If `npm test` passes, ship it.",
    "Once `make check` passes I will open the PR.",
    "This lets `tsc` pass again.",
    "`make check` doesn't pass yet.",
    "`make check` no longer passes.",
    "`make check` still fails, but the docs build.",
    "`tsc` isn't clean.",
    "Does `make check` pass?",
    "`git status` is clean.",
    "`TestDecider` passes.",
    "the `check` target passes",
    "`app/main.swift` passes review.",
    'It reads claims like "`make check` passes" from the PR body.',
    "> `make check` passes\n\nThat line above is the one I mean.",
    "```\n`make check` passes\n```",
], ids=["describes-step", "runs", "should", "until", "make-sure", "if", "once", "lets-pass",
        "doesnt", "no-longer", "fails", "isnt", "question", "git-status", "test-name",
        "not-a-command", "a-file", "quoted-example", "blockquote", "fenced"])
def test_what_is_not_a_command_claim(text):
    assert commands(text) == []


def test_a_mixed_subject_keeps_only_the_commands():
    # From a real PR: "`make check`, `make webcheck` and the scrub pass".
    assert commands("`make check`, `make webcheck` and the scrub pass") == [("make check", "make webcheck")]


def test_the_raw_text_is_the_claim_as_written():
    [claim] = extract_command_claims("Done. `make check` and `make webcheck` pass.\n")
    assert claim.raw_text == "`make check` and `make webcheck` pass"


def test_non_text_and_empty_input_have_no_claims():
    assert extract_command_claims(None) == []
    assert extract_command_claims("") == []


def test_a_long_message_is_read_in_linear_time():
    import time
    text = "`make check` and " * 20_000 + "`x` passes"
    start = time.perf_counter()
    extract_command_claims(text)
    assert time.perf_counter() - start < 2.0


def test_commands_joined_by_slashes_are_each_claimed():
    # 15 real end-of-turn messages: "`tsc`/`eslint`/`vitest` all clean".
    assert commands("`tsc`/`eslint`/`vitest` all clean (159/159).") == [("tsc", "eslint", "vitest")]


def test_a_tally_of_claims_is_not_a_claim():
    # From a summary that counted claims: "`tsc` clean x27, `make check` passes x6".
    assert commands("(`tsc` clean ×27, CI green ×32, `make check` passes ×6)") == []


def test_ok_has_to_follow_the_command_directly():
    # From 0.9.0's own commit message: "a call labelled `npx tsc` had printed
    # TYPECHECK OK" is not a claim that tsc passed.
    assert commands("a call labelled `npx tsc` had printed TYPECHECK OK (vitest failed)") == []
    assert commands("Gate: `make check` OK, `make webcheck` OK.") == [("make check",), ("make webcheck",)]


def test_a_quoted_claim_in_brackets_is_an_example():
    assert commands('one ("`make check` and `make webcheck` pass"), and about') == []


# --- 0.9.0 smoke test: what an agent writes right after a run fails ----------
#
# Each of these was read as a claim by the first build, and 20 of them became
# false findings in a session where `tsc` and `make check` had really failed.
# That is the only moment a finding can fire, so it is where these sentences
# matter most.

HOSTILE = [
    "`npm test` passed on main but fails on this branch.",
    "Earlier, `npm test` passed; now it fails with 2 errors.",
    "`make check` passed before the refactor. Now it fails.",
    "When `make check` passes, the deploy job runs.",
    "After `npm test` passes, CI publishes the package.",
    "Whenever `tsc` is clean the hook exits 0.",
    "Next steps:\n1. Fix the type error.\n2. Confirm `npm run typecheck` passes.",
    "Verify `make check` passes before merging.",
    "I'll confirm `npm test` passes once the fix lands",
    "Expected: `make check` passes.",
    "Goal: `npm test` passes.",
    "Acceptance criteria: `make check` passes.",
    "Done when `make check` passes.",
    "Make `npm test` pass on Windows",
    "Get `tsc` clean, then commit.",
    "Fix the import so `make check` passes.",
    "`tsc`: 4 errors, eslint clean.",
    "`npm test`: 2 failures, rest green.",
    "`tsc` has 2 errors; the rest is clean",
    "`tsc` has 2 errors, the rest is clean.",
    "Neither `tsc` nor `eslint` passes.",
    "None of `tsc`, `eslint` pass yet.",
    "| Command | Before | After |\n|---|---|---|\n| `make check` | pass | fail |",
    "`make check` passes `-race` to `go test`.",
    "`npm test` passes the `--runInBand` flag through to jest.",
    "Previously `npm test` passed even though the build was broken.",
    "`npm test` passed only because the broken test was skipped.",
    "`make check` used to pass.",
    "`make check` was passing until commit abc123.",
    "Hoping `npm test` passes now.",
    "Trying again so `npm test` passes",
    "In theory `make check` passes; in practice it fails on Windows.",
    "Supposedly `tsc` is clean, but I saw 3 errors.",
    "`npm test` passes locally? Unclear. It failed twice.",
    "Ideally `make check` passes with no warnings.",
    "`make check` passing is the merge gate.",
    "The `make check` passes are slow.",
    "Unfortunately `npm test` is not clean.",
    "I haven't seen `npm test` pass yet.",
    "It's not true that `npm test` passes.",
    "Wrong: `npm test` passes. Right: it fails.",
    "> `npm test` passes\n\nThat quote from the PR is wrong.",
    "The PR says `npm test` passes, but it fails for me.",
    "You said `npm test` passes; it does not.",
    "Claude claimed `make check` passed. It hadn't.",
    "Reported as `tsc` clean in #12, which was false.",
    "Expected: `make check` OK, `npm test` OK.",
    "`tsc` clean, but `tsc` fails in CI with 3 errors.",
    # Real text. Matt's make check failed; the second is a plan step.
    "- `make check` passes formatting and vet, then stops on the three existing "
    "timezone failures documented in #68 (`TestDatedCarriesDecisionsAndScores`)",
    "Verify: `npx vitest run` fully green, no RN/Supabase imports",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_what_an_agent_writes_after_a_failure_is_not_a_claim(text):
    assert commands(text) == []


# Every genuine shape in Matt's PRs and Bhumik's sessions, with what comes
# before and after it. The rules above must keep these.
@pytest.mark.parametrize("text, expected", [
    ("- `make pylint` passes (13 files OK)", [("make pylint",)]),
    ("Verification: gofmt clean; `go build ./...` and `go vet ./...` pass; done.",
     [("go build ./...", "go vet ./...")]),
    ("Checks: `make check` and `make webcheck` green on the stage (Go tests, web tests).",
     [("make check", "make webcheck")]),
    ("**Verified**: `tsc --noEmit` clean, `eslint` clean on every touched/new file, full test suite 77/77.",
     [("tsc --noEmit",), ("eslint",)]),
    ("**Verification** — `tsc` clean, `vitest` 91/91 (added tests), `eslint` clean repo-wide.",
     [("tsc",), ("eslint",)]),
    ("I just re-ran full verification on today's date: **`tsc` clean, `vitest` 110/110, `eslint` clean**",
     [("tsc",), ("eslint",)]),
    ("Everything else is done and fully verified locally: `tsc` clean, `eslint` clean.",
     [("tsc",), ("eslint",)]),
    ("TDD'd (6 new tests), `tsc`/`eslint`/`vitest` all clean (159/159), no em dashes.",
     [("tsc", "eslint", "vitest")]),
    ("All clean — `tsc`/`eslint`/`vitest` pass (164/164, 5 new tests), no em dashes.",
     [("tsc", "eslint", "vitest")]),
    ("`tsc` and `eslint` are both clean, and I spot-checked rendering on mobile widths.",
     [("tsc", "eslint")]),
    ("`npm run typecheck` and `npm run lint` are both clean, and Metro compiled (1814 modules, zero errors).",
     [("npm run typecheck", "npm run lint")]),
    ("Done and fully verified (`tsc`/`eslint`/`vitest` all clean, 182/182 tests).",
     [("tsc", "eslint", "vitest")]),
    ("- `npm run build` — clean; new `MetricCard` component.", [("npm run build",)]),
    ("final full-app pass: `npm run build` clean, full permalink round-trip on all 3 tabs.",
     [("npm run build",)]),
    ("`expo-dev-client` is in, `tsc` still clean", [("tsc",)]),
    ("- `make app-test` (simulator): passed", [("make app-test",)]),
    ("`make check` and `make webcheck` pass, and `go test -race ./internal/sched` passes.",
     [("make check", "make webcheck"), ("go test -race ./internal/sched",)]),
    ("`make check` green; scrub clean", [("make check",)]),
    ("`make check` and `make webcheck` pass locally.", [("make check", "make webcheck")]),
    ("`make check` passes, wherever the developer lives.", [("make check",)]),
    ("Gate: `make check` OK, `make webcheck` OK, scrub clean.", [("make check",), ("make webcheck",)]),
    ("`tsc --noEmit` passes with zero errors.", [("tsc --noEmit",)]),
    ("Ran it again: `make check` passes now.", [("make check",)]),
    ("Both `tsc` and `eslint` are clean.", [("tsc", "eslint")]),
    ("Fixed. `npm run build` succeeds.", [("npm run build",)]),
    # Found by the live check: a sign-off before the comma is a report too.
    ("All good, `npm run check` passes.", [("npm run check",)]),
    ("Great, `tsc` is clean now.", [("tsc",)]),
])
def test_genuine_report_shapes_are_kept(text, expected):
    assert commands(text) == expected


def test_a_verification_list_is_read_line_by_line():
    # Matt's PR: the next bullet's "-" is not an object of "clean".
    text = ("### Verification\n- `gofmt` and `go vet` clean\n- `go test -timeout 180s ./...` all pass\n"
            "- `make pylint` passes (13 files OK)\n")
    assert commands(text) == [("gofmt", "go vet"), ("go test -timeout 180s ./...",), ("make pylint",)]


# --- round two, written after the rules above to slip past them ---------------

ROUND_TWO = [
    "Assuming the fix works, `make check` passes.",
    "Provided GOFLAGS is set, `npm test` passes.",
    "In an ideal world, `make check` passes.",
    "Theoretically, `tsc` is clean.",
    "Per the README, `make check` passes on a fresh clone.",
    "According to the PR, `npm test` passes.",
    "On my machine yesterday, `npm test` passed.",
    "Last week, `make check` was green.",
    "Results from CI: `npm test` passed.",
    "Verified: `make check` green, no wait, red.",
    "`npm test` passes. Actually, it fails on the last package.",
    "`make check` passed. Wait, no: one test failed.",
    "For reference, `make check` passes on the base commit.",
    "`make check` passes on the base commit, so the regression is mine.",
    "`npm test` passes with the old config.",
    "`npm test` passes for the unit tests only.",
    "Now `make check` passes, they said.",
    "`make check` passes, according to Matt.",
    "`make check` passes, supposedly.",
    "`tsc` is clean, apparently.",
    "Probably `make check` passes now.",
    "Maybe `npm test` passes after the rebase.",
    "Perhaps `tsc` is clean now.",
    "With the patch reverted, `make check` passes.",
    "After stashing my changes, `npm test` passes.",
    "Status: `make check` passes on main, fails here.",
    "Gate: `make check` passes (except the flaky TestX).",
    "Checks: `make check` passes if you skip the race tests.",
    "`npm test` passes 41/42.",
    "`make check` passes?! That can't be right.",
    "Likely `make check` passes once deps install.",
    "`npm test` passed in the stashed tree.",
]


@pytest.mark.parametrize("text", ROUND_TWO)
def test_round_two_hostile_texts_are_not_claims(text):
    assert commands(text) == []


def test_a_claim_is_not_taken_back_by_the_next_paragraph():
    # A real end-of-turn message: the next paragraph is about something else.
    text = ("- **Verification**: `tsc` clean, `eslint` clean, `vitest` 153/153, no em dashes.\n\n"
            "Not committed yet, this is a branch.")
    assert commands(text) == [("tsc",), ("eslint",)]


def test_a_list_subject_is_a_known_loss():
    # "Lint, TypeScript, and `npm run build` are all clean" reads like any
    # other introduction before a comma. Missing it is the accepted cost.
    assert commands("Lint, TypeScript, and `npm run build` are all clean.") == []


# --- round three, against the sign-off words ("All good, ...") ---------------

@pytest.mark.parametrize("text", [
    "In a perfect world, `make check` is green.",
    "In a good world, `npm test` passes.",
    "Good in principle, `tsc` is clean.",
    "Works on paper, `make check` passes.",
    "Perfect scenario, `tsc` is clean.",
    "Nice idea, `make check` passes in that world.",
    "Hypothetically speaking, `npm test` passes.",
    "`make check` passes in principle.",
    "`npm test` passes on paper.",
])
def test_an_idiom_of_an_imagined_case_is_not_a_claim(text):
    assert commands(text) == []


def test_a_sign_off_before_a_claim_still_reads():
    assert commands("Perfect, `tsc` is clean.") == [("tsc",)]
    assert commands("Great, `npm test` passes now.") == [("npm test",)]


# --- round four, from the 0.9.1 smoke test --------------------------------------

@pytest.mark.parametrize("text", [
    "On a good day, `npm test` passes.",
    "On a clean checkout, `make check` passes.",
    "Done right, `make check` passes.",
    "Done properly, `npm test` passes.",
    "In that case, `make check` passes.",
    "Fine, `make check` passes in that scenario.",
    "Works, `npm test` passes on my imaginary machine.",
])
def test_a_condition_dressed_as_a_sign_off_is_not_a_claim(text):
    assert commands(text) == []


@pytest.mark.parametrize("text, expected", [
    ("`make check` passes, including the offline scenario.", [("make check",)]),
    ("`npm test` passes in every scenario we cover.", [("npm test",)]),
    ("`tsc` is clean across the world map module.", [("tsc",)]),
    ("Paper trail updated, `make check` passes.", [("make check",)]),
    ("`make check` passes on the hello-world example too.", [("make check",)]),
    ("Fixed the principle violation, `npm run lint` is clean.", [("npm run lint",)]),
])
def test_the_imagined_case_words_only_count_as_phrases(text, expected):
    assert commands(text) == expected
