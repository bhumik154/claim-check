# M0: measured facts about the Claude Code hook environment

Date: 2026-07-29. Platform: Windows 11, Claude Code, Python 3.12.10.

The official plugin docs contradict themselves on at least one field name and
omit several things entirely. Everything below was **measured**, not read from
documentation. Anything still unmeasured is marked as such and handled
defensively in code.

## 1. Cross-platform launching is a solved problem — use a polyglot file

The `superpowers` plugin ships a single `hooks/run-hook.cmd` that is
simultaneously a valid Bash script and a valid Windows batch file:

```
: << 'CMDBLOCK'
@echo off
... batch body ...
CMDBLOCK

# Unix continues here
exec bash "${SCRIPT_DIR}/${SCRIPT_NAME}" "$@"
```

In Bash, `:` is a no-op and `<< 'CMDBLOCK'` swallows the batch body as a
heredoc. In `cmd.exe`, the batch body runs and `exit /b` returns before the
Unix tail is reached.

**Consequence: one hook entry, not two.** The plan originally called for
shipping `run-hook.cmd` *and* `run-hook.sh` and adding an `O_CREAT|O_EXCL`
single-fire lock in case Claude Code executed both in parallel on Windows.
A polyglot file makes that entire risk and its mitigation unnecessary.

**Critical naming constraint**, from that file's own comment:

> Hook scripts use extensionless filenames (e.g. "session-start" not
> "session-start.sh") so Claude Code's Windows auto-detection — which prepends
> "bash" to any command containing .sh — doesn't interfere.

So the launcher must **not** have a `.sh` extension, and any script it
delegates to should be extensionless.

## 2. Interpreter startup is cheap — the Stop hook is viable

Measured on the target machine, 5 runs:

| Measurement | Time |
|---|---|
| `python -c "pass"` | 75–79 ms |
| `python -c "import claim_check.claims"` (via `PYTHONPATH=src`) | 122 ms |

The design review flagged 150–800 ms as a plausible Windows floor and warned
that a per-turn Stop hook might be a product-level non-starter. At ~120 ms for
a full parse-capable import, it is not. **The Stop hook is affordable.**

This still argues for keeping the cheap path's import graph small — do not
import `subprocess`, `shlex`, or `argparse` on the no-claim path.

## 3. Transcript format

Location: `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`.
Subagent transcripts live under `<session-id>/subagents/agent-*.jsonl`.

Observed sizes in this project: 2.1 MB for a working session, 14.7 MB for a
long one. **Tail-reading is mandatory**; `read_text()` on a 15 MB file on every
turn end is not acceptable.

One JSON object per line. Record `type` values observed: `assistant`, `user`,
`system`, `attachment`, `queue-operation`, `last-prompt`, `custom-title`,
`mode`.

An assistant text message:

```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": [{"type": "text", "text": "..."}],
    "model": "...", "usage": {...}, "stop_reason": null
  },
  "uuid": "...", "parentUuid": "...", "sessionId": "...",
  "cwd": "...", "gitBranch": "...", "timestamp": "...",
  "isSidechain": false
}
```

`message.content` is a list of blocks; block `type` is `text` or `tool_use`.
Only `text` blocks carry prose. **Selecting the last assistant message means
scanning backwards for `type == "assistant"` and taking its last `text` block,
skipping records whose content holds only `tool_use`.**

`isSidechain` was `false` on all 427 main-transcript records and `true` on
none — subagent output goes to separate files. The Stop hook reading the main
transcript will not mistake a subagent's message for the main agent's.

## 4. Bash tool result shape

From `toolUseResult` records in a real transcript (30 + 17 occurrences of the
two variants):

```
stdout       str
stderr       str
interrupted  bool
isImage      bool
noOutputExpected  bool   (present on some, absent on others)
```

**There is no `exit_code`.** A pytest run's success or failure must be derived
from the summary line, not from a return code — which is what
`pytest_parser.parse_summary_line` already does.

In this one session, **19 Bash results had `"passed"` in stdout**, so real
pytest evidence is plentiful in ordinary use. That is an encouraging early
signal for M3/M4 viability, though the real M3 measurement is how often
*fresh, same-session, unscoped* evidence exists at end of turn.

## 5. Stop-hook telemetry exists and is observable

Claude Code writes a `system` record after Stop hooks run:

```json
{"type": "system", "subtype": "stop_hook_summary", "hookCount": 2,
 "hookErrors": [], "preventedContinuation": false, "stopReason": "",
 "hasOutput": false, "level": "suggestion",
 "hookInfos": [{"command": "callback"}, {"command": "callback"}]}
```

`preventedContinuation` and `stopReason` are how a blocking Stop hook surfaces.
This record is a useful debugging channel: after installing the plugin, grep
the transcript for `stop_hook_summary` to confirm the hook actually ran, which
addresses the "hooks load only at session start, so it's silently inert"
failure mode.

## 6. Still unmeasured — handled defensively

These require a live hook invocation, which requires installing the plugin and
restarting Claude Code. Rather than a throwaway capture plugin, the hooks ship
with a permanent debug affordance: setting `CLAIM_CHECK_DEBUG_DUMP=<dir>`
writes each raw payload to that directory.

| Unknown | Defensive handling |
|---|---|
| PostToolUse result field: `tool_response` (shipping code) vs `tool_result` (official sample generator) | Read both; a test asserts they produce identical evidence. Shape is known to be the dict in §4. |
| Whether `stop_hook_active` is present on Stop payloads | Treat a missing value as `False`, and add an independent per-session block budget so recursion safety never depends on a single guard we do not control. |
| Whether stderr from a Stop hook exiting 0 surfaces anywhere the user sees | Emit user-facing text via `systemMessage` in stdout JSON, not stderr. |

Capture the real payloads with `CLAIM_CHECK_DEBUG_DUMP` after the first
install, and commit them under `tests/fixtures/hook_payloads/`.
