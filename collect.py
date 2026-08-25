#!/usr/bin/env python3
"""Collect one benchmark row from a finished Codex run.

Reads the Codex rollout JSONL written for the session that started after the
run marker, pulls token usage / model / wall time out of it, measures the code
the agent actually produced with git, and runs the test suite itself so
"did it work" is the runner's answer, not the agent's.

Invoked by run-end.sh; not meant to be called directly.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# The Windows console defaults to a legacy code page; keep labels readable.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BENCH = Path(__file__).resolve().parent
RESULTS = BENCH / "results.csv"
SCOREBOARD = BENCH / "SCOREBOARD.md"
CONFIG = BENCH / "bench.config"

# Only structural fallbacks live here. A test command is deliberately absent:
# guessing one for someone else's repo would score the wrong thing silently.
CONFIG_DEFAULTS = {
    "work": "",
    "baseline_tag": "bench-start",
    "test_cmd": "",
    "pass_pattern": "",
    "fail_pattern": "",
}

FIELDS = [
    "run", "label", "model", "effort", "started_utc", "duration_s",
    "in_tok", "cached_in_tok", "out_tok", "reasoning_tok", "total_tok",
    "loc_added", "loc_deleted", "files_changed", "tests", "tests_detail",
    "session_id",
]


def native_path(value: str) -> Path:
    """Accept an MSYS-style /c/... path from Git Bash under Windows Python."""
    if os.name == "nt" and len(value) > 2 and value[0] == "/" and value[2] == "/":
        value = f"{value[1].upper()}:{value[2:]}"
    return Path(value)


def read_kv(path: Path) -> dict:
    """Parse a KEY=value file — the format both the shell and Python read."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def read_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    cfg.update(read_kv(CONFIG))
    # Environment wins, so a single run can be scored differently without
    # editing the shared config.
    for key in cfg:
        env = os.environ.get(f"BENCH_{key.upper()}")
        if env:
            cfg[key] = env
    if not cfg["test_cmd"]:
        sys.exit(
            "\n".join([
                "ERROR: no test command configured.",
                f"       expected 'test_cmd=' in {CONFIG}",
                "",
                "       Point the harness at your repo:",
                "         bash bench/init.sh /path/to/repo",
                "       or set it for one run:",
                '         BENCH_TEST_CMD="make test" bash bench/run-end.sh',
            ])
        )
    return cfg


def read_marker() -> dict:
    marker = BENCH / ".current-run"
    if not marker.exists():
        sys.exit("ERROR: no .current-run marker. Run bench/run-begin.sh first.")
    out = {}
    for line in marker.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def sessions_root() -> Path:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return Path(os.environ.get("CODEX_HOME", home / ".codex")) / "sessions"


def find_rollout(started_epoch: int, workdir: Path):
    """Newest rollout whose session started at/after the marker.

    Prefers a session whose cwd is the benchmark workdir; falls back to the
    newest qualifying session so a run launched from a parent dir still counts.
    """
    candidates = []
    for path in sessions_root().rglob("rollout-*.jsonl"):
        # Cheap prefilter only: on Windows mtime can lag the last write, so
        # keep the window generous and let session_meta be the real gate.
        if path.stat().st_mtime < started_epoch - 3600:
            continue
        meta = None
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "session_meta":
                    meta = rec
                    break
        if meta is None:
            continue
        ts = parse_ts(meta["payload"].get("timestamp") or meta.get("timestamp"))
        if ts is None or ts.timestamp() < started_epoch - 5:
            continue
        cwd = Path(meta["payload"].get("cwd", ""))
        same = cwd.resolve() == workdir.resolve() if str(cwd) else False
        candidates.append((same, ts, path, meta))
    if not candidates:
        sys.exit(
            "ERROR: no Codex session found that started after run-begin.\n"
            "       Did the run happen in a fresh Codex chat?"
        )
    # Recency decides, never the cwd match: preferring a matching cwd would
    # silently reach past the run you just did and score an older session.
    candidates.sort(key=lambda c: c[1])
    picked = candidates[-1]
    if not picked[0] and not os.environ.get("BENCH_ALLOW_CWD_MISMATCH"):
        cwd = picked[3]["payload"].get("cwd", "<unknown>")
        sys.exit(
            "\n".join([
                "ERROR: the Codex session ran in the wrong directory.",
                f"       session cwd : {cwd}",
                f"       expected    : {workdir}",
                "",
                "       Its edits landed outside the benchmark workdir, so this",
                "       row would report 0 lines and stale tests. Re-run:",
                '         bash bench/run-begin.sh "<label>"',
                f"         cd {workdir} && codex     # start Codex HERE",
                "         bash bench/run-end.sh",
                "",
                "       Override (only if the paths differ legitimately):",
                "         BENCH_ALLOW_CWD_MISMATCH=1 bash bench/run-end.sh",
            ])
        )
    return picked


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def scan_rollout(path: Path) -> dict:
    """Last token_count wins: Codex reports cumulative totals per event."""
    usage, model, effort, session_id = {}, "", "", ""
    first_ts = last_ts = None
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_ts(rec.get("timestamp"))
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            payload = rec.get("payload") or {}
            kind = rec.get("type")
            if kind == "session_meta":
                session_id = payload.get("session_id", "")
            elif kind == "turn_context":
                model = payload.get("model", model)
                effort = payload.get("effort", effort)
            elif kind == "event_msg" and payload.get("type") == "token_count":
                total = (payload.get("info") or {}).get("total_token_usage")
                if total:
                    usage = total
    duration = int((last_ts - first_ts).total_seconds()) if first_ts and last_ts else 0
    return {
        "session_id": session_id,
        "model": model,
        "effort": effort,
        "started_utc": first_ts.strftime("%Y-%m-%dT%H:%M:%SZ") if first_ts else "",
        "duration_s": duration,
        "in_tok": usage.get("input_tokens", 0),
        "cached_in_tok": usage.get("cached_input_tokens", 0),
        "out_tok": usage.get("output_tokens", 0),
        "reasoning_tok": usage.get("reasoning_output_tokens", 0),
        "total_tok": usage.get("total_tokens", 0),
    }


def git_stat(workdir: Path, tag: str) -> dict:
    """Lines the agent produced, measured against the identical baseline.

    New test files are untracked, so stage everything into a throwaway index —
    the real index and working tree stay untouched.
    """
    ref = f"refs/tags/{tag}"
    env = dict(os.environ, GIT_INDEX_FILE=str(workdir / ".git" / "bench-index"))
    subprocess.run(["git", "read-tree", ref], cwd=workdir, env=env, check=True,
                   capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=workdir, env=env, check=True, capture_output=True)
    numstat = subprocess.run(
        ["git", "diff-index", "--numstat", ref],
        cwd=workdir, env=env, capture_output=True, text=True, check=True,
    ).stdout
    (workdir / ".git" / "bench-index").unlink(missing_ok=True)

    added = deleted = files = 0
    for row in numstat.splitlines():
        parts = row.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            deleted += int(parts[1])
    return {"loc_added": added, "loc_deleted": deleted, "files_changed": files}


def run_tests(workdir: Path, cfg: dict) -> dict:
    """Green/red is the runner's exit code — that part is language-agnostic.

    Per-test counts need a framework-specific marker, so they are optional:
    leave the patterns empty and the row reports the exit code instead.
    """
    proc = subprocess.run(
        cfg["test_cmd"], cwd=workdir, capture_output=True, text=True, shell=True,
    )
    output = proc.stdout + proc.stderr
    verdict = "green" if proc.returncode == 0 else "red"

    pass_pat, fail_pat = cfg.get("pass_pattern", ""), cfg.get("fail_pattern", "")
    if pass_pat or fail_pat:
        passed = count_tests(output, pass_pat)
        failed = count_tests(output, fail_pat)
        detail = f"{passed} pass / {failed} fail"
    else:
        detail = f"exit {proc.returncode}"
    return {"tests": verdict, "tests_detail": detail, "_output": output}


def count_tests(output: str, pattern: str) -> int:
    """Two shapes of runner, one config key.

    Per-test markers (Go's `--- PASS`) are counted by occurrence. Summary lines
    (pytest's `2 passed`) carry the number themselves, so a pattern containing
    a capture group is read as a regex and the group is the count.
    """
    if not pattern:
        return 0
    if "(" in pattern:
        match = re.search(pattern, output)
        return int(match.group(1)) if match else 0
    return output.count(pattern)


def write_row(row: dict, test_output: str) -> None:
    new = not RESULTS.exists()
    with RESULTS.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in FIELDS})
    (BENCH / "logs").mkdir(exist_ok=True)
    (BENCH / "logs" / f"run-{row['run']}-tests.txt").write_text(test_output, encoding="utf-8")


def render_scoreboard() -> None:
    with RESULTS.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    head = (
        "# Scoreboard\n\n"
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        "from `results.csv` — collected, not hand-typed.\n\n"
        "| # | Setup | Model | Started (UTC) | Time | In tok | Cached in | Out tok | Total | LOC +/- | Files | Tests |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    body = ""
    for r in rows:
        body += (
            f"| {r['run']} | {r['label']} | {r['model']} | {r['started_utc']} | "
            f"{r['duration_s']}s | {int(r['in_tok']):,} | {int(r['cached_in_tok']):,} | "
            f"{int(r['out_tok']):,} | {int(r['total_tok']):,} | "
            f"+{r['loc_added']}/-{r['loc_deleted']} | {r['files_changed']} | "
            f"{r['tests']} ({r['tests_detail']}) |\n"
        )
    SCOREBOARD.write_text(head + body, encoding="utf-8")


def main() -> None:
    cfg = read_config()
    marker = read_marker()
    workdir = native_path(marker["work"])
    started_epoch = int(marker["started_epoch"])

    _, _, rollout, _ = find_rollout(started_epoch, workdir)
    row = scan_rollout(rollout)
    row.update(git_stat(workdir, marker.get("baseline_tag") or cfg["baseline_tag"]))
    tests = run_tests(workdir, cfg)
    test_output = tests.pop("_output")
    row.update(tests)
    row["label"] = marker["label"]

    prior = 0
    if RESULTS.exists():
        with RESULTS.open(encoding="utf-8") as fh:
            prior = sum(1 for _ in csv.DictReader(fh))
    row["run"] = prior + 1

    write_row(row, test_output)
    render_scoreboard()

    print(f"RUN {row['run']} RECORDED  ({row['label']})")
    print(f"  rollout : {rollout.name}")
    print(f"  model   : {row['model']} (effort {row['effort']})")
    print(f"  started : {row['started_utc']}   duration {row['duration_s']}s")
    print(f"  tokens  : in {row['in_tok']:,} (cached {row['cached_in_tok']:,}) "
          f"/ out {row['out_tok']:,} / total {row['total_tok']:,}")
    print(f"  code    : +{row['loc_added']} -{row['loc_deleted']} lines, "
          f"{row['files_changed']} files")
    print(f"  tests   : {row['tests']} — {row['tests_detail']}")
    print(f"\n  -> {RESULTS}\n  -> {SCOREBOARD}")


if __name__ == "__main__":
    main()
