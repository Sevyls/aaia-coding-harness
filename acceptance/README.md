# Acceptance checks

Tests in this directory prove that a task is solved. They must fail on the
starting commit and pass after the agent's change.

- They are **not** part of the agent's repository copy, and the agent's sandbox never mounts
  them. The final verification mounts this directory read-only at `/acceptance`.
- Configure the command under `[acceptance]` in the task file (`tasks/<name>.toml`).
- Confirm the check fails on the starting code with
  `uv run coding-harness baseline --task-file tasks/<name>.toml`.

One directory per task (`invalid-quantity/`, `negative-batch/`, `cancel-allocation/`). `conftest.py` holds the
in-memory fakes they share; the acceptance command passes `--rootdir=/acceptance` so it is loaded.

The fake unit of work has a `session` that records SQL instead of running it, like the real one
offers a database session. Without it, a correct change that updates the read model
(`allocations_view`) directly in a command handler failed here although it works against the
real database; the first Stage 2 run showed this (see the README).
