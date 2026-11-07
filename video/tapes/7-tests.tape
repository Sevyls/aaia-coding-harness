# Clip 7: what the recorded run did not exercise, shown by the harness tests (scripted model replies).
Output video/clips/7-tests.mp4
Source video/tapes/settings.tape

Type "echo '=== Harness tests with scripted model replies, no model, no container: fix loop, safe resume, context ==='" Enter
Type "uv run pytest tests/test_fix_loop.py tests/test_resume.py tests/test_context.py -v 2>&1 | grep -E 'PASSED|FAILED|passed'" Enter
Wait
Sleep 8s
