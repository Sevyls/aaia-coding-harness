# Clip 5: the saved result of the session and the diff summary.
Output video/clips/5-result.mp4
Source video/tapes/settings.tape

Type "echo '=== Live: the saved result, read back from the session (filtered to the check table) ==='" Enter
Type "uv run coding-harness show $S 2>&1 | checks" Enter
Wait
Type "git -C .harness/runs/$S/repo diff --stat" Enter
Wait
Sleep 6s
