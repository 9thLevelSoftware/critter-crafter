"""Recipe generator cc-gen-2 (docs/generator.md). Operates on a compiled catalog document.

Must stay decision-for-decision identical to RecipeGenerator.cs; tests/golden pins both.
"""

from __future__ import annotations

from typing import Any

from .rng import SplitMix64

ALGORITHM = "cc-gen-2"


class GenerationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def length_fits(part_mm: int, branch_mm: int) -> bool:
    return 4 * part_mm <= 5 * branch_mm and 4 * branch_mm <= 5 * part_mm


def part_accepted(part: dict[str, Any], branch: dict[str, Any]) -> bool:
    acc = branch["accepts"]
    if part["category"] not in acc["categories"]:
        return False
    if acc["templates"] and part["template"] not in acc["templates"]:
        return False
    if acc["tags_any"] and not set(acc["tags_any"]) & set(part["species_tags"]):
        return False
    return length_fits(part["length_mm"], branch["length_mm"])


def _usable(p: dict[str, Any]) -> bool:
    return p["status"] != "draft"


def generate(catalog: dict[str, Any], pool_id: str, seed: int) -> dict[str, Any]:
    pool = next((p for p in catalog["pools"] if p["pool_id"] == pool_id), None)
    if pool is None:
        raise GenerationError("CC_GEN_UNKNOWN_POOL", pool_id)
    rng = SplitMix64(seed)
    skeletons = sorted(
        (s for s in catalog["skeletons"]
         if _usable(s) and (s["family"] in pool["families"] or s["skeleton_id"] in pool["skeleton_ids"])),
        key=lambda s: s["skeleton_id"],
    )
    if not skeletons:
        raise GenerationError("CC_GEN_NO_SKELETON", pool_id)
    skeleton = rng.pick(skeletons)

    parts = sorted((p for p in catalog["parts"] if _usable(p)), key=lambda p: p["part_id"])
    by_id = {p["part_id"]: p for p in parts}
    body_parts = [p for p in parts if p["category"] != "connector"]
    connectors = [p for p in parts if p["category"] == "connector"]

    budget = int(catalog["limits"]["max_triangles"])
    filled: dict[str, str] = {}
    fills: list[dict[str, str]] = []
    for br in skeleton["branches"]:
        bid = br["branch_id"]
        if br["parent_branch"] and br["parent_branch"] not in filled:
            continue
        chosen: dict[str, Any] | None = None
        mirror = br["mirror_of"]
        if mirror and mirror in filled and rng.roll(skeleton["symmetry_pct"]):
            cand = by_id[filled[mirror]]
            if cand["max_triangles"] <= budget and part_accepted(cand, br):
                chosen = cand
        if chosen is None:
            if not br["required"] and not rng.roll(br["optional_fill_pct"]):
                continue
            cands = [p for p in body_parts if part_accepted(p, br) and p["max_triangles"] <= budget]
            if not cands:
                if br["required"]:
                    raise GenerationError("CC_GEN_NO_CANDIDATE", f"{skeleton['skeleton_id']}.{bid}")
                continue
            chosen = rng.pick(cands)
        budget -= chosen["max_triangles"]
        connector_id = ""
        if br["connector_size_class"]:
            ccands = [c for c in connectors
                      if c["size_class"] == br["connector_size_class"] and c["max_triangles"] <= budget]
            if ccands:
                conn = rng.pick(ccands)
                connector_id = conn["part_id"]
                budget -= conn["max_triangles"]
        filled[bid] = chosen["part_id"]
        fills.append({"branch_id": bid, "part_id": chosen["part_id"], "connector_part_id": connector_id})

    return {
        "schema_version": "2.0.0",
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
    body = ";".join(f"{f['branch_id']}={f['part_id']}+{f['connector_part_id'] or '-'}" for f in recipe["fills"])
    return f"{recipe['skeleton_id']}|{body}"
