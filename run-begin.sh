#!/usr/bin/env bash
# Start one benchmark run: reset the workspace to the identical baseline and
# drop a marker so run-end.sh knows which agent session belongs to this row.
#
# Usage:  bash bench/run-begin.sh "<label>"      e.g. "terra"
set -euo pipefail

BENCH="$(cd "$(dirname "$0")" && pwd)"
KIT="$(cd "$BENCH/.." && pwd)"
LABEL="${1:-unlabeled}"

# Read only the two keys the shell needs. Parsed, never sourced: values like
# `test_cmd=pytest -q` are not valid shell, and a config file should not be
# executable code.
cfg() {
  [ -f "$BENCH/bench.config" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$BENCH/bench.config" | tail -1
}
WORK="${BENCH_WORK:-$(cfg work)}"
[ -n "$WORK" ] || {
  echo "ERROR: no benchmark repo configured." >&2
  echo "       Run: bash bench/init.sh <repo-dir>" >&2
  exit 1
}
TAG="${BENCH_BASELINE_TAG:-$(cfg baseline_tag)}"
TAG="${TAG:-bench-start}"
case "$WORK" in /*|[A-Za-z]:*) ;; *) WORK="$KIT/$WORK" ;; esac

[ -d "$WORK/.git" ] || {
  echo "ERROR: $WORK is not a git repo." >&2
  echo "       Run 'bash bench/init.sh <repo-dir>' to set up a benchmark target." >&2
  exit 1
}
git -C "$WORK" rev-parse -q --verify "refs/tags/$TAG" >/dev/null || {
  echo "ERROR: $WORK has no tag '$TAG' to reset to." >&2
  echo "       Run 'bash bench/init.sh $WORK' to create it." >&2
  exit 1
}

git -C "$WORK" reset --hard "refs/tags/$TAG" >/dev/null
git -C "$WORK" clean -fdq

# Record a native path: collect.py runs under Windows Python, which cannot
# open an MSYS-style /c/... path.
WORK_NATIVE="$(cd "$WORK" && { pwd -W 2>/dev/null || cygpath -m "$WORK" 2>/dev/null || pwd; })"

cat > "$BENCH/.current-run" <<EOF
label=$LABEL
work=$WORK_NATIVE
baseline_tag=$TAG
started_epoch=$(date +%s)
started_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

echo "RUN ARMED"
echo "  label   : $LABEL"
echo "  workdir : $WORK  (reset to $TAG)"
echo "  started : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo
echo "Now: start a FRESH agent chat in $WORK, paste the prompt, let it finish."
echo "Then: bash bench/run-end.sh"
