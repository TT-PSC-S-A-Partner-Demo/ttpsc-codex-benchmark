#!/usr/bin/env bash
# Point the benchmark at any repo: detect its test command, tag the current
# commit as the baseline every run resets to, and write bench.config.
#
# Usage:  bash bench/init.sh <repo-dir> [--tag NAME] [--test-cmd "..."]
#
# The repo must already be at the state you want every run to start from —
# init.sh tags what is there, it does not modify your code.
set -euo pipefail

BENCH="$(cd "$(dirname "$0")" && pwd)"
TARGET=""
TAG="bench-start"
TEST_CMD=""

while [ $# -gt 0 ]; do
  case "$1" in
    --tag)      TAG="$2"; shift 2 ;;
    --test-cmd) TEST_CMD="$2"; shift 2 ;;
    -h|--help)  sed -n '2,9p' "$0"; exit 0 ;;
    *)          TARGET="$1"; shift ;;
  esac
done

[ -n "$TARGET" ] || { echo "ERROR: no repo directory given." >&2; sed -n '4,6p' "$0" >&2; exit 1; }
[ -d "$TARGET" ] || { echo "ERROR: '$TARGET' is not a directory." >&2; exit 1; }
TARGET="$(cd "$TARGET" && pwd)"

# --- test command: explicit flag wins, else detect from the repo's manifest ---
PASS_PAT=""
FAIL_PAT=""
if [ -z "$TEST_CMD" ]; then
  if   [ -f "$TARGET/go.mod" ];         then TEST_CMD="go test ./... -count=1 -v"; PASS_PAT="--- PASS"; FAIL_PAT="--- FAIL"
  elif [ -f "$TARGET/Cargo.toml" ];     then TEST_CMD="cargo test";                PASS_PAT="([0-9]+) passed"; FAIL_PAT="([0-9]+) failed"
  elif [ -f "$TARGET/pom.xml" ];        then TEST_CMD="mvn -q test"
  elif [ -f "$TARGET/build.gradle" ] || [ -f "$TARGET/build.gradle.kts" ]; then TEST_CMD="gradle test"
  elif [ -f "$TARGET/pyproject.toml" ] || [ -f "$TARGET/setup.py" ] || [ -f "$TARGET/requirements.txt" ]; then
    TEST_CMD="pytest -q"; PASS_PAT="([0-9]+) passed"; FAIL_PAT="([0-9]+) failed"
  elif [ -f "$TARGET/package.json" ];   then TEST_CMD="npm test --silent"
  elif ls "$TARGET"/*.sln "$TARGET"/*.csproj >/dev/null 2>&1; then TEST_CMD="dotnet test"
  else
    echo "WARNING: could not detect a test command for $TARGET." >&2
    echo "         Re-run with --test-cmd \"<your command>\", or edit bench.config after." >&2
    TEST_CMD="false"   # a red row is honest; a missing runner is not
  fi
fi

# --- baseline: git repo + a tag every run resets to ---
if [ ! -d "$TARGET/.git" ]; then
  echo "== no git repo in $TARGET — creating one so runs have a baseline =="
  git -C "$TARGET" init -q
  git -C "$TARGET" add -A
  git -C "$TARGET" commit -qm "bench: baseline"
fi
if [ -n "$(git -C "$TARGET" status --porcelain)" ]; then
  echo "== uncommitted changes present — committing them as the baseline =="
  git -C "$TARGET" add -A
  git -C "$TARGET" commit -qm "bench: baseline"
fi
git -C "$TARGET" tag -f "$TAG" >/dev/null
echo "== tagged $(git -C "$TARGET" rev-parse --short HEAD) as '$TAG' =="

# --- config both the shell and Python read ---
# `pwd -W` yields a full Windows path; cygpath -m can hand back an 8.3 name.
WORK_NATIVE="$(cd "$TARGET" && { pwd -W 2>/dev/null || cygpath -m "$TARGET" 2>/dev/null || pwd; })"
cat > "$BENCH/bench.config" <<EOF
# Written by bench/init.sh. KEY=value; no spaces around '='.
# Every value can be overridden per-run with BENCH_<KEY> in the environment.
work=$WORK_NATIVE
baseline_tag=$TAG
test_cmd=$TEST_CMD
# Optional per-test counts. Leave both empty to report the exit code instead.
pass_pattern=$PASS_PAT
fail_pattern=$FAIL_PAT
EOF

echo
echo "READY"
echo "  workdir   : $TARGET"
echo "  baseline  : $TAG"
echo "  test cmd  : $TEST_CMD"
echo "  config    : $BENCH/bench.config"
echo
echo "Verify the test command is green at baseline before benchmarking:"
echo "  cd \"$TARGET\" && $TEST_CMD"
echo
echo "Then per run:"
echo "  bash bench/run-begin.sh \"<label>\""
echo "  cd \"$TARGET\" && codex        # fresh chat, paste the prompt"
echo "  bash bench/run-end.sh"
