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
_VERDICT = re.compile(
    r"(?:(?P<gap>[^.\n`?!;]{0,30}?)\b(?i:pass(?:es|ed)?|succeed(?:s|ed)?|clean|green)\b"
    r"|[ \t]*:?[ \t]*OK\b)"
)

# In the gap: the verdict is denied, reversed, or only expected
# ("`make check` should pass").
_DENIED = re.compile(
    r"\b(?:not|never|no longer|fails?|failed|failing|broke|broken|without|"
    r"should|will|would|must|could|might|may|can|to)\b|n't",
    re.I,
)

# Before the subject, in the same sentence: the claim is a condition, an
# intention or an instruction, not a report.
_HYPOTHETICAL = re.compile(
    r"\b(?:should|will|would|must|until|unless|if|once|ensure|make sure|whether|so that|"
    r"could|might|needs? to|expect|wait for|lets?|before|does|do|did|can)\b",
    re.I,
)
_TO_BEFORE = re.compile(r"\bto\s*$", re.I)

_SENTENCE_END = re.compile(r"[.!?\n]")


def extract_command_claims(text) -> List[CommandClaim]:
    """Every claim in `text` that a named command passed, in order."""
    if not isinstance(text, str) or not text:
        return []
    spans = code_spans(text)
    if not spans:
        return []
    quoted = merged(quoted_regions(text, code_spans=False) + quoted_block_regions(text))

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
        if _DENIED.search(gap):
            continue
        start, end = group[0][0], verdict.end()
        if _TALLY_AFTER.match(text, end):
            continue
        if _is_question(text, end) or _is_hypothetical(text, start):
            continue
        if any(a < end and start < b for a, b in quoted):
            continue
        commands = tuple(
            body for body in (text[a:b].strip("`").strip() for a, b in group) if _is_command(body)
        )
        if commands:
            claims.append(CommandClaim(commands=commands, raw_text=text[start:end], span=(start, end)))
    return claims


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
