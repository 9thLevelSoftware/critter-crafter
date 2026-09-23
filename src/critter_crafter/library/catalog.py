"""Load authoring sources from data/ and compile them into a library.v2 catalog document.

The compiled catalog is the one contract shared with engines. It is JsonUtility-friendly:
arrays instead of maps, no nulls (empty string / empty list instead), every optional
field written explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import mathutil as mu

SCHEMA_VERSION = "2.0.0"
DEFAULT_OPTIONAL_FILL_PCT = 50
ROOT_BONE = "root"


class CatalogError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_sources(data_dir: Path) -> dict[str, Any]:
    """Read every authoring file under data/ (sorted, so output is stable)."""
    return {
        "library": _load(data_dir / "library.json"),
        "gait": _load(data_dir / "gait_profiles.json"),
        "templates": [_load(p) for p in sorted((data_dir / "branch_templates").glob("*.json"))],
        "skeletons": [_load(p) for p in sorted((data_dir / "skeletons").glob("*.skeleton.json"))],
        "parts": [_load(p) for p in sorted((data_dir / "parts").glob("*.part.json"))],
        "pools": _load(data_dir / "pools" / "pools.json")["pools"],
    }


def bone_name(branch_id: str, index: int) -> str:
    return f"{branch_id}_b{index}"


def compile_skeleton(src: dict[str, Any], templates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    sid = src["skeleton_id"]
    bones: list[dict[str, Any]] = [
        {"name": ROOT_BONE, "parent": "", "head_m": [0.0, 0.0, 0.0], "tail_m": [0.0, 0.1, 0.0], "up_m": [0.0, 0.0, 1.0]}
    ]
    branches_out: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for b in src["branches"]:
        bid = b["branch_id"]
        if bid in by_id:
            raise CatalogError("CC_DUP_BRANCH", f"{sid}: duplicate branch {bid}")
        tpl = templates.get(b["template"])
        if tpl is None:
            raise CatalogError("CC_UNKNOWN_TEMPLATE", f"{sid}.{bid}: template {b['template']}")
        parent_id = b.get("parent_branch") or ""
        if parent_id:
            parent = by_id.get(parent_id)
            if parent is None:
                raise CatalogError("CC_PARENT_ORDER", f"{sid}.{bid}: parent {parent_id} must precede it")
            idx = b.get("attach_bone_index", 0)
            names = parent["bone_names"]
            if idx < 0:
                idx = len(names) + idx
            if not 0 <= idx < len(names):
                raise CatalogError("CC_ATTACH_BONE", f"{sid}.{bid}: attach_bone_index out of range")
            attach_bone = names[idx]
        else:
            attach_bone = ROOT_BONE
        if "origin_m" in b:
            origin = tuple(b["origin_m"])
        else:
            attach = next(x for x in bones if x["name"] == attach_bone)
            origin = tuple(attach["tail_m"])
        x, y, z = mu.frame_from_dir_up(b["direction"], b["up"])
        quat = mu.quat_from_basis(x, y, z)
        length = float(b["length_m"])
        names: list[str] = []
        cursor = origin
        for i, frac in enumerate(tpl["bone_fractions"]):
            tail = mu.add(cursor, mu.scale(z, frac * length))
            name = bone_name(bid, i)
            bones.append({
                "name": name,
                "parent": attach_bone if i == 0 else names[-1],
                "head_m": mu.r6v(cursor),
                "tail_m": mu.r6v(tail),
                "up_m": mu.r6v(y),
            })
            names.append(name)
            cursor = tail
        accepts = b.get("accepts", {})
        gait = b.get("gait", {})
        out = {
            "branch_id": bid,
            "template": b["template"],
            "parent_branch": parent_id,
            "attach_bone": attach_bone,
            "bone_names": names,
            "length_m": mu.r6(length),
            "length_mm": mu.mm(length),
            "size_class": b["size_class"],
            "side": b.get("side", "C"),
            "required": bool(b.get("required", False)),
            "optional_fill_pct": int(b.get("optional_fill_pct", 100 if b.get("required") else DEFAULT_OPTIONAL_FILL_PCT)),
            "mirror_of": b.get("mirror_of") or "",
            "accepts": {
                "categories": sorted(accepts.get("categories", [])),
                "templates": sorted(accepts.get("templates", [])),
                "tags_any": sorted(accepts.get("tags_any", [])),
            },
            "connector_size_class": b.get("connector_size_class") or "",
            "gait_role": gait.get("role", "none"),
            "gait_phase_rad": mu.r6(float(gait.get("phase_rad", 0.0))),
            "snap": {"position_m": mu.r6v(origin), "rotation_xyzw": mu.r6v(quat)},
        }
        by_id[bid] = out
        branches_out.append(out)
    for br in branches_out:
        if br["mirror_of"] and br["mirror_of"] not in by_id:
            raise CatalogError("CC_MIRROR", f"{sid}.{br['branch_id']}: mirror_of {br['mirror_of']} unknown")
    return {
        "skeleton_id": sid,
        "family": src["family"],
        "locomotion_hint": src["locomotion_hint"],
        "status": src.get("status", "draft"),
        "symmetry_pct": int(src.get("symmetry_pct", 50)),
        "bones": bones,
        "branches": branches_out,
        "asset": {"fbx": "", "glb": "", "clips": []},
    }


def compile_part(src: dict[str, Any]) -> dict[str, Any]:
    conn = src.get("connector") or {}
    return {
        "part_id": src["part_id"],
        "category": src["category"],
        "template": src["template"],
        "species_tags": sorted(src.get("species_tags", [])),
        "roles": sorted(src.get("roles", [])),
        "size_class": src.get("size_class") or "",
        "status": src.get("status", "draft"),
        "style_profile": src.get("style_profile", ""),
        "dimensions_m": mu.r6v(src["dimensions_m"]),
        "length_m": mu.r6(float(src["length_m"])),
        "length_mm": mu.mm(float(src["length_m"])),
        "max_triangles": int(src["budget"]["max_triangles"]),
        "max_material_slots": int(src["budget"].get("max_material_slots", 2)),
        "connector_radius_m": mu.r6(float(conn.get("radius_m", 0.0))),
        "connector_span_m": mu.r6v(conn.get("span_m", [0.0, 0.0])) if conn else [],
        "fallback_primitive": src.get("fallback", {}).get("primitive", "capsule"),
        "albedo": src.get("fallback", {}).get("albedo", "#a07a80"),
        "source": src.get("provenance", {}).get("source", "placeholder"),
        "asset": {"fbx": "", "glb": "", "triangles": 0},
    }


def compile_catalog(sources: dict[str, Any]) -> dict[str, Any]:
    lib = sources["library"]
    templates = {t["template_id"]: t for t in sources["templates"]}
    skeletons = [compile_skeleton(s, templates) for s in sorted(sources["skeletons"], key=lambda s: s["skeleton_id"])]
    parts = [compile_part(p) for p in sorted(sources["parts"], key=lambda p: p["part_id"])]
    pools = [
        {"pool_id": p["pool_id"], "families": sorted(p.get("families", [])), "skeleton_ids": sorted(p.get("skeleton_ids", []))}
        for p in sorted(sources["pools"], key=lambda p: p["pool_id"])
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "document_kind": "critter_library",
        "library_id": lib["library_id"],
        "version": lib["version"],
        "frame": lib["frame"],
        "limits": lib["limits"],
        "generator": lib["generator"],
        "gait_profiles": sources["gait"]["gait_profiles"],
        "branch_templates": [
            {"template_id": t["template_id"], "chain_kind": t["chain_kind"],
             "nominal_length_m": t["nominal_length_m"], "bone_fractions": t["bone_fractions"]}
            for t in sorted(sources["templates"], key=lambda t: t["template_id"])
        ],
        "skeletons": skeletons,
        "parts": parts,
        "pools": pools,
    }


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=True) + "\n"
