# Experiments with qwen2.5-coder:7b

The Stage 1 evidence uses qwen3.8:27b (see the README). These runs use the smaller
qwen2.5-coder:7b (`--model qwen2.5-coder:7b`, Windows) to see where the harness breaks and to
improve it. Each change to the harness was measured with three runs before and after. Traces are
kept locally under `.harness/runs/<id>/` and are not in the repository.

To reproduce with the current harness (`ollama pull qwen2.5-coder:7b` first):

```bash
uv run coding-harness run --task-file tasks/invalid-quantity.toml --model qwen2.5-coder:7b
uv run coding-harness run --task-file tasks/invalid-quantity.toml --model qwen2.5-coder:7b --review-model qwen3.8:27b
```

For the context-window runs, set `context_window = 16384` under `[model]` in `harness.toml`.
The "before" rows below describe earlier versions of the harness (see `git log`); the current
version behaves like the last row of each table. Small models vary more between runs than
qwen3.8:27b, so expect the same kinds of failure rather than the same steps.

## Edit guardrails

Before and after adding the indentation repair, undefined-name check, numbered rejection context
and no-op check ([guardrails.md](guardrails.md)):

| Harness | Verified | What happened |
|---|---|---|
| Before (`20261004-213903`, `-213950`, `-214016`) | 0 / 3 | Run 1: raised `InvalidQuantity` without defining it, the unit tests still passed, and the model claimed success; only the acceptance check caught it. Runs 2 and 3: sent the same unindented edit 5 times, rejected by the syntax guardrail each time, then `repeat_limit`. |
| After (`20261004-215420`, `-215502`, `-215542`) | 2 / 3 | The no-op and undefined-name checks rejected its first edits; it then defined the exception and the check. 11–12 model calls, about 33,000 prompt tokens, one denied call to a nonexistent `commit` tool. The failed run put an `import` inside the function and hit `repeat_limit`. |

The two verified 7B fixes pass every check but are not clean: the class sits between the imports
and `line = OrderLine(...)` is duplicated. The checks prove the behaviour, not the code quality,
so the diff review stays necessary.

## Lint and review

qwen2.5-coder:7b with `lint = true` and `--review-model qwen3.8:27b`:

| Harness | Verified | What happened |
|---|---|---|
| Lint + review (`20261004-220809`, `-220847`, `-222010`) | 0 / 3 | Two runs hit `repeat_limit` before finishing, so there was nothing to review. In the third, the acceptance check **passed**, but lint failed with 17 new problems (duplicated imports, E402). Before the lint check, this run would have been VERIFIED. The reviewer named every problem correctly, including the duplicated `line = OrderLine(...)` that lint cannot see. The 7B model fixed only part of it in its one round. This run also showed two weaknesses, both fixed since: lint warnings compared only with the previous edit, so a worsened problem went quiet, and the agent could not run lint itself. |
| Same, after both fixes (`20261004-222701`, `-222743`, `-222825`) | 0 / 3 | None reached `finish`, so none was reviewed: `model_error` (Ollama aborted the 7B model for repeating tokens), `repeat_limit`, `denied_limit` (5 edits with an empty `old_text`). |
| qwen3.8:27b with lint (`20261004-222052`, `-222903`) | 2 / 2 | Lint passed; same fix and numbers as without lint. |

So the lint check and the review stop a sloppy 7B fix from counting as VERIFIED. They do not make
the 7B model produce a clean one. Review costs time: with Ollama swapping between the 7B and 27B
models, the reviewed run took about 12 minutes.

## Context window and loop detection

The 7B prompts had reached 4,075–4,094 tokens, right at Ollama's apparent 4,096-token default.
Both models were rerun with `context_window = 16384` (lint on, review off), and the loop
detection was then improved based on what the 7B runs did:

| Setting | Verified | What happened |
|---|---|---|
| 7B, 16k context, old repeat limit (consecutive only) (`20261004-223933`, `-224019`, `-224108`) | 0 / 3 | Largest prompt up to 13,793 tokens, so the limit was lifted, but the model was no better. Two runs went off-task to `Batch.__repr__` and alternated `read_file` with the same failing edit until `step_limit` (30 steps, 229,000 prompt tokens). The consecutive-only repeat check missed the cycle. |
| 27B, 16k context (`20261004-224159`, `-224455`, `-224715`) | 3 / 3 | Same fix and numbers as with the default context (largest prompt 2,826), but slower: 34% of the model ran on the CPU. |
| 7B, 16k, loop detection over the whole run (`20261004-225348`, `-225523`, `-225613`) | 0 / 3 | Two runs stopped at `repeat_limit` after 15 steps (74,000 tokens). One still hit `step_limit`: its edit's `new_text` contained `old_text`, so the same edit matched again and the file grew every time. |
| 7B, 16k, identical writes also count (`20261004-230746`, `-230829`, `-230910`) | 0 / 3 | All stopped at `repeat_limit` after 14 steps and about 63,000 tokens, instead of up to 229,000. |

The context limit was not the cause of the 7B failures, and a larger window only makes failing
runs more expensive; the default stays.

## Conclusion

qwen2.5-coder:7b does not solve this task reliably in any setting tried. Its typical failures are
going off-task, broken or misplaced edits, success claims without checking, and loops. With it,
the runs show that the guardrails, lint, review and limits contain a weak model, stop its loops
early with a clear reason, and report nothing false as VERIFIED. Three runs per setting are too
few for a success rate.

# Stage 2: the cancel-allocation feature with qwen3.8:27b

Task: [`tasks/cancel-allocation.toml`](../tasks/cancel-allocation.toml). Machine: Mac with 48 GB
unified memory, Ollama 0.40 (MLX runner), Podman machine running. Model id `47951e4a6661`.

## Run 1 (`20261103-202856-e74504`): a defect in the acceptance check

Task version that listed the five files in `scope` and `allowed_paths`; temperature 0.

- 12 model calls in 5 min 20 s, 18 actions (9 `read_file`, 6 `edit_file`, 2 `run_check`, `finish`),
  0 denied. The navigation tools were not used. The first context selected `model.py`,
  `handlers.py` and `commands.py`, three of the four files the change needed.
- The agent's own checks (unit tests, lint) passed, so no fix round was needed.
- Final checks: unit, integration, build, lint and scope passed; **acceptance 6 of 9**:
  NOT VERIFIED.
- Cause: the new `deallocate` handler deletes the line from `allocations_view` with
  `uow.session.execute(...)`. The real unit of work has a session; the acceptance check's fake had
  none, so the handler crashed there. The behaviour was right, the check's fake was incomplete.
- **Manual help:** the fake unit of work in `acceptance/conftest.py` got a `session` that records
  SQL. Afterwards the baselines of all three tasks were unchanged (8/1, 4/2, 2/3 failed/passed),
  and run 1's code passed all 9 acceptance tests.

## Run 2 (`20261103-204319-959995`): live cancel and resume, then a model that never answered

Same task version, temperature 0.

- Very slow from the start: 4 steps in 13 minutes, one request ran into the 10-minute timeout.
  Memory was at 91 % (Ollama 22–26 GB with the default 131,072-token window, Podman, other apps;
  9 GB compressed, swapping).
- **Cancel during a model request** (`coding-harness cancel`, 21:00): the process ended at once,
  status `cancelled`, no further action, no final checks, no uncertain actions.
- **Refused resume:** one line added by hand to `model.py` → `PAUSED`, nothing executed, reason
  "working copy differs from the last checkpoint". After undoing the line, **resume** passed every
  check and continued at step 5 (the cancelled step 4 still counted).
- Still slow (step 7 timed out), so: second **cancel**, `ollama stop qwen3.8:27b` (manual help),
  `context_window = 32768` set (the model then loaded with 18 GB instead of 22–26 GB), **resume**
  at step 8.
- Step 8 timed out three times (3 × 10 min) with memory healthy: **`model_error`**, no edits,
  acceptance failed, every other check passed.
- Diagnosis by replaying the step-8 request with streaming: at temperature 0 the model produced
  10,578 characters of reasoning in 90 s, going back and forth ("OK here's my final decision …
  Actually, I realize …") without an answer; greedy decoding repeats the same path on every retry.
  At temperature 0.6 the same request finished in 176 s with a plan that avoided the
  `Deallocated` trap. One sample each, so this is a strong hint, not a measurement.
- Change: Qwen's recommended sampling for thinking mode, `temperature = 0.6`, `top_p = 0.95`,
  `top_k = 20` (new `[model]` settings). Runs are no longer repeatable step for step; the checks
  decide the result.

## Run 3 (`20261104-173814-276a73`): hint-free task, VERIFIED

Current task version: `allowed_paths = ["src/allocation/**"]`, the scope only says not to touch
tests, so the files to change are not named. Sampling 0.6 / 0.95 / 20, `context_window = 32768`.

- 25 model calls in 7 minutes, 34 actions (15 `read_file`, 14 `edit_file`, 2 `run_check`,
  `search`, `list_files`, `finish`), 0 denied, 3 failed: one wrong path, and two edits the
  guardrails rejected before writing (an undefined name in `model.py`, a syntax error in
  `handlers.py`); the model corrected both.
- Changed `commands.py`, `model.py`, `handlers.py`, `flask_app.py` (61 lines added): a
  `Deallocate` command, `InvalidDeallocation` in the domain, `Batch.deallocate` and
  `Product.deallocate`, a handler that also removes the line from `allocations_view`, and
  `POST /deallocate` with 400 for `InvalidSku` and `InvalidDeallocation`. It did not reuse the
  `Deallocated` event, so nothing is re-allocated.
- The agent's checks passed at the first `finish` (no fix round). Final checks: acceptance
  (9 tests), unit, integration, build, lint and scope passed: **VERIFIED**.
- Not used in this run: the navigation tools (`find_symbol`, `find_references`, `module_deps`,
  `git_status`). The model explored with `read_file` and one `search`. The history stayed under the
  context limit (largest prompt 14,706 tokens), so no compression happened in a real run.
- Manual help: none during the run.

## Stage 1 task with the Stage 2 harness (`20261104-174531-576f51`)

`tasks/invalid-quantity.toml`, same settings as run 3: **VERIFIED** in 4 minutes, 10 model calls,
15 actions, 0 denied; changed `handlers.py` and `flask_app.py`; acceptance (6 tests), unit,
integration, build, lint and scope passed. The bug-fix workflow from Stage 1 still works.

## Second Stage 1 task with the Stage 2 harness (`20261104-180013-5467f7`)

`tasks/negative-batch.toml`: **VERIFIED** in 2 minutes, 5 model calls, 8 actions, 0 denied;
changed `handlers.py` and `flask_app.py`; acceptance (5 tests), unit, integration, build, lint
and scope passed.

## Runs recorded for the demo video

- `20261104-182755-944080`: the first recording attempt. VHS did not see the "step 6" line, so
  nothing cancelled the run: **VERIFIED** in 9 model calls, 20 actions.
- `20261104-183843-affd86`, the session in the video: a helper of the recording script ran
  `coding-harness cancel` when step 6 started (status `cancelled`, 6 of 30 steps, edits in
  `commands.py` and `model.py`). A deliberate hand edit to `config.py` made `resume` refuse
  (`paused`); after undoing it, `resume` continued at step 7. At step 10 the model called
  `find_references("Deallocated")`, the first use of a navigation tool in a real run, and then
  added its own event `DeallocatedStock` whose only handler updates `allocations_view`, avoiding
  `reallocate`. The agent's checks passed at `finish`; final checks: acceptance (9), unit,
  integration, build, lint, scope: **VERIFIED**. 21 model calls, 28 actions, 1 rejected edit.
