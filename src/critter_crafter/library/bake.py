"""Portable, validated authoring-time creature bakes."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from ..blender.runner import run_op
from ..recipes.generator import canonical
from ..recipes.validate import validate_recipe

SETTINGS = {"atlas_size": 4096, "gutter_px": 32, "opaque": True, "roughness": 0.75}
SCHEMA = "baked-creatures-1"


def compact(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def confined(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or "\\" in relative or ":" in relative or "\0" in relative:
        raise ValueError(f"CC_BAKE_PATH: {relative!r}")
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise ValueError(f"CC_BAKE_PATH: {relative!r}")
    candidate = root / relative
    for ancestor in [candidate, *candidate.parents]:
        if ancestor == root.parent:
            break
        if ancestor.is_symlink() or ancestor.is_junction() or (
            ancestor.exists() and getattr(ancestor.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError(f"CC_BAKE_PATH: linked dependency {relative!r}")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"CC_BAKE_PATH: {relative!r}")
    return resolved


def recipe_key(recipe: dict[str, Any]) -> str:
    return digest((recipe["library_id"] + "\n" + recipe["library_version"] + "\n" + canonical(recipe)).encode("utf-8"))


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def producer_sha256() -> str:
    source = Path(__file__).resolve().parents[1]
    paths = ["library/bake.py", "blender/ops_creaturebake.py", "blender/ops_assemble.py",
             "blender/ops_skeleton.py", "blender/rigkit.py", "blender/frame.py"]
    return digest(compact({"settings": SETTINGS, "code": [
        {"path": path, "sha256": digest((source / path).read_bytes())} for path in paths]}))


def _inputs(root: Path, names: set[str]) -> list[dict[str, str]]:
    return [{"path": name, "sha256": digest(confined(root, name).read_bytes())} for name in sorted(names)]


def _asset_paths(asset: dict[str, Any]) -> set[str]:
    return {asset[key] for key in ("fbx", "blend", "motion", "albedo_png") if asset.get(key)}


def _read_index(root: Path, catalog: dict, catalog_hash: str) -> dict:
    path = confined(root, "baked_creatures/index.json")
    if not path.exists():
        return {"schema_version": SCHEMA, "library_id": catalog["library_id"],
                "library_version": catalog["version"], "source_catalog_sha256": catalog_hash, "entries": []}
    index = json.loads(path.read_bytes())
    if index.get("schema_version") != SCHEMA or index.get("library_id") != catalog["library_id"] or index.get("library_version") != catalog["version"]:
        raise ValueError("CC_BAKE_INDEX: incompatible library/index identity")
    if not isinstance(index.get("entries"), list):
        raise ValueError("CC_BAKE_INDEX: entries must be an array")
    def sha(value):
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    if not sha(index.get("source_catalog_sha256")) or any(not isinstance(e, dict) for e in index["entries"]):
        raise ValueError("CC_BAKE_INDEX: invalid catalog hash/entry shape")
    keys = [entry.get("key") for entry in index["entries"]]
    if any(not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key) for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("CC_BAKE_INDEX: invalid or duplicate keys")
    if keys != sorted(keys):
        raise ValueError("CC_BAKE_INDEX: entries must be sorted")
    for entry in index["entries"]:
        required = {"key", "skeleton_id", "composition", "source_sha256", "producer_sha256",
                    "material_sha256", "asset_sha256", "texture_sha256", "fbx", "albedo_png", "inputs"}
        if set(entry) != required or any(not sha(entry[f]) for f in required if f.endswith("sha256")):
            raise ValueError("CC_BAKE_INDEX: invalid entry fields/hash")
        if not isinstance(entry["composition"], str) or not isinstance(entry["skeleton_id"], str):
            raise ValueError("CC_BAKE_INDEX: invalid composition/skeleton")
        expected_key = digest((index["library_id"] + "\n" + index["library_version"] + "\n" + entry["composition"]).encode("utf-8"))
        if entry["key"] != expected_key:
            raise ValueError("CC_BAKE_INDEX: forged composition identity")
        if not isinstance(entry["inputs"], list) or any(
            not isinstance(row, dict) or set(row) != {"path", "sha256"} or not sha(row["sha256"])
            for row in entry["inputs"]
        ):
            raise ValueError("CC_BAKE_INDEX: invalid input manifest")
        for field in ("fbx", "albedo_png"):
            confined(root, entry[field])
        for row in entry["inputs"]:
            confined(root, row["path"])
    return index


def _fresh(root: Path, entry: dict, expected: dict) -> bool:
    if any(entry.get(key) != value for key, value in expected.items()):
        return False
    for path_field, hash_field in (("fbx", "asset_sha256"), ("albedo_png", "texture_sha256")):
        path = confined(root, entry[path_field])
        if not path.is_file() or digest(path.read_bytes()) != entry.get(hash_field):
            return False
    return True


def index_input_paths(library_dir: Path) -> list[str]:
    """Return every portable indexed dependency/output, rejecting incomplete or stale bakes."""
    root = Path(library_dir).absolute()
    if not confined(root, "baked_creatures/index.json").exists():
        return []
    catalog_bytes = confined(root, "catalog.json").read_bytes()
    index = _read_index(root, json.loads(catalog_bytes), digest(catalog_bytes))
    if index.get("source_catalog_sha256") != digest(catalog_bytes):
        raise ValueError("CC_BAKE_STALE: catalog changed")
    paths = {"baked_creatures/index.json"}
    for entry in index["entries"]:
        key = digest((index["library_id"] + "\n" + index["library_version"] + "\n" + entry["composition"]).encode("utf-8"))
        if key != entry["key"]:
            raise ValueError("CC_BAKE_INDEX: forged composition identity")
        rows = entry["inputs"]
        names = [row["path"] for row in rows]
        if names != sorted(set(names)) or "catalog.json" not in names:
            raise ValueError("CC_BAKE_INDEX: invalid dependency manifest")
        if _inputs(root, set(names)) != rows or digest(compact(rows)) != entry["source_sha256"]:
            raise ValueError("CC_BAKE_STALE: source dependency changed")
        material = f"baked_creatures/{key}/material.json"
        if material not in names or digest(confined(root, material).read_bytes()) != entry["material_sha256"]:
            raise ValueError("CC_BAKE_STALE: material descriptor changed")
        expected = {"fbx": f"baked_creatures/{key}/{key}.fbx",
                    "albedo_png": f"baked_creatures/{key}/{key}_albedo.png"}
        if not _fresh(root, entry, expected):
            raise ValueError("CC_BAKE_STALE: output changed")
        paths.update(names)
        paths.update(expected.values())
    return sorted(paths)


def bake_recipe(recipe: dict[str, Any], library_dir: Path, *, allow_review: bool = False,
                force: bool = False, proof_dir: Path | None = None) -> dict[str, Any]:
    """Validate, reuse a fresh bake or build it, then atomically publish the index.

    ``library_dir`` is an existing compiled library, not an arbitrary output directory.
    Review recipes follow exactly the existing validator's allow-review policy.
    """
    root = Path(library_dir).absolute()
    catalog_bytes = confined(root, "catalog.json").read_bytes()
    catalog = json.loads(catalog_bytes)
    errors = validate_recipe(catalog, recipe, allow_review=allow_review)
    if errors:
        raise ValueError("\n".join(errors))
    if catalog["library_id"] == "biomass_core" and catalog["version"] == "0.2.0":
        raise ValueError("CC_BAKE_IMMUTABLE: biomass_core 0.2.0 is a frozen published library")
    key = recipe_key(recipe)
    catalog_hash = digest(catalog_bytes)
    index = _read_index(root, catalog, catalog_hash)
    skeleton = next(s for s in catalog["skeletons"] if s["skeleton_id"] == recipe["skeleton_id"])
    used = {f["part_id"] for f in recipe["fills"]} | {f["connector_part_id"] for f in recipe["fills"] if f["connector_part_id"]}
    parts = {p["part_id"]: p for p in catalog["parts"] if p["part_id"] in used}
    names = {"catalog.json"} | _asset_paths(skeleton["asset"])
    for part in parts.values():
        names.update(_asset_paths(part["asset"]))
    initial_inputs = _inputs(root, names)
    args = {"skeleton": skeleton, "parts": parts, "recipe": recipe, "library_dir": str(root),
            "gait_profile": next(g for g in catalog["gait_profiles"] if g["hint"] == skeleton["locomotion_hint"]),
            "settings": SETTINGS}
    if proof_dir is not None:
        args["proof_dir"] = str(Path(proof_dir).resolve())
    inspected = run_op("creaturebake", {**args, "inspect": True})["result"]
    for relative in inspected["texture_paths"]:
        confined(root, relative)
        names.add(relative)
    material_bytes = compact({"settings": SETTINGS, "sources": inspected["materials"]})
    material_path = f"baked_creatures/{key}/material.json"
    material_hash = digest(material_bytes)
    rows = _inputs(root, names) + [{"path": material_path, "sha256": material_hash}]
    rows.sort(key=lambda row: row["path"])
    expected = {"key": key, "skeleton_id": skeleton["skeleton_id"], "composition": canonical(recipe),
                "source_sha256": digest(compact(rows)), "producer_sha256": producer_sha256(),
                "material_sha256": material_hash, "inputs": rows,
                "fbx": f"baked_creatures/{key}/{key}.fbx",
                "albedo_png": f"baked_creatures/{key}/{key}_albedo.png"}
    current = next((entry for entry in index["entries"] if entry["key"] == key), None)
    descriptor = confined(root, material_path)
    if not force and proof_dir is None and current and _fresh(root, current, expected) and descriptor.is_file() and digest(descriptor.read_bytes()) == material_hash:
        return current
    target = confined(root, f"baked_creatures/{key}")
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".bake-", dir=target) as staging:
        stage = Path(staging)
        result = run_op("creaturebake", {**args, "out_fbx": str(stage / f"{key}.fbx"),
                                        "out_png": str(stage / f"{key}_albedo.png"),
                                        "expected_material_sha256": digest(compact(inspected["materials"]))})["result"]
        if result["triangles"] > 30000 or result["bones"] > 120 or result["materials"] != 1 or result["max_influences"] > 4:
            raise ValueError("CC_BAKE_BUDGET: invalid optimized mesh")
        if _inputs(root, {row["path"] for row in initial_inputs}) != initial_inputs or _inputs(root, names) != [row for row in rows if row["path"] != material_path]:
            raise ValueError("CC_BAKE_STALE: inputs changed during bake")
        entry = {**expected, "asset_sha256": digest((stage / f"{key}.fbx").read_bytes()),
                 "texture_sha256": digest((stage / f"{key}_albedo.png").read_bytes())}
        for field in ("fbx", "albedo_png"):
            os.replace(stage / Path(entry[field]).name, confined(root, entry[field]))
    _atomic(descriptor, material_bytes)
    index["entries"] = sorted([e for e in index["entries"] if e["key"] != key] + [entry], key=lambda e: e["key"])
    # Never turn another indexed composition's stale asset into a live-assembly miss.
    # Retain the old global identity while repairing a catalog change composition by composition:
    # import/pack remain fail-closed until every entry records the current exact catalog bytes.
    if all(any(row["path"] == "catalog.json" and row["sha256"] == catalog_hash
               for row in e["inputs"]) for e in index["entries"]):
        index["source_catalog_sha256"] = catalog_hash
    _atomic(confined(root, "baked_creatures/index.json"), compact(index))
    return entry
