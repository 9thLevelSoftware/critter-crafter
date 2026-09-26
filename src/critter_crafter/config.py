"""Repository paths and external tool discovery (Windows-first, no hard-coded user paths)."""

from __future__ import annotations

import glob
import hashlib
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


ARCHIVE_NAME = "synaptic-sea-asset-archive"


def find_asset_archive() -> Path | None:
    """Root of the private binary asset archive that real parts are fitted from.

    CRITTER_ASSET_ARCHIVE env -> critter.toml [paths].asset_archive -> a sibling clone named
    ``synaptic-sea-asset-archive`` next to this repository. Meshy outputs there may be paid-private
    licensed, so they are read in place and never copied into this (public) repository.
    """
    env = os.environ.get("CRITTER_ASSET_ARCHIVE")
    if env and Path(env).is_dir():
        return Path(env)
    cfg = _toml().get("paths", {}).get("asset_archive")
    if cfg and Path(cfg).is_dir():
        return Path(cfg)
    sibling = repo_root().parent / ARCHIVE_NAME
    return sibling if sibling.is_dir() else None


_VALIDATOR_MISSING = (
    "CC_GLTF_VALIDATOR_MISSING: Khronos glTF-Validator binary not found; "
    "GLB Errors are not fail-closed. Pin [tools].gltf_validator and "
    "gltf_validator_sha256 in critter.toml or set CRITTER_GLTF_VALIDATOR."
)
_VALIDATOR_SCRIPTS = {".cmd", ".bat", ".com"}


def _configured_gltf_validator() -> str | None:
    env = os.environ.get("CRITTER_GLTF_VALIDATOR")
    if env and str(env).strip():
        return str(env).strip()
    cfg = _toml().get("tools", {}).get("gltf_validator")
    if cfg and str(cfg).strip():
        return str(cfg).strip()
    return None


def _validator_executable(value: str) -> tuple[str | None, str | None]:
    """Resolve a configured native CLI. Never PATH, never .., never .cmd/.bat/.com."""
    raw = Path(value)
    if ".." in raw.parts:
        return None, "CC_GLTF_VALIDATOR: path escapes"
    path = raw if raw.is_absolute() else repo_root() / raw
    try:
        path = path.resolve()
    except OSError:
        return None, "CC_GLTF_VALIDATOR: invalid tool path"
    suffix = path.suffix.lower()
    if suffix in _VALIDATOR_SCRIPTS:
        return None, f"CC_GLTF_VALIDATOR: refused {suffix} executable"
    if os.name == "nt":
        if suffix != ".exe":
            return None, "CC_GLTF_VALIDATOR: validator must be a .exe"
    elif suffix:
        return None, "CC_GLTF_VALIDATOR: validator must have no extension"
    if not path.is_file():
        return None, None
    return str(path), None


def find_gltf_validator() -> str | None:
    """CRITTER_GLTF_VALIDATOR -> critter.toml [tools].gltf_validator. No PATH lookup."""
    configured = _configured_gltf_validator()
    if configured is None:
        return None
    path, _error = _validator_executable(configured)
    return path


def gltf_validator_expected_sha256() -> str | None:
    value = _toml().get("tools", {}).get("gltf_validator_sha256")
    if not value:
        return None
    return str(value).strip().lower()


def resolve_gltf_validator() -> tuple[str | None, str | None]:
    """Return (binary, diagnostic).

    Missing tool: (None, CC_GLTF_VALIDATOR_MISSING) — caller warns and still builds.
    Hash mismatch or missing pin: (None, CC_GLTF_VALIDATOR_HASH) — fail the build.
    Unsafe path: (None, CC_GLTF_VALIDATOR: ...) — fail the build, do not run.
    A binary is returned only when the SHA-256 pin matches.
    """
    configured = _configured_gltf_validator()
    if configured is None:
        return None, _VALIDATOR_MISSING
    path, error = _validator_executable(configured)
    if error:
        return None, error
    if path is None:
        return None, _VALIDATOR_MISSING
    expected = gltf_validator_expected_sha256()
    if not expected:
        return None, "CC_GLTF_VALIDATOR_HASH: pin [tools].gltf_validator_sha256 to run the validator"
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest != expected:
        return None, f"CC_GLTF_VALIDATOR_HASH: {path}: {digest} != {expected}"
    return path, None


def export_validator_problems() -> tuple[str | None, list[str], list[str]]:
    """Same gate library build uses: (tool, problems, warnings). HASH/unsafe fail; MISSING warns."""
    tool, status = resolve_gltf_validator()
    if status is None:
        return tool, [], []
    if status.startswith("CC_GLTF_VALIDATOR_MISSING"):
        return None, [], [status]
    return None, [status], []
