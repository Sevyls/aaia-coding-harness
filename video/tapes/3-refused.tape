# Clip 3: a hand edit while the session is cancelled; resume refuses and pauses; the edit is undone.
Output video/clips/3-refused.mp4
Source video/tapes/settings.tape

Type "echo '=== Live: the recording script edits a file in the session copy by hand, then tries to resume ==='" Enter
Type "echo '# hand edit' >> .harness/runs/$S/repo/src/allocation/config.py" Enter
Type "uv run coding-harness resume $S" Enter
Wait
Sleep 5s
Type "git -C .harness/runs/$S/repo checkout -- src/allocation/config.py && echo 'hand edit undone'" Enter
Wait
Sleep 3s
