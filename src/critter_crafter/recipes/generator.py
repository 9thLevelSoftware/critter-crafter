"""Deterministic schema-v3 recipe generator."""

from __future__ import annotations

from typing import Any

from ..binding.profiles import CONNECTOR_INTERFACE_ID, CONNECTOR_INTERFACE_VERSION
from ..library.catalog import reference_part_id
from .rng import SplitMix64

SCHEMA_VERSION = "3.0.0"
LIBRARY_VERSION = "0.2.0"
ALGORITHM = "cc-gen-3"
MIN_SCALE_PCT = 80
MAX_SCALE_PCT = 125
GIRTH_TOLERANCE_PCT = 10


class GenerationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def length_fits(part_mm: int, branch_mm: int) -> bool:
    return MIN_SCALE_PCT * part_mm <= 100 * branch_mm <= MAX_SCALE_PCT * part_mm


def girth_fits(part: dict[str, Any], branch: dict[str, Any]) -> bool:
    """Compare girth after the part's single uniform length scale is applied."""
    part_length = int(part["length_mm"])
    branch_length = int(branch["length_mm"])
    part_girth = int(part["girth_mm"])
    branch_girth = int(branch["girth_mm"])
    scaled_numerator = part_girth * branch_length
    target_numerator = branch_girth * part_length
    tolerance = GIRTH_TOLERANCE_PCT
    return (
        (100 - tolerance) * target_numerator
        <= 100 * scaled_numerator
        <= (100 + tolerance) * target_numerator
    )


def side_fits(part_side: str, branch_side: str) -> bool:
    return part_side == "symmetric" or part_side == branch_side


def part_accepted(part: dict[str, Any], branch: dict[str, Any]) -> bool:
    accepts = branch["accepts"]
    if part["category"] not in accepts["categories"]:
        return False
    if accepts["templates"] and part["template"] not in accepts["templates"]:
        return False
    if accepts["tags_any"] and not set(accepts["tags_any"]) & set(part["species_tags"]):
        return False
    for field in ("binding_profile_id", "binding_profile_version", "binding_profile_hash"):
        if part.get(field) != branch.get(field):
            return False
    return (
        side_fits(part["side"], branch["side"])
        and length_fits(int(part["length_mm"]), int(branch["length_mm"]))
        and girth_fits(part, branch)
    )


def connector_accepted(part: dict[str, Any], branch: dict[str, Any]) -> bool:
    if part.get("category") != "connector" or not branch.get("connector_interface"):
        return False
    for field in ("binding_profile_id", "binding_profile_version", "binding_profile_hash"):
        if part.get(field) != branch.get(field):
            return False
    if not side_fits(part.get("side", ""), branch.get("side", "")):
        return False
    if int(part.get("girth_mm", -1)) != int(branch.get("girth_mm", -2)):
        return False
    interface = part.get("connector_interface") or {}
    target = branch["connector_interface"]
    return (
        interface.get("interface_id") == target.get("interface_id") == CONNECTOR_INTERFACE_ID
        and interface.get("interface_version") == target.get("interface_version") == CONNECTOR_INTERFACE_VERSION
        and interface.get("bone_groups")
        == [{"group": "b0", "role": "parent"}, {"group": "b1", "role": "child"}]
        and interface.get("max_influences") == 2
        and interface.get("weights_normalized") is True
        and interface.get("position_m") == target.get("position_m") == [0.0, 0.0, 0.0]
        and interface.get("rotation_xyzw") == target.get("rotation_xyzw") == [0.0, 0.0, 0.0, 1.0]
        and part.get("connector_span_m", [0.0, 0.0])[0] < 0
        and part.get("connector_span_m", [0.0, 0.0])[1] > 0
        and abs(round(float(part.get("connector_radius_m", 0.0)) * 2000) - int(branch["girth_mm"])) <= 1
    )


def _part_usable(part: dict[str, Any]) -> bool:
    inventory_kind = part.get("inventory_kind")
    status = part.get("status")
    return (
        inventory_kind == "reference" and status == "reference"
    ) or (
        inventory_kind == "production" and status == "approved"
    )


def _skeleton_usable(skeleton: dict[str, Any]) -> bool:
    return skeleton.get("status") == "approved"


def _require_catalog(catalog: dict[str, Any]) -> None:
    version = catalog.get("schema_version")
    if version != SCHEMA_VERSION:
        raise GenerationError("CC_GEN_SCHEMA_VERSION", f"expected {SCHEMA_VERSION}, got {version}")
    library_version = catalog.get("version")
    if library_version != LIBRARY_VERSION:
        raise GenerationError("CC_GEN_LIBRARY_VERSION", f"expected {LIBRARY_VERSION}, got {library_version}")
    algorithm = catalog.get("generator", {}).get("algorithm")
    if algorithm != ALGORITHM:
        raise GenerationError("CC_GEN_ALGORITHM", f"expected {ALGORITHM}, got {algorithm}")


def generate(catalog: dict[str, Any], pool_id: str, seed: int) -> dict[str, Any]:
    _require_catalog(catalog)
    pool = next((item for item in catalog["pools"] if item["pool_id"] == pool_id), None)
    if pool is None:
        raise GenerationError("CC_GEN_UNKNOWN_POOL", pool_id)
    rng = SplitMix64(seed)
    skeletons = sorted(
        (
            skeleton
            for skeleton in catalog["skeletons"]
            if _skeleton_usable(skeleton)
            and (skeleton["family"] in pool["families"] or skeleton["skeleton_id"] in pool["skeleton_ids"])
        ),
        key=lambda skeleton: skeleton["skeleton_id"],
    )
    if not skeletons:
        raise GenerationError("CC_GEN_NO_SKELETON", pool_id)
    return _fill(catalog, rng.pick(skeletons), rng, pool_id, seed)


def generate_for_skeleton(catalog: dict[str, Any], skeleton_id: str, seed: int) -> dict[str, Any]:
    """Fill an explicitly selected skeleton, including drafts used for anatomy review."""
    _require_catalog(catalog)
    skeleton = next((item for item in catalog["skeletons"] if item["skeleton_id"] == skeleton_id), None)
    if skeleton is None:
        raise GenerationError("CC_GEN_UNKNOWN_SKELETON", skeleton_id)
    return _fill(
        catalog,
        skeleton,
        SplitMix64(seed),
        f"review_{skeleton_id}",
        seed,
        dedicated_references=True,
    )


def _scales(part: dict[str, Any], branch: dict[str, Any]) -> tuple[float, float]:
    length_scale = int(branch["length_mm"]) / int(part["length_mm"])
    scaled_girth = int(part["girth_mm"]) * length_scale
    girth_scale = int(branch["girth_mm"]) / scaled_girth
    return round(length_scale, 6), round(girth_scale, 6)


def _fill(
    catalog: dict[str, Any],
    skeleton: dict[str, Any],
    rng: SplitMix64,
    pool_id: str,
    seed: int,
    *,
    dedicated_references: bool = False,
) -> dict[str, Any]:
    parts = sorted((part for part in catalog["parts"] if _part_usable(part)), key=lambda part: part["part_id"])
    by_id = {part["part_id"]: part for part in parts}
    body_parts = [part for part in parts if part["category"] != "connector"]
    connectors = [part for part in parts if part["category"] == "connector"]
    budget = int(catalog["limits"]["max_triangles"])
    connector_slots = max(0, int(catalog["limits"]["max_parts"]) - len(skeleton["branches"]))
    filled: dict[str, str] = {}
    fills: list[dict[str, Any]] = []
    for branch in skeleton["branches"]:
        branch_id = branch["branch_id"]
        if branch["parent_branch"] and branch["parent_branch"] not in filled:
            if branch["required"]:
                raise GenerationError("CC_GEN_REQUIRED_PARENT", f"{skeleton['skeleton_id']}.{branch_id}")
            continue
        chosen: dict[str, Any] | None = None
        if dedicated_references:
            if not branch["required"] and not rng.roll(branch["optional_fill_pct"]):
                continue
            dedicated_id = reference_part_id(skeleton["skeleton_id"], branch_id)
            candidate = by_id.get(dedicated_id)
            if (
                candidate is None
                or candidate["max_triangles"] > budget
                or not part_accepted(candidate, branch)
            ):
                raise GenerationError(
                    "CC_GEN_REFERENCE_MISSING", f"{skeleton['skeleton_id']}.{branch_id}"
                )
            chosen = candidate
        else:
            mirror = branch["mirror_of"]
            if mirror and mirror in filled and rng.roll(skeleton["symmetry_pct"]):
                candidate = by_id[filled[mirror]]
                if candidate["max_triangles"] <= budget and part_accepted(candidate, branch):
                    chosen = candidate
        if chosen is None:
            if not branch["required"] and not rng.roll(branch["optional_fill_pct"]):
                continue
            candidates = [
                part for part in body_parts
                if part_accepted(part, branch) and part["max_triangles"] <= budget
            ]
            if not candidates:
                if branch["required"]:
                    raise GenerationError("CC_GEN_NO_CANDIDATE", f"{skeleton['skeleton_id']}.{branch_id}")
                continue
            chosen = rng.pick(candidates)
        budget -= chosen["max_triangles"]
        connector_id = ""
        if branch["connector_size_class"] and connector_slots:
            if dedicated_references:
                dedicated_connector = by_id.get(
                    reference_part_id(skeleton["skeleton_id"], branch_id, connector=True)
                )
                connector_candidates = (
                    [dedicated_connector]
                    if dedicated_connector is not None
                    and connector_accepted(dedicated_connector, branch)
                    and dedicated_connector["max_triangles"] <= budget
                    else []
                )
            else:
                connector_candidates = [
                    connector for connector in connectors
                    if connector_accepted(connector, branch)
                    and connector["max_triangles"] <= budget
                ]
            if connector_candidates:
                connector = (
                    connector_candidates[0]
                    if dedicated_references
                    else rng.pick(connector_candidates)
                )
                connector_id = connector["part_id"]
                budget -= connector["max_triangles"]
                connector_slots -= 1
        filled[branch_id] = chosen["part_id"]
        length_scale, girth_scale = _scales(chosen, branch)
        fills.append({
            "branch_id": branch_id,
            "part_id": chosen["part_id"],
            "connector_part_id": connector_id,
            "binding_profile_id": branch["binding_profile_id"],
            "binding_profile_version": branch["binding_profile_version"],
            "binding_profile_hash": branch["binding_profile_hash"],
            "length_scale": length_scale,
            "girth_scale": girth_scale,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "recipe_id": f"gen_{pool_id}_{seed}",
        "library_id": catalog["library_id"],
        "library_version": catalog["version"],
        "generator": ALGORITHM,
        "pool_id": pool_id,
        "seed": seed,
        "skeleton_id": skeleton["skeleton_id"],
        "fills": fills,
    }


def canonical(recipe: dict[str, Any]) -> str:
    body = ";".join(
        f"{fill['branch_id']}={fill['part_id']}+{fill['connector_part_id'] or '-'}"
        for fill in recipe["fills"]
    )
    return f"{recipe['skeleton_id']}|{body}"
