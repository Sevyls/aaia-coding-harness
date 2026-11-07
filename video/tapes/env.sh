# Sourced (hidden) at the start of every clip. VHS types keystrokes, so non-ASCII stays in this file.
export PS1='aaia$ ' LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8  # UTF-8, or grep matches box characters bytewise
# checks: keep the check table, the test counts and the verdict of a report.
checks() { grep -E 'Checks|┃|│ [a-z_]+ +│|^[┏┡└]|[0-9]+ (failed|passed)(,| in)|VERIFIED'; }
# S: the newest session (the run in clip 2 is the last one started).
S=$(ls .harness/runs | sort | tail -1)
# cancel_at_step N: in the background, wait for the next session that starts, until it requests
# step N from the model, then cancel it the way a second terminal would (coding-harness cancel <id>).
cancel_at_step() {
  local before; before=$(ls .harness/runs | sort | tail -1)
  ( while :; do
      new=$(ls .harness/runs | sort | tail -1)
      [ "$new" != "$before" ] && grep -q "\"kind\": \"model_request\", \"step\": $1," ".harness/runs/$new/events.jsonl" 2>/dev/null && break
      sleep 1
    done
    sleep 4
    uv run coding-harness cancel "$new" ) >/dev/null 2>&1 &
  disown
}
# tool_output NAME: the model's NAME tool calls in session $S and what the tool returned (from events.jsonl).
tool_output() {
  python3 - ".harness/runs/$S/events.jsonl" "$1" <<'PY'
import json, sys
for line in open(sys.argv[1]):
    event = json.loads(line)
    data = event["data"]
    if data.get("name") == sys.argv[2] and event["kind"] == "tool_call":
        print(f"step {event['step']}: the model calls {data['name']} {json.dumps(data['arguments'])}")
    if data.get("name") == sys.argv[2] and event["kind"] == "tool_result":
        print("\n".join("  " + l for l in data["content"].splitlines()))
PY
}
# session_start: what session $S saved when it started: the task (task.json) and the starting
# context the harness selected for the model (the third message in checkpoint.json).
session_start() {
  python3 - ".harness/runs/$S" <<'PY'
import json, sys
run = sys.argv[1]
record = json.load(open(f"{run}/task.json"))
task = record["task"]
print(f"task.json  project {record['project']} @ {record['commit'][:12]}   created {record['created']}")
print(f"           allowed_paths {task['allowed_paths']}   max_steps {record['limits']['max_steps']}")
print(f"           acceptance {' '.join(task['acceptance']['acceptance'][-1:])} (hidden from the agent)")
print(f"           regression {', '.join(task['regression'])}")
context = json.load(open(f"{run}/checkpoint.json"))["messages"][2]["content"]
print(f"\ncheckpoint.json, message 3: the starting context ({len(context):,} characters)")
lines = context.splitlines()
for line in lines[:14]:
    print("  " + line)
print("  ...")
for line in lines[14:]:
    if line.startswith("<untrusted source="):
        print("  " + line)
PY
}
