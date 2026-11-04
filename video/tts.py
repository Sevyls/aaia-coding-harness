"""Kokoro (local neural TTS) narration helper shared by make_audio.py and make_demo.py."""
import shutil
import subprocess
import urllib.request
from pathlib import Path

import espeakng_loader
import soundfile as sf
from kokoro_onnx import EspeakConfig, Kokoro

HERE = Path(__file__).parent
VOICE, SPEED = "af_heart", 1.0
MODELS = HERE / "kokoro"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
    if not (MODELS / name).exists():
        MODELS.mkdir(exist_ok=True)
        print(f"downloading {name} ...")
        urllib.request.urlretrieve(f"{RELEASE}/{name}", MODELS / name)
# ponytail: espeak-ng truncates data paths at 160 bytes, so its data is copied to a short /tmp path.
ESPEAK_DATA = Path("/tmp/espeak-ng-data")
if not (ESPEAK_DATA / "phontab").exists():
    shutil.copytree(espeakng_loader.get_data_path(), ESPEAK_DATA, dirs_exist_ok=True)
TTS = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"),
             espeak_config=EspeakConfig(lib_path=espeakng_loader.get_library_path(),
                                        data_path=str(ESPEAK_DATA)))


def duration(path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout)


def narrate(text: str, out: Path, seconds: float) -> float:
    """Speak text into out (48 kHz mono WAV) padded to exactly `seconds`; return speech length."""
    raw = out.with_name(out.stem + "-raw.wav")
    samples, rate = TTS.create(text, voice=VOICE, speed=SPEED, lang="en-us")
    sf.write(raw, samples, rate)
    length = duration(raw)
    if length > seconds - 0.5:
        raise SystemExit(f"{out.name}: narration {length:.1f}s does not fit {seconds}s; shorten the text")
    # 0.5 s lead-in, then pad with silence to exactly `seconds`.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af",
                    f"adelay=500:all=1,apad,atrim=0:{seconds}", "-ar", "48000", "-ac", "1", str(out)], check=True)
    return length
