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
