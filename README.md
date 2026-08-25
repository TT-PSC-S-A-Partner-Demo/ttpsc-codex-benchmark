# bench — agent benchmark harness

Scores an AI coding agent on a real task in a real repo, from logs rather than
by hand. One row per run: tokens in/out, wall time, model, lines produced, and a
green/red verdict from your own test runner — never from the agent's claim.

Works on **any repo**. Ships configured for the Go kit next door.

## Requirements

- git, Python 3.8+, and your project's test runner
- **Codex CLI** — token/time/model come from its rollout logs
  (`~/.codex/sessions/**/rollout-*.jsonl`, override with `CODEX_HOME`)

## Point it at your repo

```bash
bash bench/init.sh /path/to/your/repo
```

It detects the test command, commits any pending work, tags the current commit
as the baseline every run resets to, and writes `bench/bench.config`.

Detected stacks: Go, Rust, Python, Node, .NET, Maven, Gradle. Anything else:

```bash
bash bench/init.sh /path/to/repo --test-cmd "make test" --tag my-baseline
```

The repo must already be at the state runs should start from — `init.sh` tags
what is there, it never edits your code. Confirm the baseline is green before
benchmarking; a red baseline makes every row meaningless.

## Run

Three steps per row. Only the label changes between runs.

```bash
bash bench/run-begin.sh "gpt-5.6-luna, no tools"    # resets to baseline, arms
cd /path/to/your/repo && codex                      # FRESH chat, paste prompt
bash bench/run-end.sh                               # collects the row
```

Paste the **identical prompt** every run. The variable under test is the model
or the tooling, never the wording.

A **fresh chat each run is mandatory** — Codex reports token totals cumulatively
per session, so continuing an old chat inflates the row.

## Configuration

`bench/bench.config`, `KEY=value`. Parsed, never sourced — it is data, not code.
`init.sh` writes it; `bench.config.example` is the documented template with a
preset per stack, if you would rather fill it in by hand:

```bash
cp bench/bench.config.example bench/bench.config
```

| Key | Meaning |
|---|---|
| `work` | repo the runs happen in |
| `baseline_tag` | git tag every run resets to |
| `test_cmd` | command whose exit code decides green/red |
| `pass_pattern` / `fail_pattern` | optional per-test counts |

Patterns come in two shapes. A plain string is counted by occurrence — Go prints
`--- PASS` once per test. A pattern with a capture group is read as a regex and
the group *is* the count — pytest prints `3 passed` once. Leave both empty and
the row reports the exit code instead.

Any key can be overridden for a single run without editing the file:

```bash
BENCH_TEST_CMD="pytest -q -k guard" bash bench/run-end.sh
```

## Output

- `results.csv` — append-only raw record, one row per run
- `SCOREBOARD.md` — regenerated from the CSV after every run
- `logs/run-N-tests.txt` — full test output, for when a row is red

## Where each number comes from

| Column | Source | Why it is trustworthy |
|---|---|---|
| Started, Time | `session_meta.timestamp` + last event | Codex's clock, not a stopwatch |
| In / Cached / Out / Reasoning / Total | last `token_count` → `total_token_usage` | the numbers `/status` renders |
| Model, effort | `turn_context.payload.model` | what actually ran, not what you meant to pick |
| LOC +/-, files | `git diff-index --numstat` vs the baseline tag | identical start every run; new untracked files counted |
| Tests | `test_cmd` exit code | your runner's verdict |

## Guards

`run-end.sh` refuses to write a row when the agent session's `cwd` is not the
benchmark repo — its edits landed elsewhere, so the row would report 0 lines and
stale tests. Re-run it properly, or override deliberately:

```bash
BENCH_ALLOW_CWD_MISMATCH=1 bash bench/run-end.sh
```

It also picks sessions by recency, never by cwd match, so it cannot silently
score an older session instead of the run you just did.

## Reading the results honestly

- **Token counts do not compare across repos.** Input is dominated by the system
  prompt, instruction files, and MCP tool definitions — not by your code. This
  compares variants *within one task*, which is the whole point.
- **n=1 is noise.** Repeat each variant ~3x before believing a gap.
- **Fewer lines is not automatically better.** Read LOC next to the test column.
- A tool that moves no number is a real result. Report it.

## Sharing this kit

`bench/` is self-contained — copy the directory into any repo. The recipient
runs `init.sh` and gets their own baseline.

`.gitignore` already excludes everything machine-local: `bench.config`,
`.current-run`, `__pycache__/`, and the run output (`results.csv`,
`SCOREBOARD.md`, `logs/`). Token counts do not compare across repos or
accounts, so committing one person's numbers invites false comparisons — drop
those lines from `.gitignore` only if you are deliberately publishing results.

Tracked: the four scripts, `README.md`, `bench.config.example`, `.gitignore`.

Handing over a working copy rather than a fresh clone:

```bash
rm -f bench/bench.config bench/results.csv bench/SCOREBOARD.md && rm -rf bench/logs
```

## Limits

- **Codex CLI only.** Claude Code, Cursor, and Copilot write different
  transcript formats and would each need an adapter in `collect.py`.
- One agent session per row.
- The baseline tag is force-moved by `init.sh`; pick a name you do not use
  elsewhere.
