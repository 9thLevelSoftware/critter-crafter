"""Repository paths and external tool discovery (Windows-first, no hard-coded user paths)."""

from __future__ import annotations

import glob
import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "data").is_dir():
            return parent
    return Path.cwd()


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def schemas(self) -> Path:
        return self.root / "schemas"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def library_out(self) -> Path:
        return self.root / "library"

    @property
    def blender_ops(self) -> Path:
        return Path(__file__).resolve().parent / "blender"


def paths() -> Paths:
    return Paths(repo_root())


def _toml() -> dict:
    p = repo_root() / "critter.toml"
    if p.exists():
        with p.open("rb") as f:
            return tomllib.load(f)
    return {}


def find_blender() -> str | None:
    """CRITTER_BLENDER env -> critter.toml [tools].blender -> Program Files Blender 5.* -> PATH."""
    env = os.environ.get("CRITTER_BLENDER")
    if env and Path(env).exists():
        return env
    cfg = _toml().get("tools", {}).get("blender")
    if cfg and Path(cfg).exists():
        return cfg
    candidates = glob.glob(r"C:\Program Files\Blender Foundation\Blender 5.*\blender.exe")
    if candidates:
        def ver(p: str) -> tuple[int, ...]:
            name = Path(p).parent.name.split(" ")[-1]
            return tuple(int(x) for x in name.split(".") if x.isdigit())
        return max(candidates, key=ver)
    return shutil.which("blender")


def find_unity() -> str | None:
    env = os.environ.get("CRITTER_UNITY")
    if env and Path(env).exists():
        return env
    cfg = _toml().get("tools", {}).get("unity")
    if cfg and Path(cfg).exists():
        return cfg
    bases = [r"C:\Program Files\Unity\Hub\Editor"]
    hub_cfg = Path(os.environ.get("APPDATA", "")) / "UnityHub" / "secondaryInstallPath.json"
    if hub_cfg.exists():
        try:
            import json
            secondary = json.loads(hub_cfg.read_text(encoding="utf-8"))
            if secondary:
                bases.insert(0, secondary)
        except ValueError:
            pass
    for base in bases:
        hits = sorted(glob.glob(os.path.join(base, "6000.*", "Editor", "Unity.exe")))
        if hits:
            return hits[-1]
    return None
