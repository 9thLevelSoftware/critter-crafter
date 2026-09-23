"""Headless Blender entry point.

    blender -b --factory-startup -P _entry.py -- <op> <args.json> <result.json>

<op> is a module name `ops_<op>` in this package exposing `run(args: dict) -> dict`,
or `batch`, whose args are {"jobs": [{"op": ..., "args": {...}}, ...]}.
The same `run` functions are what `critter blender snippet` calls through the Blender MCP.
"""

import json
import os
import sys
import traceback

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _run_op(op: str, args: dict) -> dict:
    import importlib

    module = importlib.import_module(f"critter_crafter.blender.ops_{op}")
    return module.run(args)


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) != 3:
        print("usage: -- <op> <args.json> <result.json>", file=sys.stderr)
        return 2
    op, args_path, result_path = argv
    with open(args_path, "r", encoding="utf-8") as f:
        args = json.load(f)
    if op == "batch":
        results = []
        for job in args["jobs"]:
            try:
                results.append({"op": job["op"], "ok": True, "result": _run_op(job["op"], job["args"])})
            except Exception as e:  # keep going; report per-job failures
                results.append({"op": job["op"], "ok": False, "error": f"{type(e).__name__}: {e}",
                                "trace": traceback.format_exc()})
        out = {"ok": all(r["ok"] for r in results), "results": results}
    else:
        try:
            out = {"ok": True, "result": _run_op(op, args)}
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
