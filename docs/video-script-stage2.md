# Demo video script, Stage 2 (about 3:45, maximum allowed: 4:00)

Shows what the Stage 2 handout asks for: selecting a project, a feature across modules, navigation,
the bounded context, failed checks guiding fixes, cancel, safe resume (including a refused one),
the final diff and checks, and any manual help.

## Before recording

- Ollama is running and `qwen3.8:27b` is loaded (send one short prompt first).
- Podman machine is running, image `harness-cosmic-python` is built, `harness.toml` exists.
- Two terminals side by side, large font. `README.md` open at the agent loop diagram.
- The real run takes a few minutes. Record all of it, speed up the waiting parts (4–8x).

## Script

**1. What Stage 2 adds (0:00–0:25), README agent loop diagram**
"Stage 1 fixed a bug in one place. Stage 2 does a feature across modules: it finds the code
itself, keeps the model's context bounded, feeds failed checks back, and saves every step so a run
can be cancelled and resumed safely."

**2. Project and task (0:25–0:50), `coding-harness projects`, `tasks/cancel-allocation.toml`**
"Only projects listed in the settings can be selected. The task file holds the request, the files
the agent may change, and the hidden acceptance and regression checks, saved with the session.
The feature: cancel an allocation, across the domain model, commands, handlers and the Flask API."

**3. Baseline (0:50–1:05), `coding-harness baseline --task-file tasks/cancel-allocation.toml`**
"On the starting commit the acceptance check fails: 8 failed, because there is no `Deallocate`
command and no endpoint. Unit, integration and build checks pass."

**4. Run, then cancel (1:05–1:45), `coding-harness run --task-file …` (sped up), Ctrl+C**
"The session id comes first. The model uses `find_symbol` and `module_deps` to find the flow, then
edits. I press Ctrl+C: the run stops, no further action, the final checks are skipped, and the
diff so far is shown as unfinished work."

**5. Status and a refused resume (1:45–2:20), second terminal**
`coding-harness status <id>`: "Status cancelled, the budget used so far, the last steps."
Edit a file in `.harness/runs/<id>/repo`, then `coding-harness resume <id>`: "The working copy no
longer matches the checkpoint, so the session is paused and nothing runs." Undo the edit
(`git -C .harness/runs/<id>/repo checkout -- <file>` only for files the agent did not change, or
`--accept-working-copy` after inspecting).

**6. Resume to the end (2:20–3:20), `coding-harness resume <id>` (sped up)**
"Same checks again, now it matches. The consumed steps carry over, and an interrupted action is
listed to the model, never replayed. When the model says done, the harness runs the unit tests
and lint itself; if one fails, the output goes back to the model. Then the final checks: acceptance
9 passed, unit, integration, build, lint and scope passed: VERIFIED. Here is the diff."

**7. Close (3:20–3:45), `tests/` and README**
"166 tests cover navigation, context limits, session storage, cancellation, safe resume and the
fix loop with scripted replies, so they need no model. Manual help: <none / what it was>."

## Manual help to name

Name anything you did by hand during the recorded run (restarting Ollama, re-running after a
model timeout, the deliberate edit in step 5). The deliberate edit is part of the demo, not help.
