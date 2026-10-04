"""Explicit, quantized morphology authoring without changing curated seed streams."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Any

from . import amalgam, archetypes


class MorphologyError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class MorphologyParams:
    blend: float = 1.0
    height: float = 1.0
    length: float = 1.0
    width: float = 1.0
    limb: float = 1.0
    stance: float = 1.0
    topology_count: int | None = None
    appendages: bool = True
    body_length: float = 1.3
    fat_ratio: float = .33
    clearance_ratio: float = .65
    head_growth: str = "ordinary"

    def quantized(self) -> MorphologyParams:
        values = asdict(self)
        for key in ("blend", "height", "length", "width", "limb", "stance",
                    "body_length", "fat_ratio", "clearance_ratio"):
            value = values[key]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise MorphologyError(f"CC_MORPHOLOGY_PARAMETER: {key} is not finite numeric")
            values[key] = math.floor(value * 10000 + .5) / 10000
        if type(values["appendages"]) is not bool:
            raise MorphologyError("CC_MORPHOLOGY_PARAMETER: appendages must be boolean")
        return replace(self, **values)


CURATED_RANGES = {"blend": (0., 2.), "height": (.94, 1.06), "length": (.92, 1.10),
                  "width": (.92, 1.10), "limb": (.93, 1.08), "stance": (.95, 1.05)}
AMALGAM_RANGES = {"body_length": (.9, 1.7), "fat_ratio": (.26, .40), "clearance_ratio": (.45, .85)}
LINEAGES = tuple(sorted(archetypes.ARCHETYPES)) + tuple(f"amalgam_{mode}" for mode in amalgam.MODES)


def parameter_ranges(lineage: str) -> dict[str, tuple[float, float]]:
    if lineage.startswith("amalgam_"):
        return {key: bounds for key, bounds in AMALGAM_RANGES.items()
                if key != "clearance_ratio" or lineage == "amalgam_walker"}
    return dict(CURATED_RANGES)


def count_range(lineage: str) -> tuple[int, int] | None:
    if lineage.startswith("amalgam_"):
        return {"amalgam_walker": (2, 6), "amalgam_hauled": (1, 3), "amalgam_slither": (0, 0)}[lineage]
    anatomy = archetypes.ARCHETYPES[lineage]
    if anatomy["variant"] == "tripod":
        return 3, 6
    if anatomy["plan"] in {"crawler", "hexapod"}:
        return 3, 5
    if anatomy["plan"] == "radial":
        return 3, 8
    return None


def choices(lineage: str) -> dict[str, tuple[Any, ...]]:
    return ({"head_growth": ("ordinary", *amalgam.HEAD_GROWTHS), "appendages": (False, True)}
            if lineage.startswith("amalgam_") else {"appendages": (False, True)})


def morphology_identity(lineage: str, seed: int, params: MorphologyParams) -> tuple[str, dict[str, Any]]:
    if lineage not in LINEAGES or type(seed) is not int or seed < 0:
        raise MorphologyError("CC_MORPHOLOGY_PARAMETER: unknown lineage or invalid seed")
    params = params.quantized()
    for field, (low, high) in parameter_ranges(lineage).items():
        value = getattr(params, field)
        if not low <= value <= high:
            raise MorphologyError(f"CC_MORPHOLOGY_PARAMETER: {field} outside [{low},{high}]")
    counts = count_range(lineage)
    if counts is None:
        if params.topology_count is not None:
            raise MorphologyError("CC_MORPHOLOGY_PARAMETER: count is anatomically authored")
    elif params.topology_count is not None and (type(params.topology_count) is not int or not counts[0] <= params.topology_count <= counts[1]):
        raise MorphologyError("CC_MORPHOLOGY_PARAMETER: topology count outside lineage range")
    for field, allowed in choices(lineage).items():
        if getattr(params, field) not in allowed:
            raise MorphologyError(f"CC_MORPHOLOGY_PARAMETER: invalid {field}")
    if lineage.startswith("amalgam_") and params.head_growth == "limb_tip" and not params.appendages:
        raise MorphologyError("CC_MORPHOLOGY_PARAMETER: limb-tip head requires an authored appendage")
    values = asdict(params)
    payload = {"archetype_id": lineage, "seed": seed,
               "parameters": {key: (math.floor(value * 10000 + .5) if isinstance(value, float) else value)
                              for key, value in values.items()}}
    return hashlib.sha256(canonical(payload)).hexdigest(), payload


def build_morphology(archetype_id: str, seed: int, params: MorphologyParams) -> dict[str, Any]:
    params = params.quantized()
    digest, payload = morphology_identity(archetype_id, seed, params)
    skeleton_id = f"{archetype_id}_m{digest[:12]}_v3"
    provenance = {"generator": "cc-gen-3", "seed": seed, "preset": "morphology",
                  "morphology_sha256": digest, "morphology": payload}
    if archetype_id.startswith("amalgam_"):
        source = amalgam._build_amalgam(seed, mode=archetype_id.removeprefix("amalgam_"),
            body_length=params.body_length, fat_ratio=params.fat_ratio, clearance_ratio=params.clearance_ratio,
            working_count=params.topology_count, growth=params.head_growth, skeleton_id=skeleton_id, provenance=provenance,
            appendages=params.appendages)
    else:
        shape = archetypes._blend_shape(params.blend)
        for field in ("height", "length", "width", "limb", "stance"):
            shape[field] *= getattr(params, field)
        shape["radial"] *= params.length
        source = archetypes._build(archetype_id, shape, seed, "anatomical", skeleton_id, provenance,
                                   topology_count=params.topology_count, appendages=params.appendages)
    branches = source["branches"]
    if source["family"] == "amalgam" and len(branches) > amalgam.MAX_BRANCHES:
        raise MorphologyError("CC_MORPHOLOGY_BUDGET: amalgam branch budget")
    parts = len(branches) + sum(branch.get("connector_size_class") is not None for branch in branches)
    bones = source["anatomy"]["budgets"]["bones"]
    triangles = 900 + bones * 155
    if parts > 16 or bones > 120 or triangles > 30000:
        raise MorphologyError(f"CC_MORPHOLOGY_BUDGET: parts={parts}, bones={bones}, triangles={triangles}")
    return source
