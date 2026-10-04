"""Run from the selected isolated upstream environment; imports only the requested backend."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    parser = argparse.ArgumentParser(description="Owner-managed offline critter authoring worker")
    commands = parser.add_subparsers(dest="kind", required=True)
    skin = commands.add_parser("skin")
    skin.add_argument("--job", required=True, type=Path)
    skin.add_argument("--out", required=True, type=Path)
    skin.add_argument("--checkpoint", type=Path)
    skin.add_argument("--vae-checkpoint", type=Path)
    motion = commands.add_parser("motion")
    actions = motion.add_subparsers(dest="action", required=True)
    train = actions.add_parser("train")
    train.add_argument("--job", required=True, type=Path)
    train.add_argument("--out", required=True, type=Path)
    train.add_argument("--device", default="cuda")
    train.add_argument("--check-only", action="store_true")
    infer = actions.add_parser("infer")
    infer.add_argument("--model", required=True, type=Path)
    infer.add_argument("--job", required=True, type=Path)
    infer.add_argument("--seed", type=int, default=1)
    infer.add_argument("--out", required=True, type=Path)
    infer.add_argument("--device", default="cuda")
    args = parser.parse_args()
    try:
        if args.kind == "skin":
            from skin import run
        elif args.action == "train":
            from motion_train import run
        else:
            from motion_infer import run
        run(args)
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
