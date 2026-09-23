"""Recipe validation for the schema-v3 compiled catalog."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from .generator import (
    ALGORITHM,
    LIBRARY_VERSION,
    SCHEMA_VERSION,
    _part_usable,
    _scales,
    connector_accepted,
    part_accepted,
)

_RECIPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "recipe_id", "library_id", "library_version", "generator",
        "pool_id", "seed", "skeleton_id", "fills",
    ],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "recipe_id": {"type": "string"},
        "library_id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
        "library_version": {"const": LIBRARY_VERSION},
        "generator": {"const": ALGORITHM},
        "pool_id": {"type": "string"},
        "seed": {"type": "integer"},
        "skeleton_id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
        "fills": {
            "type": "array", "maxItems": 16,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": [
                    "branch_id", "part_id", "connector_part_id", "binding_profile_id",
                    "binding_profile_version", "binding_profile_hash", "length_scale", "girth_scale",
                ],
                "properties": {
                    "branch_id": {"type": "string"}, "part_id": {"type": "string"},
                    "connector_part_id": {"type": "string"}, "binding_profile_id": {"type": "string"},
                    "binding_profile_version": {"type": "string", "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$"},
                    "binding_profile_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "length_scale": {"type": "number", "minimum": 0.8, "maximum": 1.25},
                    "girth_scale": {"type": "number", "minimum": 0.9, "maximum": 1.1},
                },
            },
        },
    },
}


def validate_recipe(
    catalog: dict[str, Any], recipe: dict[str, Any], *, allow_review: bool = False
) -> list[str]:
    """Return sorted ``CODE: detail`` diagnostics, rejecting versions first."""
    catalog_version = catalog.get("schema_version")
    if catalog_version != SCHEMA_VERSION:
        return [f"CC_LIBRARY_SCHEMA_VERSION: expected {SCHEMA_VERSION}, got {catalog_version}"]
    catalog_release = catalog.get("version")
    if catalog_release != LIBRARY_VERSION:
        return [f"CC_LIBRARY_VERSION: expected {LIBRARY_VERSION}, got {catalog_release}"]
    recipe_version = recipe.get("schema_version")
    if recipe_version != SCHEMA_VERSION:
        return [f"CC_RECIPE_SCHEMA_VERSION: expected {SCHEMA_VERSION}, got {recipe_version}"]
    if recipe.get("library_id") != catalog.get("library_id"):
        return [f"CC_RECIPE_LIBRARY_ID: expected {catalog.get('library_id')}, got {recipe.get('library_id')}"]
    if recipe.get("library_version") != catalog.get("version"):
        return [f"CC_RECIPE_LIBRARY_VERSION: expected {catalog.get('version')}, got {recipe.get('library_version')}"]
    if recipe.get("generator") != ALGORITHM:
        return [f"CC_RECIPE_GENERATOR: expected {ALGORITHM}, got {recipe.get('generator')}"]

    schema_errors = sorted(Draft202012Validator(_RECIPE_SCHEMA).iter_errors(recipe), key=lambda item: list(item.path))
    if schema_errors:
        return [
            f"CC_RECIPE_SCHEMA: {'/'.join(str(value) for value in error.path) or '<root>'}: {error.message}"
            for error in schema_errors
        ]

    diagnostics: list[str] = []
    skeleton = next(
        (item for item in catalog["skeletons"] if item["skeleton_id"] == recipe.get("skeleton_id")),
        None,
    )
    if skeleton is None:
        return [f"CC_UNKNOWN_SKELETON: {recipe.get('skeleton_id')}"]
    review_mode = allow_review and recipe.get("pool_id") == f"review_{skeleton['skeleton_id']}"
    if skeleton.get("status") == "rejected" or (not review_mode and skeleton.get("status") != "approved"):
        diagnostics.append(f"CC_SKELETON_NOT_APPROVED: {skeleton['skeleton_id']}")
    if not review_mode:
        pool = next((item for item in catalog.get("pools", []) if item.get("pool_id") == recipe.get("pool_id")), None)
        if pool is None:
            diagnostics.append(f"CC_UNKNOWN_POOL: {recipe.get('pool_id')}")
        elif skeleton["family"] not in pool.get("families", []) and skeleton["skeleton_id"] not in pool.get("skeleton_ids", []):
            diagnostics.append(f"CC_POOL_SKELETON: {recipe.get('pool_id')}={skeleton['skeleton_id']}")
    parts = {part["part_id"]: part for part in catalog["parts"]}
    branches = {branch["branch_id"]: branch for branch in skeleton["branches"]}
    filled: dict[str, dict[str, Any]] = {}
    triangles = 0
    connector_count = 0
    order = {branch["branch_id"]: index for index, branch in enumerate(skeleton["branches"])}
    last = -1
    for fill in recipe.get("fills", []):
        if not isinstance(fill, dict):
            diagnostics.append("CC_RECIPE_FILL_SHAPE: fill must be an object")
            continue
        required_fill = {
            "branch_id", "part_id", "connector_part_id", "binding_profile_id",
            "binding_profile_version", "binding_profile_hash", "length_scale", "girth_scale",
        }
        missing = sorted(required_fill - fill.keys())
        if missing:
            diagnostics.append(f"CC_RECIPE_FILL_SHAPE: missing {','.join(missing)}")
            continue
        branch_id = fill.get("branch_id")
        branch = branches.get(branch_id)
        if branch is None:
            diagnostics.append(f"CC_UNKNOWN_BRANCH: {branch_id}")
            continue
        if order[branch_id] <= last:
            diagnostics.append(f"CC_FILL_ORDER: {branch_id}")
        last = max(last, order[branch_id])
        if branch_id in filled:
            diagnostics.append(f"CC_BRANCH_OCCUPIED: {branch_id}")
            continue
        if branch["parent_branch"] and branch["parent_branch"] not in filled:
            diagnostics.append(f"CC_PARENT_UNFILLED: {branch_id}")
        part = parts.get(fill["part_id"])
        if part is None:
            diagnostics.append(f"CC_UNKNOWN_PART: {fill['part_id']}")
            continue
        if not _part_usable(part):
            diagnostics.append(f"CC_PART_NOT_APPROVED: {branch_id}={part['part_id']}")
        if part["category"] == "connector" or not part_accepted(part, branch):
            diagnostics.append(f"CC_PART_REJECTED: {branch_id}={part['part_id']}")
        for field in ("binding_profile_id", "binding_profile_version", "binding_profile_hash"):
            if fill.get(field) != branch[field]:
                diagnostics.append(f"CC_BINDING_IDENTITY: {branch_id}.{field}")
        expected_length, expected_girth = _scales(part, branch)
        if fill.get("length_scale") != expected_length:
            diagnostics.append(f"CC_LENGTH_SCALE: {branch_id}")
        if fill.get("girth_scale") != expected_girth:
            diagnostics.append(f"CC_GIRTH_SCALE: {branch_id}")
        triangles += part["max_triangles"]
        connector_id = fill.get("connector_part_id") or ""
        if connector_id:
            connector = parts.get(connector_id)
            if connector is None:
                diagnostics.append(f"CC_UNKNOWN_PART: {connector_id}")
            elif not _part_usable(connector):
                diagnostics.append(f"CC_PART_NOT_APPROVED: {branch_id}={connector_id}")
            elif not connector_accepted(connector, branch):
                diagnostics.append(f"CC_CONNECTOR_MISMATCH: {branch_id}={connector_id}")
            else:
                triangles += connector["max_triangles"]
                connector_count += 1
        filled[branch_id] = part
    for branch in skeleton["branches"]:
        if branch["required"] and branch["branch_id"] not in filled:
            diagnostics.append(f"CC_REQUIRED_UNFILLED: {branch['branch_id']}")
    limits = catalog["limits"]
    if triangles > limits["max_triangles"]:
        diagnostics.append(f"CC_BUDGET_TRIS: {triangles}>{limits['max_triangles']}")
    if len(skeleton["bones"]) > limits["max_bones"]:
        diagnostics.append(f"CC_BUDGET_BONES: {len(skeleton['bones'])}>{limits['max_bones']}")
    part_count = len(filled) + connector_count
    if part_count > limits["max_parts"]:
        diagnostics.append(f"CC_BUDGET_PARTS: {part_count}>{limits['max_parts']}")
    return sorted(diagnostics)
