"""Render slides.toml into slides/slideN.png (1920x1080). Usage: make_slides.py [slides.toml]"""
import sys
import tomllib
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
CONTENT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "slides.toml"
WORK = HERE / "slides"
W, H = 1920, 1080

BG, PANEL, BORDER = "#0d1117", "#161b22", "#30363d"
FG, DIM, ACCENT = "#e6edf3", "#8b949e", "#2f81f7"
COLORS = {"r": "#ff7b72", "g": "#7ee787", "d": "#8b949e", "b": "#79c0ff", "y": "#e3b341"}
SANS = "/System/Library/Fonts/SFNS.ttf"
MONO = "/System/Library/Fonts/SFNSMono.ttf"
AUTHOR = "Author: Michael Auß (se25m055@technikum-wien.at)  ·  AI usage: Claude Opus 5.5"


def font(path, size):
    return ImageFont.truetype(path, size)


def draw_line(draw, x, y, raw, mono):
    """Draw one line; '!x' switches colour for the rest of the segment."""
    color = FG
    for i, part in enumerate(raw.split("!")):
        if i and part[:1] in COLORS:
            color, part = COLORS[part[0]], part[1:]
        elif i:
            part = "!" + part
        draw.text((x, y), part, font=mono, fill=color)
        x += draw.textlength(part, font=mono)


def render(n, slide, note=""):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=ACCENT)
    d.text((70, 40), f"{slide['section']}  ·  {slide['time']}", font=font(SANS, 30), fill=DIM)
    d.text((70, 88), slide["title"], font=font(SANS, 54), fill=FG)
    d.rounded_rectangle([60, 172, W - 60, 950], radius=14, fill=PANEL, outline=BORDER, width=2)
    mono = font(MONO, slide["size"])
    step = int(slide["size"] * 1.45)
    lines = slide["lines"].removesuffix("\n").split("\n")
    if 200 + len(lines) * step > 950:
        print(f"warning: slide {n} code panel overflows; reduce lines or size", file=sys.stderr)
    for i, line in enumerate(lines):
        draw_line(d, 94, 200 + i * step, line, mono)
    d.text((70, 975), slide["caption"], font=font(SANS, 32), fill=FG)
    if slide.get("author"):
        d.text((70, 1025), AUTHOR, font=font(SANS, 26), fill=DIM)
    if note:
        small = font(SANS, 26)
        d.text((W - 70 - d.textlength(note, font=small), 1025), note, font=small, fill=DIM)
    path = WORK / f"slide{n}.png"
    img.save(path)
    return path


if __name__ == "__main__":
    WORK.mkdir(exist_ok=True)
    for old in WORK.glob("slide*.png"):
        old.unlink()
    content = tomllib.loads(CONTENT.read_text(encoding="utf-8"))
    for n, slide in enumerate(content["slide"], 1):
        print(render(n, slide, content.get("audio_note", "")))
