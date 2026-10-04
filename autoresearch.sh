#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd -- "$repo_root"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHASHSEED=0
if command -v uv >/dev/null 2>&1; then
    exec uv run --offline --no-sync python tools/benchmark_recipes.py
fi
exec uv.exe run --offline --no-sync python tools/benchmark_recipes.py
