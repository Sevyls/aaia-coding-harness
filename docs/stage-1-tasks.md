# Stage 1 tasks and runs

Moved from the README in Stage 2. The commands below are the current ones (task files); the run ids refer to traces on the author's machine.


- **Target:** [cosmicpython/code](https://github.com/cosmicpython/code) at commit `14c84797ffa77255d53cf1a02fe6aafda2b68aeb`
- **Bug:** allocating an order line with a quantity of zero or less is accepted. A negative
  quantity even *increases* the batch's available stock (10 → 15 for −5) and publishes an
  `Allocated` event, and `POST /allocate` answers 202 (or 500 once something raises). The path runs
  `flask_app` → `commands.Allocate` → message bus → `handlers.allocate` → `model.Product` / `model.Batch`.
- **Task:** [`tasks/invalid-quantity.toml`](../tasks/invalid-quantity.toml). The task does not name a
  solution or an exception type. It asks for a rejection from the allocation flow with unchanged
  stock, and for status 400 with a JSON `message` from `POST /allocate`, as for an unknown SKU.
- **Allowed scope:** `service_layer/handlers.py`, `entrypoints/flask_app.py` and, if needed,
  `domain/model.py`, enforced by
  `allowed_paths` (file tools and final `scope` check). Existing tests must not change.
- **Acceptance check:** [`acceptance/invalid-quantity/`](../acceptance/invalid-quantity/test_invalid_quantity.py) (shared fakes in `acceptance/conftest.py`).
  It checks two levels on in-memory fakes: the message bus (any exception, stock unchanged) and
  the real Flask app through its test client (400, a JSON `message`, stock unchanged). On the
  starting commit, `baseline` reports 4 failed and 2 passed (the two positive-quantity tests).
  After the agent's change all 6 pass and the 20 existing unit tests still pass.
- **Manual help during the agent run:** none.

Stage 1 runs with qwen3.8:27b (before the Stage 2 changes; traces are kept locally under `.harness/runs/`):

| Run | Task given | Result |
|---|---|---|
| `20260927-214256`, `20260927-215753`, `20260928-202511` | earlier version of `tasks/invalid-quantity.md` (bus only, named `handlers.InvalidQuantity`) | VERIFIED: the same 6-line fix each time, 5 model calls, 7 actions, 0 denied, 11,564 prompt tokens in the last run |
| `20260927-214641` | only the word "invalid-quantity.md" (a usage error) | NOT VERIFIED: the model guessed `qty < 0`, claimed success, and the acceptance check failed for `qty = 0`. The CLI now rejects one-word tasks. |
| `20261004-222903` | earlier version of the task, Windows, final harness with lint | VERIFIED: acceptance, the 20 unit tests and lint pass; 4 model calls, 7 actions, 0 denied, 8,934 prompt tokens |
| `20261006-182952` | `tasks/invalid-quantity.md` (now `.toml`) (no exception name, API must answer 400), `allowed_paths` enforced | VERIFIED: acceptance (6 tests), the 20 unit tests, lint and scope pass; changed `handlers.py` and `flask_app.py`; 6 model calls, 11 actions, 0 denied |
| `20261104-174531` | Stage 2 harness: `tasks/invalid-quantity.toml`, sessions, context, fix loop, temperature 0.6 | VERIFIED: acceptance (6 tests), unit, integration, build, lint and scope pass; changed `handlers.py` and `flask_app.py`; 10 model calls, 15 actions, 0 denied, 4 minutes |

## Second task: negative batch quantity

A second, similar task shows that the harness is not tuned to one bug: creating a stock batch
with a negative quantity is accepted. [`tasks/negative-batch.toml`](../tasks/negative-batch.toml) asks to
reject it and to answer `POST /add_batch` with 400 and a `message`. Its acceptance check is
[`acceptance/negative-batch/`](../acceptance/negative-batch/test_negative_batch.py). Since Stage 2 each
task file carries its own checks, so the separate `harness.negative-batch.toml` is gone.

```bash
uv run coding-harness baseline --task-file tasks/negative-batch.toml   # 2 failed, 3 passed
uv run coding-harness run --task-file tasks/negative-batch.toml        # VERIFIED (Stage 1 and Stage 2 harness)
```

| Run | Result |
|---|---|
| `20261006-183355-d5e9a1` (qwen3.8:27b) | VERIFIED: acceptance (5 tests), the 20 unit tests, lint and scope pass; changed `handlers.py` and `flask_app.py`; 8 model calls, 13 actions, 0 denied |

Runs with the smaller qwen2.5-coder:7b, and the harness changes they led to, are in
[experiments.md](experiments.md).
