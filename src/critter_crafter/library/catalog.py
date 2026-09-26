"""Load schema-v3 authoring sources and compile the engine-facing catalog."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .. import mathutil as mu
from ..locomotion.block import locomotion_block
from ..binding.profiles import (
    CONNECTOR_INTERFACE_ID,
    CONNECTOR_INTERFACE_VERSION,
    BindingProfileError,
    index_profiles,
    resolve_profile,
)

SCHEMA_VERSION = "3.0.0"
LIBRARY_VERSION = "0.2.0"
GENERATOR = "cc-gen-3"
DEFAULT_OPTIONAL_FILL_PCT = 50
ROOT_BONE = "root"
REFERENCE_GROUND_CLEARANCE_M = 0.005
CONNECTOR_MAX_TRIANGLES = 800


class CatalogError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _load(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CatalogError("CC_DUP_JSON_KEY", f"{path}:{key}")
            result[key] = value
        return result

    with path.open("r", encoding="utf-8") as f:
        return json.load(f, object_pairs_hook=reject_duplicates)


def _matching(paths: list[Path], version: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for path in paths:
        doc = _load(path)
        if doc.get("schema_version") == version:
            documents.append(doc)
    return documents


def load_sources(data_dir: Path) -> dict[str, Any]:
    """Load only documents matching the version selected by ``library.json``."""
    library = _load(data_dir / "library.json")
    version = str(library.get("schema_version", ""))
    gait: dict[str, Any] = {"schema_version": version, "gait_profiles": []}
    for gait_path in (data_dir / "gait_profiles.v3.json", data_dir / "gait_profiles.json"):
        if gait_path.exists():
            candidate = _load(gait_path)
            if candidate.get("schema_version") == version:
                gait = candidate
                break
    pools_doc = _load(data_dir / "pools" / "pools.json")
    pools = pools_doc.get("pools", []) if pools_doc.get("schema_version") == version else []
    registry_path = data_dir / "binding_profiles" / "registry.json"
    registry_doc = _load(registry_path) if registry_path.exists() else {}
    registry = registry_doc.get("profiles", []) if registry_doc.get("schema_version") == version else []
    baseline_path = data_dir / "binding_profiles" / "released_registry.json"
    baseline_doc = _load(baseline_path) if baseline_path.exists() else {}
    baseline = baseline_doc.get("profiles", []) if baseline_doc.get("schema_version") == version else []
    return {
        "library": library,
        "gait": gait,
        "templates": _matching(sorted((data_dir / "branch_templates").glob("*.json")), version),
        "binding_profiles": _matching(
            sorted((data_dir / "binding_profiles").glob("*.binding.json")), version
        ),
        "binding_profile_registry": registry,
        "binding_profile_baseline": baseline,
        "skeletons": _matching(sorted((data_dir / "skeletons").glob("*.skeleton.json")), version),
        "parts": _matching(sorted((data_dir / "parts").glob("*.part.json")), version),
        "pools": pools,
    }


def bone_name(branch_id: str, index: int) -> str:
    return f"{branch_id}_b{index}"


def _qmul(left: list[float] | tuple[float, ...], right: list[float] | tuple[float, ...]) -> tuple[float, float, float, float]:
    ax, ay, az, aw = left
    bx, by, bz, bw = right
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _near(left: list[float] | tuple[float, ...], right: list[float] | tuple[float, ...], tolerance: float) -> bool:
    return len(left) == len(right) and all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right, strict=True))


def _compile_socket(
    skeleton_id: str,
    source: dict[str, Any],
    parent: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    branch_id = source["branch_id"]
    socket = source["socket"]
    parent_joint = socket["parent_joint"]
    if parent is None:
        if parent_joint != ROOT_BONE:
            raise CatalogError("CC_SOCKET_PARENT", f"{skeleton_id}.{branch_id}: root branch requires parent_joint=root")
        attach_bone = ROOT_BONE
    else:
        try:
            index = parent["joint_order"].index(parent_joint)
        except ValueError as exc:
            raise CatalogError(
                "CC_SOCKET_PARENT",
                f"{skeleton_id}.{branch_id}: {parent_joint} not in {parent['branch_id']} joint_order",
            ) from exc
        attach_bone = parent["bone_names"][index]
    return attach_bone, {
        "parent_joint": parent_joint,
        "position_m": mu.r6v(socket["position_m"]),
        "rotation_xyzw": mu.r6v(socket["rotation_xyzw"]),
    }


def _socket_frame(
    skeleton_id: str,
    source: dict[str, Any],
    parent: dict[str, Any] | None,
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    socket = source["socket"]
    local_position = tuple(float(value) for value in socket["position_m"])
    local_rotation = tuple(float(value) for value in socket["rotation_xyzw"])
    if parent is None:
        origin = local_position
        rotation = local_rotation
    else:
        joint_index = parent["joint_order"].index(socket["parent_joint"])
        parent_rotation = tuple(parent["snap"]["rotation_xyzw"])
        joint_position = mu.add(
            parent["snap"]["position_m"],
            mu.scale(
                mu.quat_rotate(parent_rotation, (0.0, 0.0, 1.0)),
                parent["length_m"] * sum(parent["bone_fractions"][:joint_index]),
            ),
        )
        origin = mu.add(joint_position, mu.quat_rotate(parent_rotation, local_position))
        rotation = _qmul(parent_rotation, local_rotation)
    direction = mu.quat_rotate(rotation, (0.0, 0.0, 1.0))
    up = mu.quat_rotate(rotation, (0.0, 1.0, 0.0))
    if "origin_m" in source and not _near(tuple(source["origin_m"]), origin, 2e-4):
        raise CatalogError("CC_SOCKET_GEOMETRY", f"{skeleton_id}.{source['branch_id']}: origin_m disagrees with socket")
    try:
        _, authored_up, authored_direction = mu.frame_from_dir_up(source["direction"], source["up"])
    except ValueError as exc:
        raise CatalogError("CC_BRANCH_FRAME", f"{skeleton_id}.{source['branch_id']}") from exc
    if not _near(authored_direction, direction, 3e-6) or not _near(authored_up, up, 3e-6):
        raise CatalogError("CC_SOCKET_GEOMETRY", f"{skeleton_id}.{source['branch_id']}: direction/up disagree with socket")
    return origin, rotation


def compile_skeleton(
    src: dict[str, Any],
    templates: dict[str, dict[str, Any]],
    profiles: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile a skeleton while preserving legacy bone/snap fields for consumers."""
    if src.get("schema_version") != SCHEMA_VERSION:
        raise CatalogError("CC_SKELETON_SCHEMA_VERSION", f"{src.get('skeleton_id', '<unknown>')}")
    profiles = profiles or {}
    sid = src["skeleton_id"]
    bones: list[dict[str, Any]] = [{
        "name": ROOT_BONE,
        "parent": "",
        "head_m": [0.0, 0.0, 0.0],
        "tail_m": [0.0, 0.1, 0.0],
        "up_m": [0.0, 0.0, 1.0],
    }]
    branches_out: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for branch in src["branches"]:
        branch_id = branch["branch_id"]
        if branch_id in by_id:
            raise CatalogError("CC_DUP_BRANCH", f"{sid}: duplicate branch {branch_id}")
        try:
            profile = resolve_profile(
                profiles,
                branch["binding_profile_id"],
                branch["binding_profile_version"],
            )
        except BindingProfileError as exc:
            raise CatalogError(exc.code, f"{sid}.{branch_id}: {exc.message}") from exc
        if branch.get("template") != profile["template"]:
            raise CatalogError("CC_BINDING_TEMPLATE", f"{sid}.{branch_id}")
        parent_id = branch.get("parent_branch") or ""
        parent = by_id.get(parent_id) if parent_id else None
        if parent_id and parent is None:
            raise CatalogError("CC_PARENT_ORDER", f"{sid}.{branch_id}: parent {parent_id} must precede it")
        if branch.get("required") and parent is not None and not parent["required"]:
            raise CatalogError(
                "CC_REQUIRED_ANCESTOR_OPTIONAL",
                f"{sid}.{branch_id}: required branch has optional parent {parent_id}",
            )
        attach_bone, socket = _compile_socket(sid, branch, parent)
        origin, snap_rotation = _socket_frame(sid, branch, parent)
        length_axis = mu.quat_rotate(snap_rotation, (0.0, 0.0, 1.0))
        up_axis = mu.quat_rotate(snap_rotation, (0.0, 1.0, 0.0))
        length = float(branch["length_m"])
        fractions = [float(value) for value in profile["bone_fractions"]]
        joint_order = list(profile["joint_order"])
        if len(fractions) != len(joint_order):
            raise CatalogError("CC_BINDING_TOPOLOGY", f"{sid}.{branch_id}: joint/fraction count differs")
        names: list[str] = []
        cursor = origin
        for i, (joint_id, fraction) in enumerate(zip(joint_order, fractions, strict=True)):
            tail = mu.add(cursor, mu.scale(length_axis, fraction * length))
            name = bone_name(branch_id, i)
            bones.append({
                "name": name,
                "parent": attach_bone if i == 0 else names[-1],
                "joint_id": joint_id,
                "head_m": mu.r6v(cursor),
                "tail_m": mu.r6v(tail),
                "up_m": mu.r6v(up_axis),
            })
            names.append(name)
            cursor = tail
        accepts = branch.get("accepts", {})
        gait = dict(branch.get("gait", {}))
        out = {
            "branch_id": branch_id,
            "template": branch.get("template", profile["template"]),
            "parent_branch": parent_id,
            "attach_bone": attach_bone,
            "bone_names": names,
            "joint_order": joint_order,
            "bone_fractions": [mu.r6(value) for value in fractions],
            "binding_profile_id": profile["binding_profile_id"],
            "binding_profile_version": profile["binding_profile_version"],
            "binding_profile_hash": profile["binding_profile_hash"],
            "length_m": mu.r6(length),
            "length_mm": mu.mm(length),
            "girth_m": mu.r6(float(branch["girth_m"])),
            "girth_mm": mu.mm(float(branch["girth_m"])),
            "size_class": branch.get("size_class", ""),
            "side": branch["side"],
            "required": bool(branch.get("required", False)),
            "optional_fill_pct": int(
                branch.get("optional_fill_pct", 100 if branch.get("required") else DEFAULT_OPTIONAL_FILL_PCT)
            ),
            "mirror_of": branch.get("mirror_of") or "",
            "accepts": {
                "categories": sorted(accepts.get("categories", [])),
                "templates": sorted(accepts.get("templates", [])),
                "tags_any": sorted(accepts.get("tags_any", [])),
            },
            "connector_size_class": branch.get("connector_size_class") or "",
            "connector_interface": ({
                "interface_id": CONNECTOR_INTERFACE_ID,
                "interface_version": CONNECTOR_INTERFACE_VERSION,
                "parent_role": "parent",
                "child_role": "child",
                "parent_bone": attach_bone,
                "child_bone": names[0],
                "position_m": [0.0, 0.0, 0.0],
                "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
            } if branch.get("connector_size_class") else {}),
            "gait_role": gait.get("role", "none"),
            "gait_phase_rad": mu.r6(float(gait.get("phase_rad", 0.0))),
            "gait": gait,
            "contacts": list(branch.get("contacts", [])),
            "socket": socket,
            "stance_deg": [mu.r6(float(value)) for value in branch.get("stance_deg", [])],
            "stance_z_deg": [mu.r6(float(value)) for value in branch.get("stance_z_deg", [])],
            "snap": {"position_m": mu.r6v(origin), "rotation_xyzw": mu.r6v(snap_rotation)},
        }
        by_id[branch_id] = out
        branches_out.append(out)
    for branch in branches_out:
        if branch["mirror_of"] and branch["mirror_of"] not in by_id:
            raise CatalogError("CC_MIRROR", f"{sid}.{branch['branch_id']}: mirror_of {branch['mirror_of']} unknown")
    compiled = {
        "skeleton_id": sid,
        "family": src["family"],
        "locomotion_hint": src["locomotion_hint"],
        "status": src.get("status", "draft"),
        "symmetry_pct": int(src.get("symmetry_pct", 50)),
        "anatomy": src["anatomy"],
        "neutral_pose": src["neutral_pose"],
        "bones": bones,
        "branches": branches_out,
        "asset": {"fbx": "", "glb": "", "clips": []},
    }
    compiled["locomotion"] = locomotion_block(compiled)
    return compiled


def _compile_connector_interface(part_id: str, connector: dict[str, Any]) -> dict[str, Any]:
    interface = connector.get("interface") or {}
    groups = interface.get("bone_groups") or []
    expected_groups = [{"group": "b0", "role": "parent"}, {"group": "b1", "role": "child"}]
    if (
        interface.get("interface_id") != CONNECTOR_INTERFACE_ID
        or interface.get("interface_version") != CONNECTOR_INTERFACE_VERSION
        or groups != expected_groups
    ):
        raise CatalogError("CC_CONNECTOR_TOPOLOGY", part_id)
    if interface.get("max_influences") != 2 or interface.get("weights_normalized") is not True:
        raise CatalogError("CC_CONNECTOR_WEIGHTS", part_id)
    position = interface.get("position_m")
    rotation = interface.get("rotation_xyzw")
    if position != [0.0, 0.0, 0.0] and position != [0, 0, 0]:
        raise CatalogError("CC_CONNECTOR_TRANSFORM", part_id)
    if rotation != [0.0, 0.0, 0.0, 1.0] and rotation != [0, 0, 0, 1]:
        raise CatalogError("CC_CONNECTOR_TRANSFORM", part_id)
    return {
        "interface_id": CONNECTOR_INTERFACE_ID,
        "interface_version": CONNECTOR_INTERFACE_VERSION,
        "bone_groups": expected_groups,
        "max_influences": 2,
        "weights_normalized": True,
        "position_m": [0.0, 0.0, 0.0],
        "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
    }


def compile_part(
    src: dict[str, Any], profiles: dict[tuple[str, str], dict[str, Any]] | None = None
) -> dict[str, Any]:
    profiles = profiles or {}
    is_connector = src["category"] == "connector"
    try:
        profile = resolve_profile(profiles, src["binding_profile_id"], src["binding_profile_version"])
    except BindingProfileError as exc:
        raise CatalogError(exc.code, f"{src.get('part_id', '<unknown>')}: {exc.message}") from exc
    if not is_connector and src.get("template") != profile["template"]:
        raise CatalogError("CC_BINDING_TEMPLATE", src.get("part_id", "<unknown>"))
    connector = src.get("connector") or {}
    connector_interface = _compile_connector_interface(src["part_id"], connector) if is_connector else {}
    dimensions = src["dimensions_m"]
    girth = float(src.get("girth_m", max(float(dimensions[0]), float(dimensions[1]))))
    return {
        "part_id": src["part_id"],
        "category": src["category"],
        "template": src.get("template", profile["template"]),
        "binding_profile_id": profile["binding_profile_id"],
        "binding_profile_version": profile["binding_profile_version"],
        "binding_profile_hash": profile["binding_profile_hash"],
        "inventory_kind": "production",
        "species_tags": sorted(src.get("species_tags", [])),
        "roles": sorted(src.get("roles", [])),
        "size_class": src.get("size_class") or "",
        "side": src.get("side", "C"),
        "status": src.get("status", "draft"),
        "style_profile": src.get("style_profile", ""),
        "dimensions_m": mu.r6v(dimensions),
        "length_m": mu.r6(float(src["length_m"])),
        "length_mm": mu.mm(float(src["length_m"])),
        "girth_m": mu.r6(girth),
        "girth_mm": mu.mm(girth),
        "max_triangles": int(src["budget"]["max_triangles"]),
        "max_material_slots": int(src["budget"].get("max_material_slots", 2)),
        "connector_radius_m": mu.r6(float(connector.get("radius_m", 0.0))),
        "connector_span_m": mu.r6v(connector.get("span_m", [0.0, 0.0])) if connector else [],
        "connector_interface": connector_interface,
        "fallback_primitive": src.get("fallback", {}).get("primitive", "capsule"),
        "albedo": src.get("fallback", {}).get("albedo", "#a07a80"),
        "source": src.get("provenance", {}).get("source", "production"),
        **_compile_real(src),
        "asset": {"fbx": "", "glb": "", "triangles": 0},
    }


def _compile_real(src: dict[str, Any]) -> dict[str, Any]:
    """Build inputs of a part fitted from a sourced mesh: archive-relative path, hash and fit only."""
    real = src.get("real")
    if not real:
        return {}
    compiled = {
        "archive_path": real["archive_path"],
        "sha256": real["sha256"],
        "fit": dict(sorted(real["fit"].items())),
        "texture_size": int(src["budget"].get("texture_size", 1024)),
    }
    if real.get("approved_pipeline"):
        compiled["approved_pipeline"] = real["approved_pipeline"]
    return {"real": compiled}


def reference_part_id(skeleton_id: str, branch_id: str, *, connector: bool = False) -> str:
    """Return the stable dedicated reference-inventory identity for one branch."""
    prefix = "reference_connector" if connector else "reference"
    safe = re.sub(r"[^a-z0-9_]+", "_", f"{prefix}_{skeleton_id}_{branch_id}".lower()).strip("_")
    return f"{safe}_v1"


def _reference_id(skeleton_id: str, branch_id: str) -> str:
    return reference_part_id(skeleton_id, branch_id)


def _reference_connector_id(skeleton_id: str, branch_id: str) -> str:
    return reference_part_id(skeleton_id, branch_id, connector=True)


def _reference_cap(t: float) -> float:
    cap = 0.08
    if t < cap:
        return math.sqrt(max(0.0, 1.0 - ((cap - t) / cap) ** 2))
    if t > 1.0 - cap:
        return math.sqrt(max(0.0, 1.0 - ((t - (1.0 - cap)) / cap) ** 2))
    return 1.0


def _reference_profile_radius(category: str, t: float) -> float:
    """Match the deterministic reference loft used by ``ops_placeholder``."""
    if category == "core":
        radius = (0.8 + 0.2 * math.sin(math.pi * t)) * _reference_cap(t)
    elif category == "head":
        radius = math.sqrt(max(0.0, 1.0 - (2.0 * t - 1.0) ** 2))
    else:
        radius = (
            1.0 - 0.35 * t + 0.06 * math.sin(t * math.pi * 6.0)
        ) * _reference_cap(t)
    return max(0.06, radius)


def _reference_thickness(
    skeleton: dict[str, Any],
    branch: dict[str, Any],
    category: str,
    z_start: float,
    z_end: float,
    rings: int,
    joint_samples: list[float],
) -> float:
    """Cap low sliding/body volumes against their authored ground envelope.

    Width remains the nominal matching girth.  Only the physical part-space
    thickness is reduced for body/sliding contacts and low non-contact body
    chains.  Foot and hand chains retain their authored round section because
    their terminal contact profile is responsible for meeting the floor.  The
    samples and radial profile are the exact deterministic loft samples
    consumed by the Blender reference placeholder.
    """
    girth = float(branch["girth_m"])
    contact_kinds = {contact.get("kind") for contact in branch["contacts"]}
    if contact_kinds & {"foot", "hand"}:
        return mu.r6(girth)

    rotation = branch["snap"]["rotation_xyzw"]
    local_x = mu.quat_rotate(rotation, (1.0, 0.0, 0.0))
    local_y = mu.quat_rotate(rotation, (0.0, 1.0, 0.0))
    local_z = mu.quat_rotate(rotation, (0.0, 0.0, 1.0))
    origin = branch["snap"]["position_m"]
    root_y = float(skeleton["neutral_pose"]["root_offset_m"][1])
    width_vertical_radius = abs(float(local_x[1])) * girth * 0.5
    thickness_vertical = abs(float(local_y[1]))
    thickness = girth

    samples = {index / rings for index in range(rings + 1)}
    samples.update(joint_samples)
    for t in sorted(samples):
        z = z_start + t * (z_end - z_start)
        centre_y = root_y + float(origin[1]) + float(local_z[1]) * z
        available = centre_y - REFERENCE_GROUND_CLEARANCE_M
        if available <= 0.0:
            raise CatalogError(
                "CC_REFERENCE_ENVELOPE",
                f"{skeleton['skeleton_id']}.{branch['branch_id']}: centreline is at or below ground clearance",
            )
        profile_radius = _reference_profile_radius(category, t)
        radial_budget = available / profile_radius
        remaining_sq = radial_budget * radial_budget - width_vertical_radius * width_vertical_radius
        if remaining_sq <= 0.0:
            raise CatalogError(
                "CC_REFERENCE_ENVELOPE",
                f"{skeleton['skeleton_id']}.{branch['branch_id']}: nominal width intersects ground",
            )
        if thickness_vertical > 1e-8:
            thickness = min(thickness, 2.0 * math.sqrt(remaining_sq) / thickness_vertical)

    if not math.isfinite(thickness) or thickness <= 0.0:
        raise CatalogError(
            "CC_REFERENCE_ENVELOPE",
            f"{skeleton['skeleton_id']}.{branch['branch_id']}: non-positive thickness",
        )
    # Flooring prevents decimal serialization from rounding a tight envelope
    # outward through the five-millimetre ground clearance.
    return math.floor(min(girth, thickness) * 1_000_000.0) / 1_000_000.0


def _reference_parts(skeletons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for skeleton in skeletons:
        for branch in skeleton["branches"]:
            category = branch["accepts"]["categories"][0] if branch["accepts"]["categories"] else "appendage"
            girth = branch["girth_m"]
            length = branch["length_m"]
            body_rings = max(16, 6 * len(branch["bone_names"]))
            cumulative = 0.0
            joint_samples = [0.0]
            for fraction in branch["bone_fractions"]:
                cumulative += float(fraction)
                joint_samples.append(cumulative)
            body_thickness = _reference_thickness(
                skeleton, branch, category, 0.0, length, body_rings, joint_samples
            )
            parts.append({
                "part_id": _reference_id(skeleton["skeleton_id"], branch["branch_id"]),
                "category": category,
                "template": branch["template"],
                "binding_profile_id": branch["binding_profile_id"],
                "binding_profile_version": branch["binding_profile_version"],
                "binding_profile_hash": branch["binding_profile_hash"],
                "inventory_kind": "reference",
                "species_tags": [],
                "roles": [branch["gait_role"]] if branch["gait_role"] != "none" else [],
                "size_class": branch["size_class"],
                "side": branch["side"],
                "status": "reference",
                "style_profile": "",
                "dimensions_m": [girth, body_thickness, length],
                "length_m": length,
                "length_mm": branch["length_mm"],
                "girth_m": girth,
                "girth_mm": branch["girth_mm"],
                "max_triangles": 1800,
                "max_material_slots": 1,
                "connector_radius_m": 0.0,
                "connector_span_m": [],
                "connector_interface": {},
                "fallback_primitive": "capsule",
                "albedo": "#8f7a82",
                "source": "reference",
                "asset": {"fbx": "", "glb": "", "triangles": 0},
            })
            if branch["connector_size_class"]:
                span = mu.r6(min(0.08, length * 0.08))
                connector_segments = 16 if girth * 0.5 > 0.2 else 12
                # Envelope samples follow the old 300-tri loft, not the 800 SDF cap.
                connector_rings = min(10, max(3, 300 // (2 * connector_segments) - 1))
                connector_thickness = _reference_thickness(
                    skeleton, branch, "connector", -span, span, connector_rings, [0.0, 0.5, 1.0]
                )
                parts.append({
                    "part_id": _reference_connector_id(skeleton["skeleton_id"], branch["branch_id"]),
                    "category": "connector",
                    "template": "connector2",
                    "binding_profile_id": branch["binding_profile_id"],
                    "binding_profile_version": branch["binding_profile_version"],
                    "binding_profile_hash": branch["binding_profile_hash"],
                    "inventory_kind": "reference",
                    "species_tags": [],
                    "roles": ["connector"],
                    "size_class": branch["connector_size_class"],
                    "side": branch["side"],
                    "status": "reference",
                    "style_profile": "",
                    "dimensions_m": [girth, connector_thickness, 2 * span],
                    "length_m": 2 * span,
                    "length_mm": mu.mm(2 * span),
                    "girth_m": girth,
                    "girth_mm": branch["girth_mm"],
                    "max_triangles": CONNECTOR_MAX_TRIANGLES,
                    "max_material_slots": 1,
                    "connector_radius_m": mu.r6(girth / 2),
                    "connector_span_m": [-span, span],
                    "connector_interface": {
                        "interface_id": CONNECTOR_INTERFACE_ID,
                        "interface_version": CONNECTOR_INTERFACE_VERSION,
                        "bone_groups": [
                            {"group": "b0", "role": "parent"},
                            {"group": "b1", "role": "child"},
                        ],
                        "max_influences": 2,
                        "weights_normalized": True,
                        "position_m": [0.0, 0.0, 0.0],
                        "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
                    },
                    "fallback_primitive": "cylinder",
                    "albedo": "#8f7a82",
                    "source": "reference",
                    "asset": {"fbx": "", "glb": "", "triangles": 0},
                })
    return sorted(parts, key=lambda part: part["part_id"])


def _unique_index(items: list[dict[str, Any]], field: str, code: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        identity = item[field]
        if identity in result:
            raise CatalogError(code, identity)
        result[identity] = item
    return result


def _registry_index(items: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for item in items:
        key = (item["binding_profile_id"], item["binding_profile_version"])
        if key in result:
            raise CatalogError("CC_DUP_BINDING_PROFILE_REGISTRY", f"{key[0]}@{key[1]}")
        result[key] = item["binding_profile_hash"]
    return result


def compile_catalog(sources: dict[str, Any]) -> dict[str, Any]:
    library = sources["library"]
    version = library.get("schema_version")
    if version != SCHEMA_VERSION:
        raise CatalogError("CC_LIBRARY_SCHEMA_VERSION", f"expected {SCHEMA_VERSION}, got {version}")
    if library.get("version") != LIBRARY_VERSION:
        raise CatalogError("CC_LIBRARY_VERSION", f"expected {LIBRARY_VERSION}, got {library.get('version')}")
    algorithm = library.get("generator", {}).get("algorithm")
    if algorithm != GENERATOR:
        raise CatalogError("CC_GENERATOR_VERSION", f"expected {GENERATOR}, got {algorithm}")
    try:
        registry = _registry_index(sources.get("binding_profile_registry", []))
        baseline = _registry_index(sources.get("binding_profile_baseline", []))
        if not baseline:
            raise BindingProfileError("CC_BINDING_PROFILE_BASELINE", "released registry is required")
        profiles = index_profiles(sources.get("binding_profiles", []), registry, baseline)
    except BindingProfileError as exc:
        raise CatalogError(exc.code, exc.message) from exc
    templates = _unique_index(sources.get("templates", []), "template_id", "CC_DUP_TEMPLATE")
    _unique_index(sources.get("skeletons", []), "skeleton_id", "CC_DUP_SKELETON")
    _unique_index(sources.get("parts", []), "part_id", "CC_DUP_PART")
    _unique_index(sources.get("pools", []), "pool_id", "CC_DUP_POOL")
    _unique_index(sources.get("gait", {}).get("gait_profiles", []), "hint", "CC_DUP_GAIT_PROFILE")
    for profile in profiles.values():
        template = templates.get(profile["template"])
        if template is None or len(template["bone_fractions"]) != len(profile["bone_fractions"]):
            raise CatalogError(
                "CC_BINDING_TEMPLATE",
                f"{profile['binding_profile_id']}@{profile['binding_profile_version']}",
            )
    skeletons = [
        compile_skeleton(source, templates, profiles)
        for source in sorted(sources["skeletons"], key=lambda item: item["skeleton_id"])
    ]
    production_parts = [
        compile_part(source, profiles)
        for source in sorted(sources.get("parts", []), key=lambda item: item["part_id"])
    ]
    parts = sorted(production_parts + _reference_parts(skeletons), key=lambda part: part["part_id"])
    _unique_index(parts, "part_id", "CC_DUP_PART")
    pools = [{
        "pool_id": pool["pool_id"],
        "families": sorted(pool.get("families", [])),
        "skeleton_ids": sorted(pool.get("skeleton_ids", [])),
    } for pool in sorted(sources["pools"], key=lambda item: item["pool_id"])]
    compiled_profiles = sorted(profiles.values(), key=lambda profile: (
        profile["binding_profile_id"], profile["binding_profile_version"]
    ))
    branch_templates = [{
        "template_id": profile["template"],
        "chain_kind": templates.get(profile["template"], {}).get("chain_kind", profile["template"]),
        "nominal_length_m": templates.get(profile["template"], {}).get("nominal_length_m", 1.0),
        "bone_fractions": profile["bone_fractions"],
    } for profile in compiled_profiles]
    branch_templates = list({item["template_id"]: item for item in branch_templates}.values())
    if any(branch["connector_size_class"] for skeleton in skeletons for branch in skeleton["branches"]):
        branch_templates.append({
            "template_id": "connector2",
            "chain_kind": "connector",
            "nominal_length_m": 0.16,
            "bone_fractions": [0.5, 0.5],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "document_kind": "critter_library",
        "library_id": library["library_id"],
        "version": library["version"],
        "frame": library["frame"],
        "limits": library["limits"],
        "generator": library["generator"],
        "binding_profiles": compiled_profiles,
        "gait_profiles": sources.get("gait", {}).get("gait_profiles", []),
        "branch_templates": branch_templates,
        "skeletons": skeletons,
        "parts": parts,
        "pools": pools,
    }


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=True) + "\n"
