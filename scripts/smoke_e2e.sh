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
cat > .git/hooks/commit-msg <<'HOOK'
#!/usr/bin/env bash
exec claim-check-precommit "$1"
HOOK
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
