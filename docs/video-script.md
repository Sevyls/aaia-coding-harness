# Demo video script (about 2:30, maximum allowed: 4:00)

Shows what the lecturer asks for: task entry, tool use, code change, diff, a failing check that
becomes a passing one, existing tests still passing, and manual help named.

## Before recording

- Ollama is running and `qwen3.8:27b` is loaded (send one short prompt first).
- Podman machine is running, image `harness-cosmic-python` is built, `harness.toml` exists.
- Large terminal font. `README.md` open at the component diagram.
- The real run takes 1-3 minutes. Record it all, speed up the waiting part (4-8x).

## Script

**1. Intro (0:00-0:20), README diagram**
"This is my coding harness. A task goes in. A local model works on a disposable copy of a
repository through validated tools. Then the harness runs the checks itself and shows the diff.
The target is the Cosmic Python example app."

**2. The task (0:20-0:35), `tasks/invalid-quantity.md`**
"The bug: an order line with quantity zero or less is accepted. The task: reject it and answer the
API with a 400. It does not say how. Writes are only allowed in three named files, and the harness
enforces that."

**3. Baseline (0:35-0:55), `uv run coding-harness baseline`**
"The acceptance check lives outside the agent's copy. On the starting commit it fails: 4 failed,
2 passed. The 20 existing unit tests pass. The bug is reproduced."

**4. Run (0:55-1:50), `uv run coding-harness run --task-file tasks/invalid-quantity.md` (sped up)**
"Every model step and tool request is printed. The model reads files, then edits `handlers.py` and
`flask_app.py`. The harness validates each request in code. Commands run only as named checks, in a
container without network."

**5. Result (1:50-2:15), changed files, diff, check table**
"The diff adds the `qty <= 0` check and the 400 response. Acceptance passes, the 20 existing tests
pass, lint and scope check pass. VERIFIED. The harness ran the checks, not the model. Manual help:
none."

**6. Tests and limits (2:15-2:30), `uv run pytest` (last line), README "Known limitations"**
"The core tests need no model or container: 124 passed. If the model loops or is denied too often,
the run stops with a reason. Known limits: two small tasks, and the API tests use an in-memory fake
instead of Postgres."

## Optional second task (only if total stays under 3:30)

Show `tasks/negative-batch.md`: "A second task with its own acceptance check, so the harness is not
tuned to one bug." Run `baseline -c harness.negative-batch.toml` (2 failed, 3 passed), then
`run -c harness.negative-batch.toml --task-file tasks/negative-batch.md`, sped up. VERIFIED.

## Checklist

- [ ] Task entry through the CLI
- [ ] Tool use visible, requests validated
- [ ] Code change and diff
- [ ] Acceptance check fails before, passes after
- [ ] Existing tests still pass
- [ ] Container, no network, acceptance outside the copy
- [ ] Limits mentioned, automated tests shown
- [ ] Manual help named ("none")

## Tips

- Say each command out loud before pressing Enter.
- If the run differs from the README, say: "the steps can vary, the checks are what matter."
- Cut anything that does not serve a checklist item.
