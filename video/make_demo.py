"""Assemble the Stage 2 demo: slides plus live VHS clips, each with Kokoro narration, at most 4:00.

Usage: make_demo.py OUT.mp4   (run make_slides.py and the tapes in video/tapes/ first)
A live clip is sped up or slowed (down to 0.5x) to end HOLD seconds before its slot, then holds its last frame.
Adapted from the framework-agent lab's video build.
"""
import subprocess
import sys
from pathlib import Path

from tts import duration, narrate

HERE = Path(__file__).parent
WORK = HERE / "demo"
LIMIT = 240  # seconds, the handout's maximum
HOLD = 1.5  # seconds each clip holds its last frame, so its final output stays readable

INTRO = (
    "This is Stage 2 of my coding harness. A local Qwen model works on a disposable copy of the "
    "Cosmic Python repository, through validated tools. New in Stage 2: navigation tools, a bounded "
    "context, an edit, test, fix loop, saved sessions with cancel and safe resume, and task files that "
    "carry their own checks."
)
TASK = (
    "The feature: cancel an allocation, across the domain model, the commands, the handlers and the "
    "Flask API. The task names the interface, not the files: the agent may change anything under "
    "source allocation, but no tests. The trap is the existing Deallocated event, which hands the stock "
    "out again. The task is always sent in full; repository text is marked as untrusted."
)
BASELINE = (
    "Only configured projects can be selected. On the untouched starting commit, the hidden acceptance "
    "check fails, eight failed and one passed, while the unit, integration and build checks pass."
)
RUN_CANCEL = (
    "A live run with Qwen 3.8, 27 B, shown about five times faster. The model reads the events, the "
    "Flask app and the message bus, then edits the commands. One edit to the domain model is rejected "
    "by a guardrail, because it uses a name that is not defined yet, so the model adds that exception "
    "first. When step six starts, a helper of the recording script runs coding harness cancel. "
    "The run stops at once: no further action, no final checks. Status shows six of thirty steps used, "
    "and git shows the harness's own edits."
)
CONTEXT = (
    "The session also saved its task, with the exact commit and the allowed paths, and the starting "
    "context the harness chose: a repository map and the three files that share the most words with the "
    "task, all marked untrusted."
)
SESSIONS = (
    "Every session keeps the frozen task, a checkpoint before and after every action, and a write-ahead "
    "event log. Resume checks four things before it runs anything: the project and its checks, the "
    "starting commit, the working copy's fingerprint, and the allowed paths. A mismatch pauses the "
    "session, and interrupted actions are never replayed."
)
REFUSED = (
    "Now the recording script edits a file in the session copy by hand. Resume refuses: paused, nothing "
    "executed, and it says why. Then the hand edit is undone."
)
RESUME = (
    "Resume again, now that the copy matches. The run continues at step seven, and the six used steps "
    "still count. The model looks up where Deallocated is used, adds its own event and the endpoint, and "
    "calls finish. The harness then runs the unit tests and lint itself. Both pass, so no fix round is "
    "needed. Shown about fourteen times faster."
)
RESULT = (
    "Read back from the saved session: acceptance, unit, integration and build checks, lint and scope "
    "all pass. Verified, with five files changed."
)
DIFF = (
    "This is what the model found and built. At step ten it called find references for Deallocated, and "
    "the result shows the trap: the handlers map that event to reallocate. So it added its own event, "
    "Deallocated Stock, a deallocate handler that rejects an unknown product, and a read model handler "
    "for the new event only."
)
TESTS = (
    "What this run did not exercise is covered by the harness tests, with scripted model replies and no "
    "container: a failed check guides the next edit, fix rounds are bounded, interrupted actions are never "
    "replayed, mismatches pause, and the context stays within its limit, with the task kept verbatim."
)
CLOSE = (
    "Results with the real model: the feature task without file names was verified three times, "
    "including this recorded session, and both Stage 1 bug fixes still pass. Two earlier runs needed "
    "manual help, a fix in the check's test double and an unloaded model; both are documented. Not shown "
    "by real runs: history compression, and navigation beyond one call."
)

SEGMENTS = [  # (source relative to video/, seconds, narration)
    ("slides/slide1.png", 22, INTRO),
    ("slides/slide2.png", 24, TASK),
    ("clips/1-baseline.mp4", 12, BASELINE),
    ("clips/2-run-cancel.mp4", 36, RUN_CANCEL),
    ("clips/8-context.mp4", 15, CONTEXT),
    ("slides/slide3.png", 21, SESSIONS),
    ("clips/3-refused.mp4", 11, REFUSED),
    ("clips/4-resume.mp4", 22, RESUME),
    ("clips/5-result.mp4", 10, RESULT),
    ("clips/6-diff.mp4", 22, DIFF),
    ("clips/7-tests.mp4", 20, TESTS),
    ("slides/slide4.png", 23, CLOSE),
]


def segment(n, src, seconds, text):
    wav, out = WORK / f"seg{n}.wav", WORK / f"seg{n}.mp4"
    print(f"segment {n}: narration {narrate(text, wav, seconds):.1f}s / {seconds}s")
    if src.suffix == ".png":
        video = ["-loop", "1", "-framerate", "30", "-i", str(src)]
        vf = "fps=30,format=yuv420p"
    else:
        speed = max(duration(src) / (seconds - HOLD), 0.5)
        print(f"segment {n}: {src.name} {duration(src):.1f}s played at {speed:.2f}x")
        video = ["-i", str(src)]
        vf = f"setpts=PTS/{speed},tpad=stop_mode=clone:stop_duration={seconds},fps=30,scale=1920:1080,format=yuv420p"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *video, "-i", str(wav), "-map", "0:v", "-map", "1:a",
                    "-vf", vf, "-t", str(seconds), "-c:v", "libx264", "-tune", "stillimage",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1", str(out)], check=True)
    return out


if __name__ == "__main__":
    total = sum(seconds for _, seconds, _ in SEGMENTS)
    if total > LIMIT:
        raise SystemExit(f"segments add up to {total}s, more than {LIMIT}s")
    WORK.mkdir(exist_ok=True)
    parts = [segment(n, HERE / src, s, text) for n, (src, s, text) in enumerate(SEGMENTS, 1)]
    listing = WORK / "list.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    out = HERE / sys.argv[1]
    # Normalise the narration to -16 LUFS, the usual level for speech (YouTube only turns loud videos down).
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "128k",
                    "-ar", "48000", str(out)], check=True)
    print(out, f"{duration(out):.1f}s")
