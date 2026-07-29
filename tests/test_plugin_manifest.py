"""Validates the Claude Code plugin packaging against the real files on disk.

These are the cheapest, highest-value tests in the plugin surface: every
failure mode they catch is one that produces NO error at authoring time and
only surfaces after a user installs the plugin and restarts Claude Code -
a rename that silently unhooks the plugin, a launcher path that no longer
exists, a hook timeout shorter than the pytest run it wraps.
"""

import json
import re
from pathlib import Path

import pytest

from claim_check.runner import DEFAULT_TIMEOUT_S

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_MANIFEST = REPO_ROOT / ".claude-plugin" / "marketplace.json"
HOOKS_MANIFEST = REPO_ROOT / "hooks" / "hooks.json"
SKILLS_DIR = REPO_ROOT / "skills"

_PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_PLUGIN_ROOT_TOKEN = "${CLAUDE_PLUGIN_ROOT}"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _hook_entries():
    """Yields (event_name, hook_dict) for every command hook declared."""
    manifest = _load(HOOKS_MANIFEST)
    for event, matchers in manifest["hooks"].items():
        for matcher in matchers:
            for hook in matcher["hooks"]:
                yield event, hook


def test_plugin_manifest_exists_and_declares_a_valid_name():
    manifest = _load(PLUGIN_MANIFEST)
    assert _PLUGIN_NAME_RE.match(manifest["name"]), manifest["name"]
    assert manifest["name"] == "claim-check"


def test_plugin_version_matches_the_package_version():
    # Two install paths exist - pip-installed console scripts and the
    # bundled plugin source - and they can drift on the same machine. The
    # version has to be stamped identically in both or a bug report can't
    # be tied to a revision.
    from claim_check import __version__

    assert _load(PLUGIN_MANIFEST)["version"] == __version__


def test_marketplace_manifest_lists_this_plugin():
    # Without a marketplace entry the plugin is not installable at all,
    # which defeats the entire point of packaging it.
    marketplace = _load(MARKETPLACE_MANIFEST)
    names = [entry["name"] for entry in marketplace["plugins"]]
    assert "claim-check" in names


def test_every_hook_command_references_the_plugin_root_variable():
    # A hardcoded or relative path works on the author's machine and breaks
    # on every install, because the plugin is installed into a versioned
    # cache directory whose path is not knowable at authoring time.
    for event, hook in _hook_entries():
        assert _PLUGIN_ROOT_TOKEN in hook["command"], f"{event}: {hook['command']}"


def test_every_hook_command_points_at_a_file_that_exists():
    for event, hook in _hook_entries():
        command = hook["command"]
        referenced = command.split(_PLUGIN_ROOT_TOKEN, 1)[1].split('"', 1)[0]
        target = REPO_ROOT / referenced.lstrip("/\\")
        assert target.is_file(), f"{event} references missing file: {target}"


def test_the_launcher_is_not_named_dot_sh():
    # Measured in M0: Claude Code's Windows auto-detection prepends "bash"
    # to any command containing ".sh", which would double-invoke or mangle
    # the launcher. The polyglot launcher is a .cmd that is also a valid
    # bash script, so it must not carry a .sh extension.
    for event, hook in _hook_entries():
        assert ".sh" not in hook["command"], f"{event}: {hook['command']}"


def test_hook_timeouts_exceed_the_pytest_timeout_they_wrap():
    # A hook killed at its own timeout produces no result at all. Any hook
    # that can run pytest must outlive DEFAULT_TIMEOUT_S, or the hook dies
    # before the run it is waiting on can possibly finish.
    for event, hook in _hook_entries():
        if event != "PreToolUse":
            continue
        assert hook["timeout"] > DEFAULT_TIMEOUT_S, f"{event}: {hook['timeout']}"


def test_the_pretooluse_hook_is_wired_to_the_bash_matcher():
    manifest = _load(HOOKS_MANIFEST)
    matchers = [m["matcher"] for m in manifest["hooks"]["PreToolUse"]]
    assert "Bash" in matchers


@pytest.mark.parametrize("skill_file", sorted(SKILLS_DIR.glob("*/SKILL.md")) or [None])
def test_every_skill_declares_name_and_description_frontmatter(skill_file):
    # A skill with malformed frontmatter is silently never triggered.
    if skill_file is None:
        pytest.skip("no skills shipped yet")
    text = skill_file.read_text(encoding="utf-8")
    assert text.startswith("---\n"), skill_file
    frontmatter = text.split("---\n", 2)[1]
    assert re.search(r"^name:\s*\S+", frontmatter, re.MULTILINE), skill_file
    assert re.search(r"^description:\s*\S+", frontmatter, re.MULTILINE), skill_file
