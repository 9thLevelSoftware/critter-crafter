"""JSON Schema and semantic validation for schema-v3 authoring sources."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..library.catalog import SCHEMA_VERSION, CatalogError, compile_catalog, load_sources
from ..recipes.generator import connector_accepted, part_accepted

SCHEMA_FOR = {
    "templates": "branch_template.v3.schema.json",
    "binding_profiles": "binding_profile.v3.schema.json",
    "skeletons": "skeleton.v3.schema.json",
    "parts": "part.v3.schema.json",
}


def _validator(schemas_dir: Path, name: str) -> Draft202012Validator:
    with (schemas_dir / name).open(encoding="utf-8") as f:
        return Draft202012Validator(json.load(f))


def _schema_diags(validator: Draft202012Validator, doc: Any, where: str) -> list[str]:
    return [
        f"CC_SCHEMA: {where}: {'/'.join(str(path) for path in error.absolute_path) or '<root>'}: {error.message}"
        for error in validator.iter_errors(doc)
    ]


def _is_unit(values: list[float], tolerance: float = 1e-6) -> bool:
    return all(math.isfinite(float(value)) for value in values) and abs(
        sum(float(value) ** 2 for value in values) - 1.0
    ) <= tolerance


def _dot(left: list[float], right: list[float]) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right, strict=True))


def _finite_diags(value: Any, where: str) -> list[str]:
    diagnostics: list[str] = []
    if isinstance(value, float) and not math.isfinite(value):
        diagnostics.append(f"CC_NONFINITE: {where}")
    elif isinstance(value, dict):
        for key, child in value.items():
            diagnostics += _finite_diags(child, f"{where}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            diagnostics += _finite_diags(child, f"{where}/{index}")
    return diagnostics


def _profile_diags(profile: dict[str, Any]) -> list[str]:
    diagnostics: list[str] = []
    identity = f"{profile.get('binding_profile_id')}@{profile.get('binding_profile_version')}"
    order = profile.get("joint_order", [])
    fractions = profile.get("bone_fractions", [])
    finite_fractions = all(isinstance(value, (int, float)) and math.isfinite(float(value)) and value > 0 for value in fractions)
    if len(order) != len(fractions) or not finite_fractions or abs(sum(fractions) - 1.0) > 1e-6:
        diagnostics.append(f"CC_BINDING_FRACTIONS: {identity}")
    girth = profile.get("girth_ratio")
    if not isinstance(girth, (int, float)) or not math.isfinite(float(girth)) or not 0 < girth <= 1:
        diagnostics.append(f"CC_BINDING_GIRTH: {identity}")
    joints = profile.get("joints", [])
    if [joint.get("joint_id") for joint in joints] != order:
        diagnostics.append(f"CC_BINDING_JOINT_ORDER: {identity}")
    seen: set[str] = set()
    for index, joint in enumerate(joints):
        joint_id = joint.get("joint_id", f"#{index}")
        parent = joint.get("parent_joint", "")
        if (index == 0 and parent) or (index > 0 and parent not in seen):
            diagnostics.append(f"CC_BINDING_PARENT_ORDER: {identity}.{joint_id}")
        seen.add(joint_id)
        quaternion = joint.get("canonical_rotation_xyzw", [])
        if len(quaternion) != 4 or not _is_unit(quaternion) or not _near_values(quaternion, [0, 0, 0, 1]):
            diagnostics.append(f"CC_BINDING_QUATERNION: {identity}.{joint_id}")
        expected_position = [0.0, 0.0, sum(fractions[:index]) if finite_fractions else 0.0]
        position = joint.get("canonical_position_n", [])
        if len(position) != 3 or not _near_values(position, expected_position):
            diagnostics.append(f"CC_BINDING_CANONICAL_POSITION: {identity}.{joint_id}")
        primary = joint.get("primary_axis", [])
        secondary = joint.get("secondary_axis", [])
        if (
            len(primary) != 3
            or len(secondary) != 3
            or not _is_unit(primary)
            or not _is_unit(secondary)
            or abs(_dot(primary, secondary)) > 1e-6
            or not _near_values(primary, [0, 0, 1])
            or not _near_values(secondary, [0, 1, 0])
        ):
            diagnostics.append(f"CC_BINDING_AXES: {identity}.{joint_id}")
        for axis, limits in joint.get("limits_deg", {}).items():
            if (
                not isinstance(limits, list)
                or len(limits) != 2
                or not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in limits)
                or limits[0] > limits[1]
            ):
                diagnostics.append(f"CC_BINDING_LIMITS: {identity}.{joint_id}.{axis}")
    for landmark in profile.get("landmarks", []):
        landmark_id = landmark.get("landmark_id", "<unknown>")
        if landmark.get("parent_joint") not in seen:
            diagnostics.append(f"CC_BINDING_LANDMARK_PARENT: {identity}.{landmark_id}")
        quaternion = landmark.get("rotation_xyzw", [])
        if len(quaternion) != 4 or not _is_unit(quaternion):
            diagnostics.append(f"CC_BINDING_QUATERNION: {identity}.{landmark_id}")
    return diagnostics


def _near_values(left: list[Any], right: list[Any], tolerance: float = 1e-6) -> bool:
    return len(left) == len(right) and all(
        isinstance(a, (int, float)) and math.isfinite(float(a)) and abs(float(a) - float(b)) <= tolerance
        for a, b in zip(left, right, strict=True)
    )


def _skeleton_diags(
    source: dict[str, Any],
    compiled: dict[str, Any],
    profiles: dict[tuple[str, str], dict[str, Any]],
) -> list[str]:
    diagnostics: list[str] = []
    skeleton_id = source["skeleton_id"]
    branches = {branch["branch_id"]: branch for branch in compiled["branches"]}
    for branch in source["branches"]:
        branch_id = branch["branch_id"]
        quaternion = branch.get("socket", {}).get("rotation_xyzw", [])
        if len(quaternion) != 4 or not _is_unit(quaternion):
            diagnostics.append(f"CC_SOCKET_QUATERNION: {skeleton_id}.{branch_id}")
        compiled_branch = branches.get(branch_id)
        if compiled_branch:
            for contact in branch.get("contacts", []):
                if contact["bone_index"] >= len(compiled_branch["bone_names"]):
                    diagnostics.append(f"CC_CONTACT_BONE: {skeleton_id}.{branch_id}[{contact['bone_index']}]")
    expected_order = [bone["name"] for bone in compiled["bones"]]
    rotations = source.get("neutral_pose", {}).get("rotations", [])
    actual_order = [rotation.get("bone_name") for rotation in rotations]
    if actual_order != expected_order:
        diagnostics.append(f"CC_NEUTRAL_POSE_ORDER: {skeleton_id}")
    for rotation in rotations:
        quaternion = rotation.get("rotation_xyzw", [])
        if len(quaternion) != 4 or not _is_unit(quaternion):
            diagnostics.append(f"CC_NEUTRAL_QUATERNION: {skeleton_id}.{rotation.get('bone_name')}")
    rotations_by_name = {rotation.get("bone_name"): rotation.get("rotation_xyzw", []) for rotation in rotations}
    for branch in compiled["branches"]:
        source_profile = profiles.get((branch["binding_profile_id"], branch["binding_profile_version"]))
        if source_profile is None:
            continue
        for bone_name, joint in zip(branch["bone_names"], source_profile["joints"], strict=True):
            quaternion = rotations_by_name.get(bone_name, [])
            if len(quaternion) != 4 or not _is_unit(quaternion):
                continue
            x, y, z, w = (float(value) for value in quaternion)
            angles = {
                "swing_x": -math.degrees(math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))),
                "twist": math.degrees(math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))),
                "swing_y": math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))),
            }
            for axis, value in angles.items():
                low, high = joint["limits_deg"][axis]
                if not float(low) - 0.1 <= value <= float(high) + 0.1:
                    diagnostics.append(f"CC_NEUTRAL_LIMIT: {skeleton_id}.{bone_name}.{axis}")
    branch_ids = set(branches)
    anatomy = source.get("anatomy", {})
    for field in ("support_branches", "contact_branches"):
        for branch_id in anatomy.get(field, []):
            if branch_id not in branch_ids:
                diagnostics.append(f"CC_ANATOMY_BRANCH: {skeleton_id}.{field}={branch_id}")
    for name, branch_id in anatomy.get("landmarks", {}).items():
        if branch_id not in branch_ids:
            diagnostics.append(f"CC_ANATOMY_LANDMARK: {skeleton_id}.{name}={branch_id}")
    contact_branches = set(anatomy.get("contact_branches", []))
    actual_contacts = {
        branch["branch_id"] for branch in source["branches"] if branch.get("contacts")
    }
    if contact_branches != actual_contacts:
        diagnostics.append(f"CC_ANATOMY_CONTACT_SET: {skeleton_id}")
    support_branches = set(anatomy.get("support_branches", []))
    if not support_branches <= contact_branches:
        diagnostics.append(f"CC_ANATOMY_SUPPORT_CONTACT: {skeleton_id}")
    minimum_support = 2 if source.get("family") in {"quadruped", "hexapod", "crawler", "radial"} else 1
    if len(support_branches) < minimum_support:
        diagnostics.append(f"CC_ANATOMY_SUPPORT_COUNT: {skeleton_id}")
    symmetry = anatomy.get("symmetry", {})
    paired: set[str] = set()
    source_branches = {branch["branch_id"]: branch for branch in source["branches"]}
    for pair in symmetry.get("pairs", []):
        if len(pair) != 2 or any(branch_id not in source_branches for branch_id in pair):
            diagnostics.append(f"CC_ANATOMY_SYMMETRY_PAIR: {skeleton_id}")
            continue
        left_id, right_id = pair
        if left_id in paired or right_id in paired:
            diagnostics.append(f"CC_ANATOMY_SYMMETRY_PAIR: {skeleton_id}")
        paired.update(pair)
        left, right = source_branches[left_id], source_branches[right_id]
        if left.get("mirror_of") != right_id or right.get("mirror_of") != left_id:
            diagnostics.append(f"CC_ANATOMY_SYMMETRY_MIRROR: {skeleton_id}.{left_id}/{right_id}")
        if {left.get("side"), right.get("side")} != {"L", "R"}:
            diagnostics.append(f"CC_ANATOMY_SYMMETRY_SIDE: {skeleton_id}.{left_id}/{right_id}")
    if symmetry.get("kind") == "radial" and symmetry.get("ring_count") != len(support_branches):
        diagnostics.append(f"CC_ANATOMY_RADIAL_COUNT: {skeleton_id}")
    budgets = anatomy.get("budgets", {})
    if budgets.get("bones") != len(compiled["bones"]):
        diagnostics.append(f"CC_ANATOMY_BONE_COUNT: {skeleton_id}")
    if budgets.get("parts") != len(compiled["branches"]):
        diagnostics.append(f"CC_ANATOMY_PART_COUNT: {skeleton_id}")
    return diagnostics


def validate_sources(data_dir: Path, schemas_dir: Path) -> tuple[list[str], dict[str, Any] | None]:
    """Return sorted diagnostics and the catalog only when every hard contract passes."""
    try:
        sources = load_sources(data_dir)
    except CatalogError as exc:
        return [str(exc)], None
    library = sources["library"]
    version = library.get("schema_version")
    if version != SCHEMA_VERSION:
        return [f"CC_LIBRARY_SCHEMA_VERSION: expected {SCHEMA_VERSION}, got {version}"], None
    diagnostics: list[str] = []
    diagnostics += _schema_diags(
        _validator(schemas_dir, "library_source.v3.schema.json"),
        library,
        "library.json",
    )
    if library.get("version") != "0.2.0":
        diagnostics.append(f"CC_LIBRARY_VERSION: expected 0.2.0, got {library.get('version')}")
    if library.get("generator", {}).get("algorithm") != "cc-gen-3":
        diagnostics.append("CC_GENERATOR_VERSION: expected cc-gen-3")
    for subdir, schema_name in SCHEMA_FOR.items():
        validator = _validator(schemas_dir, schema_name)
        key = {
            "templates": "template_id",
            "binding_profiles": "binding_profile_id",
            "skeletons": "skeleton_id",
            "parts": "part_id",
        }[subdir]
        for document in sources[subdir]:
            identity = document.get(key, "<unknown>")
            diagnostics += _schema_diags(validator, document, f"{subdir}/{identity}")
    pools_doc = {"schema_version": SCHEMA_VERSION, "pools": sources["pools"]}
    diagnostics += _schema_diags(_validator(schemas_dir, "pools.v3.schema.json"), pools_doc, "pools/pools.json")
    diagnostics += _schema_diags(
        _validator(schemas_dir, "gait_profiles.v3.schema.json"),
        sources["gait"],
        "gait_profiles",
    )
    registry_doc = {"schema_version": SCHEMA_VERSION, "profiles": sources["binding_profile_registry"]}
    diagnostics += _schema_diags(
        _validator(schemas_dir, "binding_profile_registry.v3.schema.json"),
        registry_doc,
        "binding_profiles/registry.json",
    )
    baseline_doc = {"schema_version": SCHEMA_VERSION, "profiles": sources["binding_profile_baseline"]}
    diagnostics += _schema_diags(
        _validator(schemas_dir, "binding_profile_registry.v3.schema.json"),
        baseline_doc,
        "binding_profiles/released_registry.json",
    )
    for group, documents in sources.items():
        diagnostics += _finite_diags(documents, group)
    if diagnostics:
        return sorted(diagnostics), None
    for profile in sources["binding_profiles"]:
        diagnostics += _profile_diags(profile)
    if diagnostics:
        return sorted(diagnostics), None
    try:
        catalog = compile_catalog(sources)
    except CatalogError as exc:
        return [str(exc)], None
    compiled_by_id = {item["skeleton_id"]: item for item in catalog["skeletons"]}
    profiles = {
        (profile["binding_profile_id"], profile["binding_profile_version"]): profile
        for profile in catalog["binding_profiles"]
    }
    for skeleton in sources["skeletons"]:
        diagnostics += _skeleton_diags(skeleton, compiled_by_id[skeleton["skeleton_id"]], profiles)
    diagnostics += cross_check(catalog)
    diagnostics += _schema_diags(_validator(schemas_dir, "library.v3.schema.json"), catalog, "compiled_catalog")
    if diagnostics:
        return sorted(diagnostics), None
    return [], catalog


def cross_check(catalog: dict[str, Any]) -> list[str]:
    diagnostics: list[str] = []
    profiles = {
        (profile["binding_profile_id"], profile["binding_profile_version"]): profile
        for profile in catalog["binding_profiles"]
    }
    seen_parts: set[str] = set()
    for part in catalog["parts"]:
        if part["part_id"] in seen_parts:
            diagnostics.append(f"CC_DUP_PART: {part['part_id']}")
        seen_parts.add(part["part_id"])
        profile = profiles.get((part["binding_profile_id"], part["binding_profile_version"]))
        if profile is None or profile["binding_profile_hash"] != part["binding_profile_hash"]:
            diagnostics.append(f"CC_PART_BINDING_PROFILE: {part['part_id']}")
    families = {skeleton["family"] for skeleton in catalog["skeletons"]}
    gait_hints = {profile.get("hint") for profile in catalog["gait_profiles"]}
    for skeleton in catalog["skeletons"]:
        if skeleton["locomotion_hint"] not in gait_hints:
            diagnostics.append(
                f"CC_UNKNOWN_GAIT_PROFILE: {skeleton['skeleton_id']}={skeleton['locomotion_hint']}"
            )
        if len(skeleton["bones"]) > catalog["limits"]["max_bones"]:
            diagnostics.append(f"CC_BUDGET_BONES: {skeleton['skeleton_id']}")
        if len(skeleton["branches"]) > catalog["limits"]["max_parts"]:
            diagnostics.append(f"CC_BUDGET_PARTS: {skeleton['skeleton_id']}")
        for branch in skeleton["branches"]:
            candidates = [part for part in catalog["parts"] if part_accepted(part, branch)]
            if not candidates:
                diagnostics.append(f"CC_NO_REFERENCE_CANDIDATE: {skeleton['skeleton_id']}.{branch['branch_id']}")
            if branch["connector_size_class"]:
                connector_candidates = [part for part in catalog["parts"] if connector_accepted(part, branch)]
                if not connector_candidates:
                    diagnostics.append(f"CC_NO_REFERENCE_CONNECTOR: {skeleton['skeleton_id']}.{branch['branch_id']}")
    for pool in catalog["pools"]:
        if not (set(pool["families"]) & families) and not pool["skeleton_ids"]:
            diagnostics.append(f"CC_EMPTY_POOL: {pool['pool_id']}")
    limits = catalog["limits"]
    expected_limits = {"max_bones": 120, "max_parts": 16, "max_triangles": 30000, "max_influences": 4}
    for key, expected in expected_limits.items():
        if limits.get(key) != expected:
            diagnostics.append(f"CC_LIBRARY_LIMIT: {key} expected {expected}, got {limits.get(key)}")
    return diagnostics
