# Guardrails, lint, loop detection and review

These go beyond the Stage 1 requirements. Each one was added after a real run showed a failure
it addresses (see [experiments.md](experiments.md)). Only lint is on in the example settings;
review is off by default.

## Edit guardrails (`tools.py`)

Following the linting guardrail in the SWE-agent paper, an edit or write to a Python file is
checked before anything is written. The code is only analysed, never run.

- A new syntax error (`ast.parse`) or a new undefined name (pyflakes, like flake8 F821) rejects
  the change. The model gets the error and the numbered lines as the file would have looked.
- Only problems the change introduces count; an already broken file can still be edited.
- An edit whose `new_text` equals `old_text` is rejected as a no-op.
- Indentation repair: if `old_text` starts after a line's indentation and the later lines of
  `new_text` lack it, they are indented to match, but only when the result is valid Python. If
  `old_text` is not found exactly, whole lines may match ignoring indentation. The model is told
  when either happened. A correct edit is applied exactly as sent.

Tests: `tests/test_file_tools.py`, from `test_edit_that_breaks_python_syntax_is_rejected` to the
end. The later ones each reproduce a qwen2.5-coder:7b failure.

## Lint check (`lint.py`)

pyflakes plus pycodestyle E402 (code before an import). It compares with the starting commit and
counts only problems the run introduced, so warnings already in the target never fail a run.
Static: the code is parsed in the harness process, never imported or run.

- After each edit, the tool result lists the new problems compared with the start of the run,
  and says when they are fixed again. The edit itself is kept.
- The agent can run it as `run_check lint`.
- `[verification] lint = true` adds a final check of kind `quality`; a failure blocks VERIFIED.

Line-length and similar style rules are left out: they say little about whether a fix is sloppy.
Lint cannot see everything; a duplicated assignment, for example, passes.

Tests: `tests/test_lint.py`.

## Loop detection (`controller.py`)

Repeats are counted over the whole run (review rounds included), not just consecutively:

- A read-only request (read, search, list, check) is a repeat when it is identical and the
  repository content is the same as before. The state is a fingerprint of the content of every
  file written in the run (`RepositoryTools.state()`); a file restored to its original content
  counts as unchanged. This catches cycles such as read → failing edit → read and edit → undo,
  while re-reading a file after a real edit is not a repeat.
- A write request (`edit_file`, `write_file`) is a repeat when it is identical, in any state.
  Applying the same edit again only works if the last one recreated `old_text`, which made a 7B
  run grow the file in a loop.

From the second time on, the tool result says that repeating will not help. Above
`max_repeated_actions` the run stops with `repeat_limit`.

The state is built from the files the harness tools wrote. That holds here (the repository is
mounted read-only in the sandbox), but a check that wrote files would not be seen.

Tests: `tests/test_limits.py`, from `test_identical_requests_are_stopped_as_a_loop` to
`test_reading_again_after_an_edit_is_not_a_repeat`.

## Second-model review (`review.py`, optional)

Enable with `[review] enabled = true` or `--review-model <name>`; `--no-review` turns it off.

- If the agent stopped with `completed` and the diff is not empty, a second model reviews it. It
  sees the task, the scope and the diff, never the agent's own summary, which is where false
  success claims live. It has no tools and answers with a JSON verdict.
- If it requests changes, its findings go back to the agent as a new message in the same
  conversation, at most `max_rounds` times. The agent keeps its tools and its step and action
  budget; nothing is reset (`AgentController.continue_task`).
- The final diff is reviewed once more; that verdict is shown and kept in `trace.json`.
- The review is advisory. The reviewer is a model too: an unreachable or unreadable reviewer
  counts as unavailable, not approved, and only the final checks decide VERIFIED.

Failed final checks (lint, regression) are not sent back to the agent; only the user sees them.

Tests: `tests/test_review.py`.
