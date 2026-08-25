#!/usr/bin/env bash
# Close one benchmark run: pull tokens/time/model out of the Codex rollout log,
# measure the produced code with git, run the tests, append the row.
#
# Usage:  bash bench/run-end.sh
set -euo pipefail

BENCH="$(cd "$(dirname "$0")" && pwd)"

# Git Bash on Windows: a shell opened before the toolchain was installed
# carries a stale PATH. Re-add the standard Go install locations.
if ! command -v go >/dev/null 2>&1; then
  for d in "/c/Program Files/Go/bin" "$HOME/go/bin" "/c/Go/bin"; do
    [ -d "$d" ] && PATH="$PATH:$d"
  done
  export PATH
fi

PY=$(command -v python3 || command -v python) || {
  echo "ERROR: python not found on PATH." >&2; exit 1; }

"$PY" "$BENCH/collect.py"
rm -f "$BENCH/.current-run"
