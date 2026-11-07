# Clip 1: the allowed projects, then the feature check on the untouched starting commit.
Output video/clips/1-baseline.mp4
Source video/tapes/settings.tape

Type "echo '=== Live: allowed projects, and the checks on the starting commit (output filtered to the check table) ==='" Enter
Type "uv run coding-harness projects" Enter
Wait
Type "uv run coding-harness baseline --task-file tasks/cancel-allocation.toml 2>&1 | checks" Enter
Wait
Sleep 6s
