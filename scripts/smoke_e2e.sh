#!/usr/bin/env bash
# Reverifies the six end-to-end reproductions that motivated the hardening
# pass. Usage: scripts/smoke_e2e.sh <scratch-dir>
set -u
ROOT="${1:?usage: smoke_e2e.sh <scratch-dir>}"
rm -rf "$ROOT"; mkdir -p "$ROOT"; cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1

git init -q .; git config user.email s@t.local; git config user.name Smoke
printf '__pycache__/\n' > .gitignore
mkdir -p tests

# Prefer the installed console script, since that is what a real pre-commit
# user runs, but fall back to the module. The console-script shim is a small
# generated .exe on Windows, and an OS application-control policy can refuse
# to execute it - observed directly on the development machine, where it
# returned exit 126 and every hook invocation failed, which git reads as
# "block this commit". That made every scenario here report BLOCK, including
# the ones asserting a commit is allowed. Falling back keeps this script
# measuring claim-check's behaviour rather than the machine's exec policy.
#
# The Claude Code plugin is immune to this by construction: it invokes
# `python -m claim_check.entrypoints...` from bundled source and never goes
# through a console-script shim at all.
if claim-check-precommit --help >/dev/null 2>&1; then
  cat > .git/hooks/commit-msg <<'HOOK'
#!/usr/bin/env bash
exec claim-check-precommit "$1"
HOOK
else
  echo "note: console script unusable here; falling back to python -m" >&2
  # Quoted deliberately: the interpreter path routinely contains spaces on
  # Windows ("C:\Users\First Last\..."), and an unquoted expansion here makes
  # the hook exec a truncated path, fail, and read as "block every commit" -
  # which silently turns every ACCEPT scenario below into a false failure.
  PY="$(command -v python || command -v python3)"
  cat > .git/hooks/commit-msg <<HOOK
#!/usr/bin/env bash
exec "$PY" -m claim_check.entrypoints.precommit "\$1"
HOOK
fi
chmod +x .git/hooks/commit-msg
git add -A >/dev/null; git commit -q -m "chore: init" >/dev/null 2>&1

FAILURES=0
try() {  # try <label> <ACCEPT|BLOCK> <msg>
  local label="$1" expect="$2" msg="$3"
  out=$(git commit --allow-empty -m "$msg" 2>&1)
  if [ $? -eq 0 ]; then got="ACCEPT"; else got="BLOCK"; fi
  if [ "$got" = "$expect" ]; then mark="ok  "; else mark="FAIL"; FAILURES=$((FAILURES+1)); fi
  printf "%s  %-6s expected=%-6s  %s\n" "$mark" "$got" "$expect" "$label"
  echo "$out" | grep -E 'claim-check|Traceback' | head -2 | sed 's/^/         /'
}

echo "=== 22 passing + 1 failing ==="
cat > tests/test_suite.py <<'PY'
import pytest
@pytest.mark.parametrize("i", range(22))
def test_ok(i): assert True
def test_broken(): assert False
PY
try "ratio lie spelled 'passed'"      BLOCK  "x

22/22 passed"
try "ratio lie spelled 'tests pass'"  BLOCK  "x

22/22 tests pass"
try "negated ratio claim"             ACCEPT "x

not 22/22 passed"
try "no count claim at all, suite red" ACCEPT "x

Refactor the loop for clarity, no numbers here"

echo "=== forged summary line from a failing test ==="
cat > tests/test_suite.py <<'PY'
import pytest
@pytest.mark.parametrize("i", range(21))
def test_ok(i): assert True
def test_broken():
    print("==== 1 passed in 0.01s ====")
    assert False
PY
try "forged count"                    BLOCK  "x

22 passed"

echo "=== version-shaped duration in captured output ==="
cat > tests/test_suite.py <<'PY'
def test_broken():
    print("==================== deploy finished in 1.2.3s ====================")
    assert False
def test_ok(): assert True
PY
try "poisoned duration, honest claim" ACCEPT "x

1 passed"

echo "=== 60k-character separator line ==="
cat > tests/test_suite.py <<'PY'
def test_broken():
    print("=" * 60000)
    assert False
def test_ok(): assert True
PY
S=$(date +%s)
try "long separator, honest claim"    ACCEPT "x

1 passed"
E=$(date +%s)
echo "         hook wall time: $((E-S))s (was 12s before the fix)"

echo
if [ $FAILURES -eq 0 ]; then echo "ALL E2E SCENARIOS PASS"; else echo "$FAILURES E2E SCENARIO(S) FAILED"; exit 1; fi
