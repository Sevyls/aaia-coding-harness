# Experiments with qwen2.5-coder:7b

The Stage 1 evidence uses qwen3.8:27b (see the README). These runs use the smaller
qwen2.5-coder:7b (`--model qwen2.5-coder:7b`, Windows) to see where the harness breaks and to
improve it. Each change to the harness was measured with three runs before and after. Traces are
kept locally under `.harness/runs/<id>/`.

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
