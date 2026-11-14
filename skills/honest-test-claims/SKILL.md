---
name: honest-test-claims
description: This skill should be used when about to state how many tests pass or fail - writing a commit message containing a count like "22 passed" or "15/17 passing", saying "all tests pass" or "the suite is green" or "tests are passing" in a summary, reporting a test result, or claiming work is verified. Use it before writing the number, not after.
---

# Honest test claims

## The rule

**Never state a test count you did not read from actual output in this session.**

If you are about to write "22 passed", "15/17 passing", "all tests pass", or
"the suite is green", the number must come from a test run whose output you
actually saw. Not from memory of an earlier run, not from the number of tests
you believe you wrote, not from a count that was true before your last edit.

If you have not seen the output, you have two honest options:

1. Run the suite, read the summary line, and state that number.
2. Say what you actually know: "I added 3 tests; I have not run the suite."

Both are fine. Inventing the number is not.

## Why this is a real failure mode, not a hypothetical

Test counts drift constantly during a session. A fix lands, a test splits, a
parametrize gains a case, and a number that was true ten minutes ago is now
wrong. Restating it from memory feels like reporting and is actually guessing.

The specific trap: a count you stated earlier in the session is *evidence to
you* that the count is right, because you remember asserting it confidently.
It is not evidence. It is your own earlier claim.

## What counts as having seen the output

The pytest summary line, verbatim, from a run in this session:

```
=========== 1 failed, 21 passed in 0.09s ===========
```

That line is authoritative. Do not re-derive a total by counting individual
`PASSED` lines, and do not adjust a previous run's number in your head to
account for edits you made since, rerun instead.

## Scope matters as much as the number

`pytest -k something` and `pytest tests/unit/` produce real, correct summary
lines for a *subset*. Quoting one of those as "all tests pass" is wrong even
though the number is accurate, because the claim is about a scope the run did
not cover.

State the scope when it is not the whole suite: "22 passed in `tests/unit`"
rather than "22 passed".

## Write the count as plain text

State a result in plain prose: `443 passed.` or `All 443 tests pass.`, not
inside quotation marks or backticks. Text in quotes or a code span reads as an
example being discussed ("a claim like "22 passed""), and claim-check does not
check it. A count you want verified has to look like a statement.

The reverse holds too: when you are explaining or quoting a claim rather than
making one, put it in quotes so it is not read as yours.

## Name the command you actually ran

The same goes for a claim that a command passed: "`make check` passes",
"`npm run lint` is clean". Name the command exactly as you ran it, in
backticks, and only after reading how that run ended. A claim about
`make check` is checked against your last `make check`, not against a
different command that happens to cover the same ground.

## What this skill does not ask

It does not ask you to run the test suite before finishing every task. If you
are not making a claim about test results, there is nothing here to satisfy.
Say nothing about counts and this skill is irrelevant.

The rule is about not asserting numbers you have not observed. It is not a
requirement to generate numbers.
