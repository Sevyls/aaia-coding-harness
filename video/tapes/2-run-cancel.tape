# Clip 2: a live run, cancelled from a helper (coding-harness cancel) during the step-6 model request.
Output video/clips/2-run-cancel.mp4
Source video/tapes/settings.tape

Type "echo '=== Live run with qwen3.8:27b. A helper of the recording script runs coding-harness cancel when step 6 starts ==='" Enter
Hide
Type "cancel_at_step 6" Enter
Show
Type "uv run coding-harness run --task-file tasks/cancel-allocation.toml" Enter
Wait
Sleep 4s
Type "clear; S=$(ls .harness/runs | sort | tail -1); uv run coding-harness status $S | tail -12" Enter
Wait
Type "git -C .harness/runs/$S/repo status --short   # the harness's own edits so far" Enter
Wait
Sleep 6s
