# Make unverified claims visible

## Context

claim-check is loudest when it can prove something wrong and silent the rest of
the time. Silence covers two very different situations, and from outside they
look identical:

- the tool checked a claim and it held up
- the tool had nothing to check against and said nothing

The second case is the more common one, and until now it was invisible. That is
the gap this closes.

### What the corpus says

Measured against the 42 Claude Code sessions on this machine, replaying what
the Stop hook would have done on every turn that stated a test-count claim.
These are a snapshot taken on 2026-08-11, not stable figures: the corpus is
this machine's own session history and it grows as work continues.

```
turns stating a claim   : 117      (1.9% of all assistant turns)
would verify            :  56
would abstain, silently :  61
   no test run at all   :  31
   only narrowed runs   :   4
   unscoped but stale   :  26
```

Two findings drove the design.

**Narrowing is a rounding error.** The scope model shipped in v0.5.0 accounts
for 4 of 61 silences. The real causes are that no test run happened at all (31)
and that a whole-suite run existed but had aged past the 15 minute TTL (26).
The feature worth building is therefore not an abstention counter. It is
surfacing unverified claims, with lack of evidence as the headline case.

**Speaking is cheap.** Because the Stop hook only examines turns that state a
claim, and those are 1.9% of turns, speaking on every unverified claim costs
1.5 lines per session on average. 24 of 42 sessions would see nothing at all.
The noise objection does not survive contact with the data.

Claims per session are bimodal: 22 of 42 sessions make none, but among sessions
that make any, the median is 4 and the tail runs to 23. So a ratio has a real
denominator in exactly the sessions where it appears.

## What changes

The trigger rule is untouched at the top. **No claim, no output, ever.** That
is the one non-negotiable rule in this project and nothing here relaxes it.

Within turns that do state a claim:

| outcome | today | after |
|---|---|---|
| mismatch | reports | reports, unchanged |
| backed by a whole-suite run | silent | silent |
| no test run at all in the session | silent unless `CLAIM_CHECK_VERBOSE` | speaks |
| runs exist but all were narrowed | silent unless `CLAIM_CHECK_VERBOSE` | speaks |
| unscoped run exists but is stale | silent | speaks |

The line leads with the cause, because the cause is the actionable part, and
carries the session ratio in parentheses:

```
claim-check: could not verify "all tests pass" - no test run was recorded
in this session. (this session: 2 of 6 claims had a whole-suite run behind
them; 3 had no run at all)
```

Both the commit path and the Stop path carry it. The commit path already
prints a warning when it abstains, so it gains only the parenthetical. The
Stop path gains the three new speaking cases, using the `systemMessage`
channel it already has.

**The Stop path reports and never blocks.** It does not send the agent back to
run tests. That promise is already published in the plugin description and this
work keeps it.

### Wording is load bearing

The ratio never uses the word "verified".

The statistics are reconstructed from the transcript, not read from a log of
what the hook actually decided, because nothing is logged. The live check also
requires the source fingerprint to be unchanged since the run, and a transcript
cannot show that. So a reconstruction can count a turn as backed that the live
hook would have treated as stale.

Rather than hedge a number, the number is defined as something the transcript
can actually prove: **"had a whole-suite run behind them"** means a whole-suite
run for that claim existed in the session. That is exactly what is measured, so
the sentence is true as written. Saying "verified" would be an overclaim, and a
tool whose entire purpose is catching overclaims does not get to make one in
its own output.

## Design

### Approach: recompute, store nothing

The session statistics are recomputed by replaying the transcript. No counter
file, no accumulated state, nothing on disk.

Two alternatives were considered and rejected:

- **Counters inside the evidence record.** Disqualified by a lifetime
  mismatch. That record is TTL'd and fingerprint invalidated, so it is
  discarded whenever the tree changes. The tally would silently reset on every
  source edit. A ratio that resets on every file save is worse than no ratio,
  because it still looks authoritative.
- **A sibling tally file.** Workable, but it is real persistent state with its
  own corruption, concurrency and cleanup story, on a path whose safety
  argument rests on not trusting stored state. Two hooks can write it in the
  same turn.

Recomputation cannot produce a wrong number, because there is no number to go
stale. The failure mode of a corrupt counter is a confidently displayed lie,
which is the failure this project exists to prevent.

Measured cost, on the transcripts currently on disk:

```
largest   20.8MB   parse 75ms + scan 10ms = 85ms
typical    0.1MB   parse 14ms             = 14ms
```

against a hook that already costs roughly 300ms to spawn and has a 10 second
budget.

### Components

**New module `src/claim_check/session_stats.py`.**

```python
def summarize_session(transcript_path, now, ttl_s) -> Optional[SessionStats]

@dataclass(frozen=True)
class SessionStats:
    backed: int                  # claims with a whole-suite run behind them
    unbacked: int
    causes: Mapping[str, int]    # "no_run" | "narrowed_only" | "stale"
    def render(self) -> str
```

It reuses what already exists rather than reimplementing it:
`conversation.extract_claims` (which already strips fenced blocks, indented
blocks and summary shaped lines), `pytest_parser.parse_summary_line`, and
`evidence.is_scoped`.

**`transcript.py` gains `iter_records(path)`**, a streaming whole file record
iterator, beside the existing `last_assistant_text` which reads only the last
256KB. Two readers with clearly different jobs, one module.

### Data flow

The order of operations preserves the existing fast path exactly:

```
payload -> 256KB tail -> extract_claims -> no claim? exit      (~98% of turns)
        -> claim -> load_fresh -> classify this turn
        -> backed? stay silent                                 (no full read)
        -> unbacked -> summarize_session(full replay) -> render -> systemMessage
```

The full read happens only on turns that both state a claim and are unbacked,
measured at 1.5 times per session. It is not a per turn cost.

The commit path is the same shape, appending to the warning it already prints.
It needs `transcript_path` in the `PreToolUse` payload. That must be confirmed
with `CLAIM_CHECK_DEBUG_DUMP` before being relied on. If it is absent, the
cause sentence still prints and only the parenthetical is dropped.

### Removing a parallel implementation

`scripts/measure_evidence_coverage.py` currently carries its own private copies
of `strip_quoted_output`, `load` and `command_index`. `strip_quoted_output` has
since been implemented properly in `conversation.py`, so the script and the
shipped code are two implementations of the same idea that can drift apart
without anything noticing.

The script gets rewritten to call `session_stats`. After this, the number the
script reports and the number the plugin shows come from the same code.

### `CLAIM_CHECK_VERBOSE` goes away

It appears in exactly one place, the branch this work changes. Once that case
speaks unconditionally the variable gates nothing, so it is removed along with
its mention in `PLUGIN.md`. A documented environment variable that controls
nothing is a lie in the docs.

## Error handling

**The ratio is a garnish, the warning is the meal.**

`summarize_session` returns `None` on every failure: missing file, unreadable
path, malformed JSONL, truncated final line, a payload with no
`transcript_path`. The caller then prints the cause sentence alone.

A failure to compute the ratio must never suppress the warning, never change an
exit code, and never surface as an error. This is the existing fail open
discipline applied one level down: uncertainty about the statistics is not
evidence about the claim.

**Size cap at 64MB.** Extrapolating the measured rate that is roughly 260ms.
Past the cap `summarize_session` returns `None` rather than a partial count,
because a truncated replay would silently under count and produce a
confidently wrong ratio, which is the exact failure mode this approach was
chosen to avoid.

## Testing

**Unit, against synthetic transcripts.** Each classification in isolation: no
runs at all, narrowed runs only, unscoped but stale, properly backed, and a
mixed session. Plus the TTL boundary on both sides, since that is the only
numeric edge.

**Failure injection.** Missing file, path pointing at a directory, malformed
JSON lines interleaved with good ones, truncated last line, oversized file.
Each asserts `None`, and at entry point level asserts the warning still
printed.

**Entry point behaviour.** An unbacked claim prints cause and ratio. A failed
summarize prints the cause alone. A backed claim stays silent. A mismatch
reports exactly as it does today.

**The non-negotiable gets its own test.** A turn with no claim produces no
output on any path, including when the session ratio would be terrible. That
rule is the one thing this feature could plausibly erode, so it is pinned
directly rather than left to the existing suite.

### Acceptance criteria beyond unit tests

1. All existing tests still pass. There are 370 at the time of writing; the
   real requirement is that none of them change meaning, since unlike the
   scope model work this feature is additive and should break nothing.
2. `bash scripts/smoke_e2e.sh <dir>` gains a scenario and still prints
   `ALL E2E SCENARIOS PASS`.
3. The rewritten `measure_evidence_coverage.py`, now calling shipped code
   instead of its own copy, produces identical output to the old one. This is
   an external oracle rather than a self consistent one, which is what makes it
   worth having.

   **Compare it correctly.** The corpus is live: it is this machine's own
   Claude Code sessions, and it grows while the work is being done. The
   session count was observed changing from 43 to 42 within minutes during
   this design. So the check is not "does it still print 237 runs". It is:
   stash the change, run the old script, capture the output, restore, run the
   new script, and diff. Both runs must happen back to back against the same
   corpus, and the numbers must not be written into the test.

## Deliberately not built

- **Blocking the agent to force a test run.** Closest to the original
  self correcting vision, but it contradicts the published "reports and never
  blocks" promise, and a claim that cannot be verified twice could loop.
- **A durable per project abstention rate.** Needs exactly the persistent state
  this design avoids, and the per session number is the actionable one.
- **A threshold that stays quiet until things look bad.** A silent tool that
  has stopped working is indistinguishable from one that has nothing to say,
  which is the problem being fixed, not a solution to it.
- **Retuning the 15 minute TTL.** 26 of 61 silences are stale evidence, which
  is real signal that the TTL may be mistuned, but that is a change to how long
  evidence stays usable and belongs in its own piece of work with its own
  measurement.

## Risks

- **This makes the tool talk more, and talking more is how tools get
  uninstalled.** The measured rate is 1.5 lines per session with 24 of 42
  sessions silent, which is defensible, but the tail is real: the worst session
  in the corpus would see 14 lines. If that lands badly the mitigation is a
  cap per session, not a threshold, since a cap degrades gracefully.
- **The corpus is one developer's sessions on a handful of projects.** The
  1.9% claim rate and the 31/4/26 cause split may not generalise. The numbers
  are honest about what they measured and should be re measured before being
  treated as universal.
- **Reconstruction and the live check can disagree.** Handled by defining the
  reported quantity as something the transcript can prove, but the gap is real
  and the wording is the only thing keeping it honest. Any future edit that
  reintroduces the word "verified" into the ratio silently turns this into an
  overclaim.
