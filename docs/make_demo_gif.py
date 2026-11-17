"""Builds docs/demo.gif from the output of `cleanup.py demo` (made up inbox).

    python3 docs/make_demo_gif.py        (needs Pillow and a monospace font)
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ["/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
font = next(ImageFont.truetype(f, 15) for f in FONTS if Path(f).exists())
W, H, PAD, LH = 980, 620, 22, 21
BG, FG, DIM, GREEN, AMBER, BLUE = "#14161a", "#d7dae0", "#7d8590", "#7ee787", "#e3b341", "#79c0ff"

out = subprocess.run([sys.executable, str(ROOT / "scripts" / "cleanup.py"), "demo", "--top", "4"],
                     capture_output=True, text=True, check=True).stdout.splitlines()
out = [l.replace("**", "") for l in out if not l.startswith("|---")]
cmd = "$ python3 scripts/cleanup.py demo"


def colour(line):
    if line.startswith("#"):
        return BLUE
    if line.startswith("To trash") or line.startswith("To unsubscribe") or line.startswith("To archive"):
        return GREEN
    if line.startswith("Needs your call"):
        return AMBER
    if line.startswith("| Emails") or line.startswith("- "):
        return DIM
    return FG


def frame(typed, lines):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse((PAD + i * 20, 14, PAD + i * 20 + 12, 26), fill=c)
    rows = (H - 48 - PAD) // LH
    visible = ([cmd[:typed]] + lines)[-rows:]
    for i, line in enumerate(visible):
        d.text((PAD, 44 + i * LH), line[:108], font=font, fill=GREEN if line.startswith("$") else colour(line))
    return img.quantize(colors=32)


frames, times = [], []
for n in range(2, len(cmd) + 1, 2):
    frames.append(frame(n, [])); times.append(45)
frames.append(frame(len(cmd), [])); times.append(500)
for n in range(1, len(out) + 1):
    frames.append(frame(len(cmd), out[:n])); times.append(1400 if out[n - 1].startswith("## ") else 95)
times[-1] = 4500
frames[0].save(ROOT / "docs" / "demo.gif", save_all=True, append_images=frames[1:],
               duration=times, loop=0, optimize=True)
print("wrote docs/demo.gif", len(frames), "frames")
