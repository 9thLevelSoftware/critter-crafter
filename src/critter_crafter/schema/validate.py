"""Schema + cross-record validation of the authoring sources in data/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..library.catalog import CatalogError, compile_catalog, load_sources
from ..recipes.generator import part_accepted

SCHEMA_FOR = {
    "branch_templates": "branch_template.v2.schema.json",
    "skeletons": "skeleton.v2.schema.json",
    "parts": "part.v2.schema.json",
}


def _validator(schemas_dir: Path, name: str) -> Draft202012Validator:
    with (schemas_dir / name).open(encoding="utf-8") as f:
        return Draft202012Validator(json.load(f))


def _schema_diags(validator: Draft202012Validator, doc: Any, where: str) -> list[str]:
    return [
        f"CC_SCHEMA: {where}: {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in validator.iter_errors(doc)
    ]


def validate_sources(data_dir: Path, schemas_dir: Path) -> tuple[list[str], dict[str, Any] | None]:
    """Return (sorted diagnostics, compiled catalog or None)."""
    diags: list[str] = []
    for sub, schema in SCHEMA_FOR.items():
        v = _validator(schemas_dir, schema)
        pattern = "*.json" if sub == "branch_templates" else f"*.{sub[:-1]}.json"
        for p in sorted((data_dir / sub).glob(pattern)):
            with p.open(encoding="utf-8") as f:
                doc = json.load(f)
            diags += _schema_diags(v, doc, f"{sub}/{p.name}")
            key = {"branch_templates": "template_id", "skeletons": "skeleton_id", "parts": "part_id"}[sub]
            stem = p.name.split(".")[0]
            if doc.get(key) != stem:
                diags.append(f"CC_ID_FILENAME: {sub}/{p.name}: {key}={doc.get(key)}")
    with (data_dir / "pools" / "pools.json").open(encoding="utf-8") as f:
        diags += _schema_diags(_validator(schemas_dir, "pools.v2.schema.json"), json.load(f), "pools/pools.json")
    if diags:
        return sorted(diags), None

    try:
        catalog = compile_catalog(load_sources(data_dir))
    except CatalogError as e:
        return [str(e)], None
    diags += cross_check(catalog)
    return sorted(diags), catalog


def cross_check(catalog: dict[str, Any]) -> list[str]:
    diags: list[str] = []
    templates = {t["template_id"] for t in catalog["branch_templates"]}
    parts = catalog["parts"]
    seen: set[str] = set()
    for p in parts:
        if p["part_id"] in seen:
            diags.append(f"CC_DUP_PART: {p['part_id']}")
        seen.add(p["part_id"])
        if p["template"] not in templates:
            diags.append(f"CC_UNKNOWN_TEMPLATE: part {p['part_id']}: {p['template']}")
        if p["category"] == "connector" and p["template"] != "connector2":
            diags.append(f"CC_CONNECTOR_TEMPLATE: {p['part_id']}")
    body = [p for p in parts if p["category"] != "connector" and p["status"] != "draft"]
    conn_sizes = {p["size_class"] for p in parts if p["category"] == "connector" and p["status"] != "draft"}
    families = {s["family"] for s in catalog["skeletons"]}
    for s in catalog["skeletons"]:
        sid = s["skeleton_id"]
        if len(s["bones"]) > catalog["limits"]["max_bones"]:
            diags.append(f"CC_BUDGET_BONES: {sid}")
        for b in s["branches"]:
            fits = [p["part_id"] for p in body if part_accepted(p, b)]
            if not fits:
                code = "CC_NO_CANDIDATE_REQUIRED" if b["required"] else "CC_NO_CANDIDATE_OPTIONAL"
                diags.append(f"{code}: {sid}.{b['branch_id']}")
            if b["connector_size_class"] and b["connector_size_class"] not in conn_sizes:
                diags.append(f"CC_NO_CONNECTOR_SIZE: {sid}.{b['branch_id']}: {b['connector_size_class']}")
    for pool in catalog["pools"]:
        if not (set(pool["families"]) & families) and not pool["skeleton_ids"]:
            diags.append(f"CC_EMPTY_POOL: {pool['pool_id']}")
    return diags
