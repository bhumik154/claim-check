"""Extracts a git commit message from a raw shell command string, the shape
Claude Code's PreToolUse hook receives (tool_input.command), as opposed to
the pre-commit commit-msg hook's much simpler file-path contract.

Fails open (returns None, never raises) on anything ambiguous: a parse
failure here is not evidence a claim is wrong, it's just evidence we
couldn't check.
"""

import bisect
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
# The rest of the opening line, `<<'MSG' && git log -1`, is ordinary shell
# and is put back after the placeholder. Requiring the newline straight after
# the delimiter left those heredocs unrecognised, so an apostrophe in the body
# made the whole command unparseable.
_HEREDOC_BARE_RE = re.compile(
    r"<<-?\s*(?P<quote>['\"]?)(?P<delim>\w+)(?P=quote)(?P<rest>[^\n]*)\n(?P<body>.*?)\n(?P=delim)\b",
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

    def _stash(quote: str, body: str, rest: str) -> str:
        if not quote and _UNRESOLVED_EXPANSION_RE.search(body):
            state["unresolved"] = True
        state["counter"] += 1
        key = f"\x00HEREDOC{state['counter']}\x00"
        placeholders[key] = body
        return key + rest

    # Wrapped form first: a bare heredoc pattern is a strict substring of
    # the wrapped one, so matching wrapped first prevents it from also
    # matching (incorrectly) as a bare heredoc missing its substitution.
    rewritten = _HEREDOC_WRAPPED_RE.sub(lambda m: _stash(m.group("quote"), m.group("body"), ""), command)
    rewritten = _sub_bare_heredocs(rewritten, _stash)
    return rewritten, placeholders, state["unresolved"]


_HEREDOC_OPENER_RE = re.compile(r"<<-?\s*(?P<quote>['\"]?)(?P<delim>\w+)(?P=quote)")
_LINE_START_WORD_RE = re.compile(r"\n(\w+)")


def _sub_bare_heredocs(text: str, stash) -> str:
    """The same rewrite as `_HEREDOC_BARE_RE.sub`, in linear time.

    The regex searched forward through the rest of the command for a
    terminator line once per `<<`, so a command with thousands of them and no
    terminator took seconds (5.5 s measured, against 41 ms in 0.6.0), on a
    path that runs for every Bash command. Here every line's first word is
    indexed once, and each opener looks its terminator up directly.

    The regex's backtracking is reproduced, since commit-message extraction
    depends on the exact result: an unquoted delimiter is shortened, longest
    first, until a terminator exists (`<<EOFX` with a line `EOF` closes at
    `EOF`, keeping `X` as the rest of the opening line). A quoted one is
    taken whole. tests/test_heredoc_stash.py checks this against the regex.
    """
    terminators = {}
    for found in _LINE_START_WORD_RE.finditer(text):
        terminators.setdefault(found.group(1), []).append(found.start())

    pieces, kept_from, search_from = [], 0, 0
    while True:
        opener = _HEREDOC_OPENER_RE.search(text, search_from)
        if opener is None:
            break
        newline = text.find("\n", opener.end())
        match = None
        if newline != -1:
            quote, word = opener.group("quote"), opener.group("delim")
            lengths = [len(word)] if quote else range(len(word), 0, -1)
            for length in lengths:
                delim = word[:length]
                positions = terminators.get(delim, [])
                i = bisect.bisect_right(positions, newline)
                if i < len(positions):
                    rest_start = opener.end() - (len(word) - length)
                    match = (quote, delim, positions[i], rest_start)
                    break
        if match is None:
            search_from = opener.start() + 1
            continue
        quote, delim, terminator, rest_start = match
        pieces.append(text[kept_from:opener.start()])
        pieces.append(stash(quote, text[newline + 1:terminator], text[rest_start:newline]))
        kept_from = search_from = terminator + 1 + len(delim)
    pieces.append(text[kept_from:])
    return "".join(pieces)


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


_SHELL_PUNCTUATION = "();<>|&\n"


def shell_tokens(command) -> list:
    """Tokens of a shell line, with operators split off even when glued on.

    `shlex.split` keeps "-q;" as one token, so in `pytest -q; echo exit=$?`
    the echo read as extra pytest arguments and the run was marked scoped. A
    punctuation-aware lexer splits that correctly, with three repairs:

    - Redirects are reassembled into one token, file descriptor and target
      included ("2>/dev/null", "2>&1", ">log.txt"). Split apart, the pieces
      read as test paths. (Checked: `"" in "<>"` is True in Python, hence the
      bool() guard below, or every bare number swallows the next token.)
    - A line break is a command separator, as in the shell, so `cd x` and
      `pytest` on separate lines are two commands, reported as ";". A line
      continuation, backslash then newline, is joined first: splitting there
      would detach `-q tests/unit` from `pytest` and make a narrowed run look
      like the whole suite.
    - Heredocs are set aside first: an apostrophe in a heredoc body is not a
      quoting error, but the lexer reads it as one.

    - Comments are removed the way bash removes them. shlex's own comment
      reader also swallowed the newline after a comment, merging the next
      line back into the commented one, and started a comment at a `#` in
      the middle of a word ("fix#6"), which bash does not.

    Returns [] for anything unparseable, which `is_scoped` reads as scoped.
    """
    lexed = _lex(command)
    return lexed[0] if lexed is not None else []


_COMMENT_MAY_FOLLOW = set(" \t\r\n;&|()")


def _strip_comments(command: str) -> str:
    """Removes unquoted comments, keeping the newline that ends each one.

    As in bash, `#` starts a comment only at the start of a word, and never
    inside quotes or after a backslash. One pass, linear in the length.
    """
    out = []
    quote = None
    word_start = True
    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if quote == "'":
            out.append(c)
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\":
            out.append(command[i : i + 2])
            word_start = False
            i += 2
            continue
        if quote == '"':
            out.append(c)
            if c == '"':
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            out.append(c)
            word_start = False
            i += 1
            continue
        if c == "#" and word_start:
            end = command.find("\n", i)
            i = n if end == -1 else end
            continue
        out.append(c)
        word_start = c in _COMMENT_MAY_FOLLOW
        i += 1
    return "".join(out)


def _lex(command):
    """The tokenizer shared by `shell_tokens` and `extract_commit_message`.

    Returns (tokens, heredoc bodies, any unresolved expansion), or None when
    the command cannot be parsed. Both parsers once lexed separately and
    disagreed: the commit-message one read a newline as plain whitespace, so
    `git add -A` and `git commit -m ...` on separate lines were one command
    starting with `git add`, and the claim was never checked (issue #6).
    """
    if not isinstance(command, str):
        return None
    command, bodies, unresolved = _stash_heredocs(command)
    # Comments before continuations: in bash a backslash inside a comment is
    # part of the comment, and the newline still ends it.
    command = _strip_comments(command)
    command = command.replace("\\\r\n", " ").replace("\\\n", " ")
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=_SHELL_PUNCTUATION)
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        raw = list(lexer)
    except ValueError:
        return None
    tokens = []
    awaiting_target = False
    for token in raw:
        if "\n" in token and set(token) <= set(_SHELL_PUNCTUATION):
            # A newline on its own separates commands; after an operator
            # ("&&\n", "|\n") the operator already does.
            tokens.append(token.replace("\n", "") or ";")
            awaiting_target = False
            continue
        if awaiting_target:
            tokens[-1] += token
            awaiting_target = False
            continue
        if tokens and tokens[-1].isdigit() and token[:1] in "<>":
            tokens[-1] += token
        else:
            tokens.append(token)
        operator = tokens[-1].lstrip("0123456789")
        awaiting_target = bool(operator) and (operator[0] in "<>" or operator.startswith("&>"))
    return tokens, bodies, unresolved


def leading_cd_target(command: str) -> Optional[str]:
    """The directory a command moves into before doing anything else.

    The Bash tool reports the directory it was in, but `cd /repo && python
    -m pytest` runs the suite somewhere else. Filing that run under the
    reported directory put evidence where nothing later looked for it, and
    made the commit hook run the suite in the parent folder.

    Only a plain `cd <dir>` as the very first command counts. A cd later in
    the line ran after other commands, a bare `cd` goes to the home
    directory, and anything unparseable returns None.
    """
    if not isinstance(command, str) or not command.strip():
        return None
    segments = _split_into_segments(shell_tokens(command))
    if not segments:
        return None
    first = segments[0]
    if len(first) == 2 and first[0] == "cd" and not first[1].startswith("-"):
        return first[1]
    return None


def extract_commit_message(command: str) -> Optional[str]:
    lexed = _lex(command)
    if lexed is None:
        # Unbalanced quotes or similar - can't confidently tokenize.
        return None
    tokens, heredocs, unresolved = lexed

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
