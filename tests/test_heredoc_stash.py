"""Heredoc stashing: fast, and identical to the regex it replaces.

Smoke-testing 0.7.0 before release found the heredoc pattern quadratic: for every `<<` it
searched forward through the rest of the command for a terminator line. A
command with many `<<` and no terminator took 5.5 s, up from 41 ms in 0.6.0,
and this runs on every Bash command the commit hook sees.

The replacement must not change behaviour, because commit-message extraction
depends on it. So the old regex is kept here as the reference, and the new
code is compared against it on thousands of generated commands.
"""

import random
import re
import time

import pytest

from claim_check import shell_parser
from claim_check.shell_parser import _stash_heredocs

_REFERENCE_BARE = re.compile(
    r"<<-?\s*(?P<quote>['\"]?)(?P<delim>\w+)(?P=quote)(?P<rest>[^\n]*)\n(?P<body>.*?)\n(?P=delim)\b",
    re.DOTALL,
)


def _reference(command):
    placeholders = {}
    state = {"unresolved": False, "counter": 0}

    def _replace(m):
        body = m.group("body")
        if not m.group("quote") and shell_parser._UNRESOLVED_EXPANSION_RE.search(body):
            state["unresolved"] = True
        state["counter"] += 1
        key = f"\x00HEREDOC{state['counter']}\x00"
        placeholders[key] = body
        return key + (m.groupdict().get("rest") or "")

    rewritten = shell_parser._HEREDOC_WRAPPED_RE.sub(_replace, command)
    rewritten = _REFERENCE_BARE.sub(_replace, rewritten)
    return rewritten, placeholders, state["unresolved"]


_FRAGMENTS = [
    "<<", "<<-", "<<<", "<<EOF", "<<'EOF'", '<<"EOF"', "<<-EOF", "<< EOF", "<<MSG", "<<'MSG'",
    "EOF", "MSG", "EOFX", "EOF)", "\tEOF", " EOF", "\n", "\n\n", "\nEOF", "\nEOF\n", "\nMSG\n",
    "x", "it's", "$VAR", "$(date)", "`id`", "&&", ";", " ", "git commit -F- ", "cat ",
    "$(cat <<'EOF'\n", "\nEOF\n)", ")", '"', "'", "\\", "\r\n",
]


def _generated(count, seed=20261028):
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(_FRAGMENTS) for _ in range(rng.randrange(1, 40)))


def test_matches_the_reference_on_generated_commands():
    mismatches = [c for c in _generated(5000) if _stash_heredocs(c) != _reference(c)]
    assert mismatches == [], f"{len(mismatches)} differ, first: {mismatches[0]!r}"


@pytest.mark.parametrize("command", [
    "git commit -F- <<'MSG'\nit's done\nMSG",
    "git commit -F- <<'MSG' && git log -1\nbody\nMSG\necho after",
    'git commit -m "$(cat <<\'EOF\'\nwrapped\nEOF\n)"',
    "cat <<EOF\n$HOME\nEOF",
    "a <<A\nx\nA\nb <<B\ny\nB",
    "cat <<EOF\n\nEOF",
    "cat <<EOF\nEOF",
    "cat <<EOF\nnever closed",
])
def test_matches_the_reference_on_known_shapes(command):
    assert _stash_heredocs(command) == _reference(command)


_SLOW_BEFORE = {
    # Short ids on purpose: pytest exports the test id as an environment
    # variable, and these commands exceed Windows' 32767-character limit.
    "one-line-openers": lambda: "echo " + "<<x " * 20000 + "\n" + "y\n" * 2000,
    "cpp-shifts": lambda: "echo " + "".join(f'std::cout << "x{i}" << std::endl;\n' for i in range(3000)),
}


@pytest.mark.parametrize("label", sorted(_SLOW_BEFORE))
def test_many_openers_without_a_terminator_stay_fast(label):
    command = _SLOW_BEFORE[label]()
    start = time.perf_counter()
    _stash_heredocs(command)
    assert time.perf_counter() - start < 0.5, label


@pytest.mark.parametrize("label", sorted(_SLOW_BEFORE))
def test_the_fast_version_still_matches_the_reference_on_large_input(label):
    command = _SLOW_BEFORE[label]()
    assert _stash_heredocs(command) == _reference(command)
