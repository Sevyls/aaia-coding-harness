# Demo video script (about 2:45, maximum allowed: 4:00)

The lecturer asks for: task entry, tool use, code change, tests and diff. They also want a failing
check that becomes a passing check, the existing tests still passing, and any manual help named.
This script shows exactly that and nothing more.

## Before recording

- Ollama is running and `qwen3.8:27b` is already loaded (send one short prompt first).
- Podman machine is running, image `harness-cosmic-python` is built, `harness.toml` exists.
- Large terminal font, clean terminal. Have `README.md` open at the component diagram.
- The real run takes 1-3 minutes. Record all of it and speed up the waiting part (4-8x) in the edit.

## Script

| Time | Show | Say |
|---|---|---|
| 0:00-0:20 | README component diagram | "This is my coding harness in Python. A task goes in. A local model in Ollama works on a disposable copy of a repository through validated tools. Then the harness runs the final checks itself and shows me the diff and the results. The target is the Cosmic Python example app." |
| 0:20-0:35 | `tasks/invalid-quantity.md` | "The bug: an order line with quantity zero or less is accepted, and a negative quantity even increases the stock. The task asks to reject it, keep the stock unchanged, and answer the HTTP API with a 400 and a message. It does not say how. Allowed scope: `handlers.py`, `flask_app.py` and, if needed, `model.py`. This is enforced: the file tools refuse writes elsewhere, and a final scope check fails if another file changed." |
| 0:35-0:55 | `uv run coding-harness baseline` | "First the acceptance check on the starting commit. It is stored outside the agent's copy. It checks the message bus and the Flask API, and fails: 4 failed, 2 passed. The 20 existing unit tests pass. So the bug is reproduced." |
| 0:55-1:50 | `uv run coding-harness run --task-file tasks/invalid-quantity.md` (sped up) | "Now the task goes in. Every model step and tool request is printed: the model lists and reads files, then edits `handlers.py` and `flask_app.py`. The controller validates each request in code: known tool, valid arguments, path inside the copy. Commands run only as named checks, in a container without network." |
| 1:50-2:15 | End of the run: changed files, diff, check table | "Result: the changed file, the diff with the `qty <= 0` check and the 400 response, and the final checks. Acceptance now passes, the 20 existing unit tests still pass, lint and the scope check pass. VERIFIED. The harness ran these checks itself, not the model. Manual help during the run: none." |
| 2:15-2:40 | Terminal: `uv run pytest` (cut to the last line), then README "Known limitations" | "The core tests run without a model or container: tools, controller, invalid requests, failed commands, limits and output limits. 124 passed. Limits: if the model loops or is denied too often, the run stops with a reason and the checks still run. Known limit: two small tasks only, and the API tests use an in-memory fake instead of Postgres." |

## Optional: second task (only if the total stays under 3:30)

Show `tasks/negative-batch.md` and say: "A second task with its own acceptance check, to show the
harness is not tuned to one bug." Run `baseline -c harness.negative-batch.toml` (2 failed, 3 passed),
then `run -c harness.negative-batch.toml --task-file tasks/negative-batch.md`, sped up. VERIFIED.

## Checklist (what the lecturer looks for)

- [ ] Task entry through the CLI
- [ ] Tool use visible (read, edit) and requests validated
- [ ] Code change and diff shown
- [ ] Acceptance check: fails before, passes after
- [ ] Existing tests still pass
- [ ] Contained execution mentioned (container, no network, acceptance check outside the copy)
- [ ] Limits and errors mentioned, automated tests shown
- [ ] Manual help named ("none")

## Tips

- Say each command out loud before you press Enter.
- If the run differs from the README (steps, wording of the diff), say: "the steps can vary, the
  checks are what matter."
- Cut anything that does not help one of the checklist items. The extras (second-model review,
  qwen2.5-coder 7B experiments, Windows notes) stay in the docs.
