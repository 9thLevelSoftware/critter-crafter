"""Recipe validation against a compiled catalog. Mirrors RecipeValidator.cs (same CC_* codes)."""

from __future__ import annotations

from typing import Any

from .generator import part_accepted


def validate_recipe(catalog: dict[str, Any], recipe: dict[str, Any]) -> list[str]:
    """Return a sorted list of diagnostics 'CODE: detail'. Empty list == valid."""
    diags: list[str] = []
    skel = next((s for s in catalog["skeletons"] if s["skeleton_id"] == recipe.get("skeleton_id")), None)
    if skel is None:
        return [f"CC_UNKNOWN_SKELETON: {recipe.get('skeleton_id')}"]
    parts = {p["part_id"]: p for p in catalog["parts"]}
    branches = {b["branch_id"]: b for b in skel["branches"]}
    filled: dict[str, dict[str, Any]] = {}
    tris = 0
    order = {b["branch_id"]: i for i, b in enumerate(skel["branches"])}
    last = -1
    for f in recipe.get("fills", []):
        bid = f["branch_id"]
        br = branches.get(bid)
        if br is None:
            diags.append(f"CC_UNKNOWN_BRANCH: {bid}")
            continue
        if order[bid] <= last:
            diags.append(f"CC_FILL_ORDER: {bid}")
        last = max(last, order[bid])
        if bid in filled:
            diags.append(f"CC_BRANCH_OCCUPIED: {bid}")
            continue
        if br["parent_branch"] and br["parent_branch"] not in filled:
            diags.append(f"CC_PARENT_UNFILLED: {bid}")
        part = parts.get(f["part_id"])
        if part is None:
            diags.append(f"CC_UNKNOWN_PART: {f['part_id']}")
            continue
        if part["category"] == "connector" or not part_accepted(part, br):
            diags.append(f"CC_PART_REJECTED: {bid}={part['part_id']}")
        tris += part["max_triangles"]
        cid = f.get("connector_part_id") or ""
        if cid:
            conn = parts.get(cid)
            if conn is None:
                diags.append(f"CC_UNKNOWN_PART: {cid}")
            elif conn["category"] != "connector" or conn["size_class"] != br["connector_size_class"]:
                diags.append(f"CC_CONNECTOR_MISMATCH: {bid}={cid}")
            else:
                tris += conn["max_triangles"]
        filled[bid] = part
    for b in skel["branches"]:
        if b["required"] and b["branch_id"] not in filled:
            parent_ok = not b["parent_branch"] or b["parent_branch"] in filled
            if parent_ok:
                diags.append(f"CC_REQUIRED_UNFILLED: {b['branch_id']}")
    limits = catalog["limits"]
    if tris > limits["max_triangles"]:
        diags.append(f"CC_BUDGET_TRIS: {tris}>{limits['max_triangles']}")
    if len(skel["bones"]) > limits["max_bones"]:
        diags.append(f"CC_BUDGET_BONES: {len(skel['bones'])}>{limits['max_bones']}")
    if len(filled) > limits["max_parts"]:
        diags.append(f"CC_BUDGET_PARTS: {len(filled)}>{limits['max_parts']}")
    return sorted(diags)
