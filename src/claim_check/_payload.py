"""Type-guarded readers for Claude Code hook payloads.

Every field here is guarded rather than trusted. Claude Code sends
well-formed payloads today, but an unhandled AttributeError or TypeError in
a hook is a nonzero exit, and for a PreToolUse hook that means blocking a
tool call because of an internal defect.

Shared by all three hook entry points so the guards cannot drift apart.
"""

from pathlib import Path
from typing import Optional


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
