"""Combine LocomotionCapture frame folders into GIFs (30 fps), one per skeleton and speed.

Usage: uv run python tools/frames_to_gif.py work/review/locomotion
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

root = Path(sys.argv[1])
for skeleton_dir in sorted({p.parent for p in root.glob("*/frame_0000.png")}):
    frames = sorted(skeleton_dir.glob("frame_*.png"))
    metrics = json.loads((skeleton_dir / "metrics.json").read_text()) if (skeleton_dir / "metrics.json").exists() else {}
    label = f"{metrics.get('skeleton_id', skeleton_dir.name)}  {metrics.get('speed_mps', 0):.2f} m/s  {metrics.get('cadence_hz', 0):.2f} Hz"
    images = []
    for f in frames:
        im = Image.open(f).convert("RGB")
        ImageDraw.Draw(im).text((6, 4), label, fill=(230, 230, 230))
        images.append(im.convert("P", palette=Image.ADAPTIVE, colors=128))
    out = root / f"{skeleton_dir.name}.gif"
    images[0].save(out, save_all=True, append_images=images[1:], duration=33, loop=0, optimize=True)
    print(out, len(images), f"{out.stat().st_size/1e6:.2f} MB")
