# Acceptance checks

Tests in this directory prove that the Stage 1 task is solved. They must fail on the
starting commit and pass after the agent's change.

- They are **not** part of the agent's repository copy, and the agent's sandbox never mounts
  them. The final verification mounts this directory read-only at `/acceptance`.
- Configure the command under `[verification.acceptance]` in `harness.toml`.
- Confirm the check fails on the starting code with `uv run coding-harness baseline`.
