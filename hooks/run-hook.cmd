: << 'CMDBLOCK'
@echo off
REM Cross-platform polyglot launcher. This one file is simultaneously a valid
REM Windows batch script and a valid Bash script:
REM   - cmd.exe treats the first line as a label, runs this batch body, and
REM     exits before ever reaching the Unix tail.
REM   - Bash treats ":" as a no-op and "<< 'CMDBLOCK'" as a quoted heredoc,
REM     swallowing this whole batch body, then runs the Unix tail.
REM
REM One file means ONE hooks.json entry. Shipping a separate .cmd and .sh
REM risks Claude Code executing both - hooks under a single matcher run in
REM parallel - which for a hook that can emit a deny decision would be a
REM genuine hazard. It also must NOT be named ".sh": Claude Code's Windows
REM auto-detection prepends "bash" to any command containing that extension.
REM
REM Usage: run-hook.cmd <entrypoint-module-name>
REM
REM Exits 0 unconditionally, including when no usable Python exists. This
REM launcher must never be the reason a tool call is blocked.

setlocal enabledelayedexpansion
if "%~1"=="" exit /b 0
set "HOOK_MODULE=%~1"
set "PLUGIN_ROOT=%~dp0.."
set "PYTHONPATH=%PLUGIN_ROOT%\src;%PYTHONPATH%"
set "PY_EXE="
set "PY_ARG="

REM The probe doubles as a Microsoft Store stub filter: the stub exits
REM nonzero without running Python, so it fails the same check that rejects
REM an interpreter older than 3.9.
if defined CLAIM_CHECK_PYTHON (
    "%CLAIM_CHECK_PYTHON%" -c "import sys;sys.exit(0 if sys.version_info[:2] >= (3, 9) else 1)" >nul 2>nul
    if !errorlevel! equ 0 set "PY_EXE=%CLAIM_CHECK_PYTHON%"
)
if not defined PY_EXE (
    py -3 -c "import sys;sys.exit(0 if sys.version_info[:2] >= (3, 9) else 1)" >nul 2>nul
    if !errorlevel! equ 0 (
        set "PY_EXE=py"
        set "PY_ARG=-3"
    )
)
if not defined PY_EXE (
    python -c "import sys;sys.exit(0 if sys.version_info[:2] >= (3, 9) else 1)" >nul 2>nul
    if !errorlevel! equ 0 set "PY_EXE=python"
)
if not defined PY_EXE exit /b 0

REM -s suppresses the user site directory so a stray user-site package can't
REM shadow the bundled source. -I/-E are deliberately NOT used: they would
REM discard the PYTHONPATH this launcher depends on.
"%PY_EXE%" %PY_ARG% -s -m claim_check.entrypoints.%HOOK_MODULE%
exit /b 0
CMDBLOCK

HOOK_MODULE="${1:-}"
[ -n "$HOOK_MODULE" ] || exit 0

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)" || exit 0
export PYTHONPATH="${PLUGIN_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

for candidate in "${CLAIM_CHECK_PYTHON:-}" python3 python; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    "$candidate" -c 'import sys;sys.exit(0 if sys.version_info[:2] >= (3, 9) else 1)' >/dev/null 2>&1 || continue
    "$candidate" -s -m "claim_check.entrypoints.${HOOK_MODULE}"
    exit 0
done

exit 0
