"""Host-side launcher for headless Blender ops (no bpy import)."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..config import find_blender

ENTRY = Path(__file__).resolve().parent / "_entry.py"


class BlenderError(RuntimeError):
    pass


def run_op(op: str, args: dict[str, Any], timeout: int = 1800) -> dict[str, Any]:
    blender = find_blender()
    if not blender:
        raise BlenderError("Blender not found: set CRITTER_BLENDER or install Blender 5.x")
    with tempfile.TemporaryDirectory(prefix="critter_") as tmp:
        a = Path(tmp) / "args.json"
        r = Path(tmp) / "result.json"
        a.write_text(json.dumps(args), encoding="utf-8")
        proc = subprocess.run(
            [blender, "-b", "--factory-startup", "-noaudio", "-P", str(ENTRY), "--", op, str(a), str(r)],
            capture_output=True, text=True, timeout=timeout,
        )
        if not r.exists():
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
            raise BlenderError(f"Blender op {op!r} produced no result (exit {proc.returncode}):\n{tail}")
        result = json.loads(r.read_text(encoding="utf-8"))
    if not result.get("ok"):
        failures = [x for x in result.get("results", [result]) if not x.get("ok")]
        detail = "\n".join(f"- {f.get('op', op)}: {f.get('error')}\n{f.get('trace', '')}" for f in failures)
        raise BlenderError(f"Blender op {op!r} failed:\n{detail}")
    return result


def snippet(op: str, args: dict[str, Any]) -> str:
    """Python for the Blender MCP `execute_blender_code` tool: runs the same op in a live Blender."""
    src = str(Path(__file__).resolve().parents[2]).replace("\\", "/")
    return (
        "import sys, json, importlib\n"
        f"sys.path.insert(0, {src!r}) if {src!r} not in sys.path else None\n"
        "import critter_crafter.blender.rigkit as rk\n"
        "rk.LIVE = True  # build in a new scene; never factory-reset the open file\n"
        f"m = importlib.import_module('critter_crafter.blender.ops_{op}')\n"
        "importlib.reload(m)\n"
        f"result = m.run(json.loads({json.dumps(json.dumps(args))}))\n"
        "print(json.dumps(result))\n"
    )
