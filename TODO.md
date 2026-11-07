# TODO

Open points from a critical review of the Stage 1 requirements (course handout
"Stage 1 Build a working coding harness"), 2026-10-04.

## Gaps

- [x] **Enforce the allowed scope, not just show it.** Done: `allowed_paths` in `[target]`; file
  tools deny other writes, final `scope` check fails on other changed files.
  The scope in `harness.toml` ("change only `handlers.py` and, if needed, `model.py`; do not
  modify or delete existing tests") is only shown to the model and the reviewer. The file tools
  enforce just the boundary of the repository copy, so the agent may edit `tests/unit/*`. The
  regression check runs those tests from the agent's copy, so a weakened or deleted test would
  still count as "existing tests still pass". The hidden acceptance check cannot be gamed this
  way, and the diff would show the change, but the handout says: "File tools must enforce the
  same allowed scope. A class name alone does not create a security boundary."
  Fix: `allowed_paths` (globs) in `[target]`; the file tools reject writes outside them, and a
  final check fails if a changed file is outside. Or mount the original tests read-only into the
  regression container. Touches `config.py`, `tools.py`, `verification.py`, plus tests.

- [ ] **Record the demo video** (at most 4 minutes). Suggested flow: `baseline` (acceptance check
  fails), `run --task-file tasks/invalid-quantity.md` with qwen3.8:27b (diff, VERIFIED),
  optionally 30 seconds of a qwen2.5-coder:7b run where the guardrails and limits stop it.

## Weak points

- [x] **Run more existing tests as regression checks.** Done in Stage 2: each task file runs the
  20 unit tests, the 6 offline integration tests (SQLite) and a `compileall` build check.

- [x] **Show that the extras generalise.** Done with a second task (`tasks/negative-batch.md`),
  the points below are the original reasoning. The guardrails, lint and loop detection were all
  derived from failures on this one task. A second small task would show whether they help
  elsewhere. Not required by the handout.

- [x] **Decide how to present the hint in the task.** Done: the task no longer names an
  exception; the acceptance check is behavioural (any exception, API answers 400).

- [ ] **Pin what can drift.** The base image `python:3.9-slim`, the target's `requirements.txt`
  and the model tag `qwen3.8:27b` can change over time. The tag already changed: `aaee06c39dcf` in
  Stage 1, `47951e4a6661` in Stage 2 (`ollama list`). Consider pinning the image digest.

## Checked, holds up

- Ctrl+C or timeout: the container is removed with `podman rm --force` (`except BaseException`
  in `sandbox.py`); the process group, or on Windows the process tree, is killed.
- Sandbox: no network, all capabilities dropped, read-only root filesystem, repository mounted
  read-only, no credentials; the acceptance check is mounted only for verification.
- Guardrails and lint run on the host but only parse code; repository code runs only in the
  container.
- All 7 required test types exist, run without a model or API key, and pass on Windows.
- Model replies, file contents and reviewer output are validated or only shown, never executed.

## Stage 2 open points

- [x] **Stage 2 video:** generated with `video/build.sh` (3:58), file `video/stage2-demo.mp4` (git-ignored).
- [ ] **Read model of a cancelled allocation.** The acceptance check does not cover
  `allocations_view`; a handler on SQLite could (`tests/integration/test_views.py` style).
- [ ] **Windows:** `coding-harness cancel <id>` is POSIX only; Ctrl+C works everywhere.
- [ ] **Stage 3:** a second feature, and a few repeated runs per task to report a success rate
  instead of single runs.
