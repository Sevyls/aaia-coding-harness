# Clip 6: the navigation call that found the trap, and the model's new event and handlers.
Output video/clips/6-diff.mp4
Source video/tapes/settings.tape

Type "echo '=== Live: the find_references call, read from the session log, and the new event ==='" Enter
Type "tool_output find_references" Enter
Wait
Type "git --no-pager -C .harness/runs/$S/repo diff -U0 -- src/allocation/domain/events.py" Enter
Wait
Sleep 8s
Type "clear; git --no-pager -C .harness/runs/$S/repo diff -U0 -- src/allocation/service_layer/handlers.py" Enter
Wait
Sleep 10s
