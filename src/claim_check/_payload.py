"""Type-guarded readers for Claude Code hook payloads.

Every field here is guarded rather than trusted. Claude Code sends
well-formed payloads today, but an unhandled AttributeError or TypeError in
a hook is a nonzero exit, and for a PreToolUse hook that means blocking a
tool call because of an internal defect.

Shared by all three hook entry points so the guards cannot drift apart.
"""

import os
import re
from pathlib import Path
from typing import Optional

from .shell_parser import leading_cd_target


def resolve_cwd(payload: dict) -> str:
    """The project directory the hook should work against.

    `payload.get("cwd", ".")` is not enough: the key can be present and
    explicitly null, in which case the default never applies and the literal
    string "None" reaches subprocess as a directory name.
    """
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        try:
            if Path(cwd).is_dir():
                return cwd
        except OSError:
            # A locked share or an ACL-mismatched mount can make even the
            # is_dir() probe raise; treat it as unusable.
            return "."
    return "."


# Git Bash writes Windows drives as "/c/Users/...". Windows Python reads that
# as a path on the CURRENT drive, "C:\c\Users\...", which does not exist, so a
# hook resolving it naively falls back and files evidence in the wrong place.
_GIT_BASH_DRIVE_RE = re.compile(r"^/([A-Za-z])(?=/|$)")


def _native_path(path: str) -> str:
    if os.name == "nt":
        path = _GIT_BASH_DRIVE_RE.sub(lambda m: m.group(1).upper() + ":", path)
    return os.path.expanduser(path)


def effective_dir(payload: dict, command) -> str:
    """The directory a Bash command actually ran in.

    The payload's cwd is where the Bash tool was, but `cd /repo && pytest`
    runs the suite in /repo. Measured across real sessions, 12 of the 13
    claims that looked for evidence in the wrong place came from commands of
    that shape, and the commit hook ran the suite across every project in the
    parent folder, hit collection errors, and denied a true claim.

    Falls back to the payload directory whenever the cd target cannot be
    resolved to a directory that exists.
    """
    base = resolve_cwd(payload)
    target = leading_cd_target(command)
    if target is None:
        return base
    path = Path(_native_path(target))
    if not path.is_absolute():
        path = Path(base) / path
    try:
        if path.is_dir():
            return str(path.resolve())
    except OSError:
        pass
    return base


def bash_command(payload: dict) -> Optional[str]:
    """The shell command string of a Bash tool call, or None."""
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    return command if isinstance(command, str) else None


def tool_stdout(payload: dict) -> Optional[str]:
    """Captured stdout of a completed tool call, or None.

    Reads both `tool_response` and `tool_result`. The official plugin
    documentation and its own sample generator disagree about which name a
    PostToolUse payload uses, and the two spellings also disagree about the
    type - one a dict, one a bare string. Measured against a real transcript,
    a Bash result is a dict of {stdout, stderr, interrupted, isImage} with no
    exit code, so success has to be read from the output itself rather than a
    return code. Both spellings are accepted so this keeps working whichever
    one a given Claude Code version emits.
    """
    for key in ("tool_response", "tool_result"):
        value = payload.get(key)
        if isinstance(value, dict):
            stdout = value.get("stdout")
            if isinstance(stdout, str):
                return stdout
        elif isinstance(value, str):
            return value
    return None


def session_id(payload: dict) -> str:
    value = payload.get("session_id")
    return value if isinstance(value, str) and value else "unknown"
