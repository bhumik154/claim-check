"""Reads claims that a named command passed: "`make check` passes".

The commonest verification claim an agent makes is not a test count. 64 of 98
PR descriptions in mattabate/life-distributable end with one ("`make check`
and `make webcheck` pass."), and about 100 of the author's 794 end-of-turn
messages carry one ("`tsc` clean, `eslint` clean").

A claim here is one or more code spans naming a command, joined by commas,
"and" or "&", followed within the sentence by a verdict: passes, succeeds,
clean, green, OK. Everything else is read as not a claim: a description of a
step ("runs `make check`"), a condition or intention ("make sure `make check`
passes", "once ... passes"), a negation, a question, a quoted example, and
any code span that does not start with a program (`TestDecider`, `git
status`, a file name). Missing a claim costs a check; inventing one costs the
user's trust.

The rules ask one question: is this sentence a report? The 0.9.0 smoke test
showed why. An agent writes about a command most right after it failed:
"When `make check` passes, the deploy runs", "Confirm `npm run typecheck`
passes", "Expected: `make check` passes", "`tsc`: 4 errors, eslint clean".
The first build read 39 of 46 such sentences as claims. So a claim must look
like the reports in the real corpus (Matt's 98 PRs, the author's sessions):

- the command opens its clause, after nothing, a bullet, "and", "both", or a
  results label ("Checks:", "Verified:", "All clean -");
- only filler sits between it and the verdict ("is", "are all", ":");
- "passes" takes no object ("passes `-race` to go test");
- nothing later in the sentence takes it back ("but fails on this branch",
  "before the refactor", "then stops on three failures").
"""

import re
from typing import List

from .models import CommandClaim
from .quoting import code_spans, merged, quoted_block_regions, quoted_regions

# Programs whose success is something an agent reports as verification. Not
# git, gh, ls or echo: "`git status` is clean" is about the working tree.
_PROGRAMS = frozenset({
    "make", "gmake", "npm", "pnpm", "yarn", "bun", "deno", "node", "tsc", "eslint",
    "prettier", "biome", "vitest", "jest", "mocha", "playwright", "go", "gofmt",
    "golangci-lint", "cargo", "ruff", "mypy", "pyright", "flake8", "black", "pytest",
    "tox", "nox", "python", "python3", "py", "uv", "poetry", "hatch", "pdm", "swift",
    "swiftlint", "xcodebuild", "dotnet", "mvn", "gradle", "gradlew", "flutter", "dart",
    "bash", "sh", "zsh", "cmake", "ctest", "ninja", "bazel", "shellcheck", "actionlint",
})
_PREFIXES = frozenset({"npx", "bunx", "time", "env"})
_TWO_WORD_PREFIXES = frozenset({("pnpm", "exec"), ("pnpm", "dlx"), ("yarn", "dlx")})
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Between one code span and the next in a subject: ", ", " and ", " & ",
# ", and ", and "/" as in "`tsc`/`eslint`/`vitest` all clean".
_JOINER = re.compile(r"\s*(?:,\s*(?:and\s+)?|and\s+|&\s*|/\s*)")

# Right after the verdict: a count of claims, not a claim ("passes x6").
_TALLY_AFTER = re.compile(r"\s*[×x]\s*\d")

# After the subject: a short gap in the same sentence, then the verdict.
# "OK" only straight after the command, "`make check` OK" or "`make check`:
# OK": after other words it belongs to something else ("labelled `npx tsc`
# had printed TYPECHECK OK").
# OK is tried first: in "`make check` OK, scrub clean" the verdict is the OK,
# not the "clean" that belongs to the scrub.
_VERDICT = re.compile(
    r"(?:[ \t]*:?[ \t]*OK\b"
    r"|(?P<gap>[^.\n`?!;]{0,30}?)\b(?i:pass(?:es|ed)?|succeed(?:s|ed)?|clean|green)\b)"
)

# Between the command and the verdict: filler only. "`make check` is green",
# "`tsc`/`eslint` are all clean", "`make app-test` (simulator): passed",
# "`make check`, `make webcheck` and the scrub pass". Anything else, a
# negation ("isn't"), a modal ("should") or other words ("`tsc`: 4 errors,
# eslint clean"), means the verdict is not this command's.
_GAP = re.compile(
    r"\s*(?:\([^()\n]{0,40}\)\s*)?(?:and\s+(?:the\s+)?\w+\s+)?"
    r"(?:(?:is|are|was|were|has|have|had|been|now|again|all|both|also|still|fully|each|just)\s+"
    r"|[:—–-]\s*)*",
    re.I,
)

# An imagined case, as a phrase: "In a perfect world, ...", "Works on paper",
# "Good in principle", "Perfect scenario", "in that case". Single words cost
# real claims ("including the offline scenario", "Paper trail updated").
_IMAGINED = (
    r"\b(?:(?:perfect|ideal|dream|better|best|worst|that|this|another|an?) (?:world|scenario|case)"
    r"|in (?:a|an|that|this|my|our|your) (?:\w+ )?(?:world|scenario|case)"
    r"|on paper|in principle|hypothetical\w*|imaginary|imagined|pretend)\b"
)

# A clause before the command that sets a condition, though it carries a
# results word: "On a clean checkout, ...", "On a good day, ...", "Done
# right, ...".
_CONDITION_CLAUSE = re.compile(
    r"^(?:on|in|at|with|under|for|by|from|without|during|given)\b"
    r"|^(?:done|set up|configured|run|used|built) (?:right|properly|correctly|well)$",
    re.I,
)

# Before the command, in the same sentence (code spans blanked out): the claim
# is a condition, an intention, a goal, a memory or somebody else's words.
_HYPOTHETICAL = re.compile(
    r"\b(?:should|will|would|must|until|unless|if|once|ensure|make sure|whether|so that|"
    r"could|might|needs? to|expect\w*|wait for|lets?|before|does|do|did|can|"
    r"earlier|previously|originally|formerly|used to|whenever|when|while|in theory|"
    r"supposedly|ideally|hopefully|hoping|said|says|claim\w*|reported|neither|nor|none|"
    r"wrong|false|goals?|criteria|next|todo|plan|steps?|verify|confirm|"
    r"assuming|provided|ideal|theoretically|per|according|yesterday|last|reference|"
    r"base|baseline|revert\w*|stash\w*|probably|maybe|perhaps|likely|apparently|"
    r"presumably|reportedly|allegedly|ci)\b|" + _IMAGINED,
    re.I,
)
_TO_BEFORE = re.compile(r"\bto\s*$", re.I)

# Every clause of the sentence before the command's own has to be part of a
# report too: another command ("`tsc` clean, `eslint` clean") or a results
# word ("TDD'd, ...", "Fixed, ..."). An introduction of any other kind
# places the claim somewhere else: "Assuming the fix works, ...", "Per the
# README, ...", "Last week, ...". There are too many of those to list.
_INTRO_OK = re.compile(
    r"#|\b(?:verified|verification|checks?|gate|clean|done|passed|passing|pass|ran|re-ran|"
    r"results?|status|confirmed|validated|ok|fixed|implemented|added|updated|landed|merged|"
    r"green|tdd'd|good|great|perfect|nice|works|working|fine|all set)\b",
    re.I,
)
_PARENTHETICAL = re.compile(r"\([^()\n]*\)")

# The next sentence takes the claim back: "`npm test` passes. Actually, it
# fails on the last package."
# Same paragraph only: "Not committed yet" in the next one is about something
# else.
_RETRACTED = re.compile(
    r"[ \t]*(?:(?:actually|wait|correction|except|but|however|though|well|not really|oops|"
    r"scratch that)\b|no\s*[,:]|[^.!?\n]{0,40}\b(?:it|that|this)\b[^.!?\n]{0,30}"
    r"(?:\bfail\w*|\bdoesn't|\bdoes not|\bdidn't|\bdid not|\bisn't|\bwasn't|\bbroke\b))",
    re.I,
)

# Where a clause starts. The command must open it.
_CLAUSE_BREAK = re.compile(r"[.!?\n;,:(—–]")
_DASHES = (":", "—", "–")
_MARKUP = re.compile(r"^(?:\s*(?:[-*+•]|\d+[.)])(?=\s))?[\s*_✅✓✔]*|[\s*_]*$")
_OPENERS = frozenset({"", "and", "both", "also", "now", "and now", "and also", "confirmed", "verified"})
# A label ending in ":" or a dash has to announce results ("Checks:", "All
# clean -"), not a goal ("Expected:") or a step ("Verify:").
_RESULTS_LABEL = re.compile(
    r"\b(?:verified|verification|checks?|gate|clean|done|passed|passing|pass|ran|re-ran|"
    r"results?|status|confirmed|validated|ok)\b",
    re.I,
)

# Right after the verdict: punctuation, the end, or a word that qualifies a
# report ("passes locally", "clean on every changed file"). Anything else is
# an object: "passes `-race` to go test", "passes formatting and vet".
_NEXT_WORD = re.compile(r"[ \t]*(?P<word>[^\s.,;:()!?*_—–]*)")
_QUALIFIERS = frozenset({
    "", "on", "in", "locally", "now", "again", "with", "for", "including", "after", "at",
    "across", "against", "cleanly", "fine", "too", "here", "everywhere", "wherever", "outside",
    "under", "and", "repo-wide",
})

# Later in the same sentence: the report is taken back, placed in the past or
# somewhere else, or the run failed after all. "zero errors" is a report.
_FAILED = r"\bfail\w*|\bbroke\b|\bbroken\b|(?<!\bzero )(?<!\bno )(?<!\b0 )\berrors?\b"
_TAKEN_BACK = re.compile(
    r"\b(?:but|though|although|however|yet|except|whereas|despite|unless|until|before|"
    r"previously|earlier|used to|in theory|on main|on master|upstream|ci|stops?|red|only|"
    r"base|baseline|parent|old|stash\w*|revert\w*|said|says|according|supposedly|"
    r"apparently|reportedly|allegedly|presumably|probably|maybe|perhaps|likely|"
    r"theoretically|should|if)\b|" + _IMAGINED + "|" + _FAILED,
    re.I,
)
_FAILED_RE = re.compile(_FAILED, re.I)
_CLAUSE_OR_SPAN = re.compile(r"[.!?\n;,:(—–#]")

_SENTENCE_END = re.compile(r"[.!?\n]")


def extract_command_claims(text) -> List[CommandClaim]:
    """Every claim in `text` that a named command passed, in order."""
    if not isinstance(text, str) or not text:
        return []
    spans = code_spans(text)
    if not spans:
        return []
    quoted = merged(quoted_regions(text, code_spans=False) + quoted_block_regions(text))

    blank = _blanked(text, spans)
    failed_here = _said_to_fail(text, blank, spans)

    claims = []
    i = 0
    while i < len(spans):
        group = [spans[i]]
        j = i + 1
        while j < len(spans) and _JOINER.fullmatch(text, group[-1][1], spans[j][0]):
            group.append(spans[j])
            j += 1
        i = j

        verdict = _VERDICT.match(text, group[-1][1])
        if verdict is None:
            continue
        # A mixed subject, "`a`, `b` and the scrub pass", keeps its commands.
        gap = verdict.group("gap") or ""
        if "|" in gap or not _GAP.fullmatch(gap):
            continue
        start, end = group[0][0], verdict.end()
        if _TALLY_AFTER.match(text, end):
            continue
        if _is_question(blank, end) or _is_hypothetical(blank, start):
            continue
        if not _opens_clause(blank, start) or _in_table(text, start):
            continue
        if _NEXT_WORD.match(text, end).group("word").lower() not in _QUALIFIERS:
            continue
        stop = _sentence_end(blank, end)
        if _TAKEN_BACK.search(blank, end, stop):
            continue
        if blank[stop:stop + 1] in (".", "!") and _RETRACTED.match(blank, stop + 1):
            continue
        if any(a < end and start < b for a, b in quoted):
            continue
        commands = tuple(
            body for body in (text[a:b].strip("`").strip() for a, b in group)
            if _is_command(body) and " ".join(body.split()) not in failed_here
        )
        if commands:
            claims.append(CommandClaim(commands=commands, raw_text=text[start:end], span=(start, end)))
    return claims


def _blanked(text: str, spans) -> str:
    """`text` with each code span replaced by "#", length kept, so the words
    and punctuation inside commands ("./...", "a, b") are not read as prose."""
    parts, last = [], 0
    for a, b in spans:
        parts.append(text[last:a])
        parts.append("#" * (b - a))
        last = b
    parts.append(text[last:])
    return "".join(parts)


def _opens_clause(blank: str, start: int) -> bool:
    """Whether the command starts its clause, as a report's subject does,
    and any label it follows announces results."""
    window = max(0, start - 200)
    last = None
    for last in _CLAUSE_BREAK.finditer(blank, window, start):
        pass
    if last is None:
        if window:
            return False
        clause_start, mark = 0, ""
    else:
        clause_start, mark = last.end(), last.group()
    lead = _MARKUP.sub("", blank[clause_start:start])
    if " ".join(lead.lower().split()) not in _OPENERS:
        return False
    earlier = _PARENTHETICAL.sub("", blank[_sentence_start(blank, start):clause_start])
    for piece in _CLAUSE_BREAK.split(earlier):
        piece = _MARKUP.sub("", piece).strip()
        if piece and (not _INTRO_OK.search(piece) or _CONDITION_CLAUSE.search(piece)):
            return False
    if mark not in _DASHES:
        return True
    label_start = 0
    for found in _CLAUSE_BREAK.finditer(blank, max(0, clause_start - 200), clause_start - 1):
        if found.group() not in _DASHES:
            label_start = found.end()
    return bool(_RESULTS_LABEL.search(blank, label_start, clause_start - 1))


def _in_table(text: str, start: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    return text[line_start:start].lstrip().startswith("|")


def _sentence_end(blank: str, position: int) -> int:
    found = _SENTENCE_END.search(blank, position)
    return found.start() if found else len(blank)


def _said_to_fail(text: str, blank: str, spans) -> set:
    """Commands the same message says failed, as in "`tsc` clean, but `tsc`
    fails in CI". No claim about one of them is a clean report."""
    failed = set()
    for a, b in spans:
        # Only this command's own clause: in "`tsc` clean, `vitest` 2 failed"
        # the failure is vitest's.
        found = _CLAUSE_OR_SPAN.search(blank, b)
        if _FAILED_RE.search(blank, b, found.start() if found else len(blank)):
            failed.add(" ".join(text[a:b].strip("`").split()))
    return failed


def _is_command(body: str) -> bool:
    words = body.split()
    while words and (_ENV_ASSIGNMENT.match(words[0]) or words[0] in _PREFIXES):
        words = words[1:]
    if len(words) >= 2 and (words[0], words[1]) in _TWO_WORD_PREFIXES:
        words = words[2:]
    if not words:
        return False
    program = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if program.endswith(".exe"):
        program = program[:-4]
    if program in _PROGRAMS:
        return True
    # A script run directly, "./ops/webcheck.sh".
    return words[0].startswith(("./", "../")) and program.endswith((".sh", ".bash"))


def _sentence_start(text: str, position: int) -> int:
    window_start = max(0, position - 200)
    last = None
    for last in _SENTENCE_END.finditer(text, window_start, position):
        pass
    return last.end() if last else window_start


def _is_hypothetical(text: str, start: int) -> bool:
    before = text[_sentence_start(text, start):start]
    return bool(_HYPOTHETICAL.search(before) or _TO_BEFORE.search(before))


def _is_question(text: str, end: int) -> bool:
    found = _SENTENCE_END.search(text, end)
    return bool(found) and found.group() == "?"
