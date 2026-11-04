#!/usr/bin/env bash
# Rebuild the Stage 2 demo video (video/stage2-demo.mp4, at most 4:00).
#   video/build.sh         slides and the final video from the clips already in video/clips/
#   video/build.sh --live  first re-record the live clips with VHS: a real run with qwen3.8:27b,
#                          cancelled with Ctrl+C, a refused resume, a resume to the end (several minutes)
# Needs python3.12 and ffmpeg; --live also needs vhs (brew install vhs), Ollama with qwen3.8:27b and the
# Podman machine. The Kokoro TTS model (~350 MB) is downloaded into the git-ignored video/kokoro/.
set -euo pipefail
cd "$(dirname "$0")"

[ -x .venv/bin/python ] || python3.12 -m venv .venv
.venv/bin/python -m pip install -q -r requirements.txt

.venv/bin/python make_slides.py          # slides.toml -> slides/slideN.png

if [ "${1:-}" = "--live" ]; then
  command -v vhs >/dev/null || { echo "vhs not found: brew install vhs" >&2; exit 1; }
  mkdir -p clips
  # The tapes run from the repository root; clips 3-8 act on the session that clip 2 starts.
  (cd .. && for t in 1-baseline 2-run-cancel 3-refused 4-resume 5-result 6-diff 7-tests 8-context; do vhs "video/tapes/$t.tape"; done)
fi

.venv/bin/python make_demo.py stage2-demo.mp4
