"""Extracts a git commit message from a raw shell command string, the shape
Claude Code's PreToolUse hook receives (tool_input.command), as opposed to
the pre-commit commit-msg hook's much simpler file-path contract.

Fails open (returns None, never raises) on anything ambiguous: a parse
failure here is not evidence a claim is wrong, it's just evidence we
couldn't check.
"""

import re
import shlex
from typing import Optional

# Heredoc opener: <<DELIM, <<'DELIM', <<"DELIM", or <<-DELIM (the "-" form
# strips leading tabs from the body, irrelevant to us). Group "quote"
# captures whether the delimiter was quoted, since that determines whether
# the shell would have performed variable/command expansion inside the body.
#
# Two forms are matched, tried in this order:
#  1. Wrapped in the "$(cat <<'EOF' ... EOF)" command-substitution idiom
#     this environment's own tool instructions mandate for multi-line
#     commit messages. The *entire* "$(cat ... )" construct is consumed and
#     resolves directly to the heredoc body - that's what bash's command
#     substitution actually evaluates it to (cat's output, minus the one
#     trailing newline $(...) strips), not the literal wrapper text.
#  2. A bare heredoc with no command-substitution wrapper.
_HEREDOC_WRAPPED_RE = re.compile(
    r"\$\(\s*cat\s+<<-?\s*(?P<quote>['\"]?)(?P<delim>\w+)(?P=quote)\s*\n"
    r"(?P<body>.*?)\n(?P=delim)\s*\n?\s*\)",
    re.DOTALL,
)
_HEREDOC_BARE_RE = re.compile(
    r"<<-?\s*(?P<quote>['\"]?)(?P<delim>\w+)(?P=quote)\s*\n(?P<body>.*?)\n(?P=delim)\b",
    re.DOTALL,
)
_UNRESOLVED_EXPANSION_RE = re.compile(r"\$\{|\$\(|\$[A-Za-z_]|`")

_SEGMENT_BOUNDARY_TOKENS = {"&&", "||", ";", "|", "&", "(", ")"}

_BUNDLED_M_RE = re.compile(r"^-[a-zA-Z]*m$")
_MESSAGE_ATTACHED_RE = re.compile(r"^-[a-zA-Z]*m(.+)$")


def _stash_heredocs(command: str):
    """Replaces each heredoc construct with a placeholder token holding a
    reference to its body, so shlex (which has no concept of heredocs)
    never has to see one. Returns (rewritten_command, {placeholder: body},
    any_unresolved) - any_unresolved is True if an *unquoted* heredoc
    delimiter's body contains $VAR/$(...)/`cmd` (a quoted delimiter, e.g.
    <<'EOF', disables expansion entirely, so its body is always literal
    regardless of what characters it contains).
    """
    placeholders = {}
    state = {"unresolved": False, "counter": 0}

    def _replace(m: re.Match) -> str:
        body = m.group("body")
        if not m.group("quote") and _UNRESOLVED_EXPANSION_RE.search(body):
            state["unresolved"] = True
        state["counter"] += 1
        key = f"\x00HEREDOC{state['counter']}\x00"
        placeholders[key] = body
        return key

    # Wrapped form first: a bare heredoc pattern is a strict substring of
    # the wrapped one, so matching wrapped first prevents it from also
    # matching (incorrectly) as a bare heredoc missing its substitution.
    rewritten = _HEREDOC_WRAPPED_RE.sub(_replace, command)
    rewritten = _HEREDOC_BARE_RE.sub(_replace, rewritten)
    return rewritten, placeholders, state["unresolved"]


def _split_into_segments(tokens: list) -> list:
    segments = [[]]
    for tok in tokens:
        if tok in _SEGMENT_BOUNDARY_TOKENS:
            segments.append([])
        else:
            segments[-1].append(tok)
    return [seg for seg in segments if seg]


# git's own global options, which may appear between "git" and the
# subcommand. Only the ones that take a separate value need the two-token
# skip; the "--flag=value" forms are a single token.
_GIT_GLOBAL_FLAGS_WITH_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--exec-path",
    "--namespace",
    "--config-env",
}
_GIT_GLOBAL_BOOLEAN_FLAGS = {
    "--no-pager",
    "--paginate",
    "--bare",
    "--literal-pathspecs",
    "--no-replace-objects",
    "--no-optional-locks",
    "-P",
}


def _find_git_commit_segment(segments: list):
    """Returns the token list starting at "commit", or None.

    Skips git's own global options first: requiring "commit" to be
    literally the second token meant "git -C /repo commit" - the ordinary
    way to drive git from another directory - went entirely unverified.
    A leading wrapper ("sudo git commit", "env FOO=1 git commit") is still
    unsupported and documented as such; this only relaxes git's own flags.
    """
    for segment in segments:
        if not segment or segment[0] != "git":
            continue

        i = 1
        while i < len(segment):
            token = segment[i]
            if token in _GIT_GLOBAL_FLAGS_WITH_VALUE:
                i += 2
                continue
            if token in _GIT_GLOBAL_BOOLEAN_FLAGS:
                i += 1
                continue
            if token.startswith("--") and "=" in token and token.split("=", 1)[0] in _GIT_GLOBAL_FLAGS_WITH_VALUE:
                i += 1
                continue
            break

        if i < len(segment) and segment[i] == "commit":
            return segment[i:]

    return None


def _iter_message_values(tokens: list):
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--message" and i + 1 < len(tokens):
            yield tokens[i + 1]
            i += 2
            continue
        if tok.startswith("--message="):
            yield tok[len("--message=") :]
            i += 1
            continue
        if _BUNDLED_M_RE.match(tok) and i + 1 < len(tokens):
            yield tokens[i + 1]
            i += 2
            continue
        attached = _MESSAGE_ATTACHED_RE.match(tok)
        if attached:
            yield attached.group(1)
            i += 1
            continue
        i += 1


def extract_commit_message(command: str) -> Optional[str]:
    stashed_command, heredocs, unresolved = _stash_heredocs(command)

    try:
        lexer = shlex.shlex(stashed_command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        # Unbalanced quotes or similar - can't confidently tokenize.
        return None

    segment = _find_git_commit_segment(_split_into_segments(tokens))
    if segment is None:
        return None

    values = list(_iter_message_values(segment))
    if not values:
        # No -m/--message at all: an editor would open. A clean no-op, not
        # an error - there is nothing to statically check.
        return None

    resolved = []
    for value in values:
        if any(key in value for key in heredocs):
            if unresolved:
                # This heredoc's body relies on shell expansion we can't
                # perform; extracting it verbatim could produce a message
                # that doesn't match what git will actually receive.
                return None
            # Substring substitution, not exact-token equality: a heredoc
            # with text around it ("-m \"prefix $(cat <<'EOF' ...)\"")
            # otherwise left the internal sentinel in the message.
            for key, body in heredocs.items():
                value = value.replace(key, body)
        resolved.append(value)

    return "\n\n".join(resolved)
