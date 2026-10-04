from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from critter_crafter.library.bake import (
    SCHEMA, _fresh, bake_recipe, compact, confined, digest, index_input_paths, recipe_key,
)
from critter_crafter.recipes.generator import canonical, generate_for_skeleton
from critter_crafter.library.commands import pack_files


@pytest.fixture
def review(tmp_path):
    catalog = json.loads((Path(__file__).parent / "golden_v3/catalog.json").read_text(encoding="utf-8"))
    recipe = generate_for_skeleton(catalog, catalog["skeletons"][0]["skeleton_id"], 1)
    (tmp_path / "catalog.json").write_bytes(compact(catalog))
    return catalog, recipe


@pytest.mark.parametrize("field,value,error", [
    ("generator", "forged", "CC_RECIPE_GENERATOR"),
    ("library_version", "0.0.1", "CC_RECIPE_LIBRARY_VERSION"),
    ("girth_scale", 1.001, "CC_GIRTH_SCALE"),
    ("binding_profile_hash", "f" * 64, "CC_BINDING_IDENTITY"),
])
def test_composition_hit_cannot_bypass_requested_recipe_validation(tmp_path, review, monkeypatch, field, value, error):
    _, original = review
    forged = copy.deepcopy(original)
    if field in {"girth_scale", "binding_profile_hash"}:
        forged["fills"][0][field] = value
        assert canonical(forged) == canonical(original)
    else:
        forged[field] = value
    def forbidden(*args, **kwargs):
        pytest.fail("invalid recipe reached Blender")
    monkeypatch.setattr("critter_crafter.library.bake.run_op", forbidden)
    with pytest.raises(ValueError, match=error):
        bake_recipe(forged, tmp_path, allow_review=True)
    assert not (tmp_path / "baked_creatures").exists()


def test_review_permission_is_checked_even_when_composition_matches(tmp_path, review):
    _, recipe = review
    with pytest.raises(ValueError, match="CC_SKELETON_NOT_APPROVED"):
        bake_recipe(recipe, tmp_path)
    assert not (tmp_path / "baked_creatures").exists()


def test_identity_uses_composition_not_seed_or_recipe_id(review):
    _, recipe = review
    other = copy.deepcopy(recipe)
    other["recipe_id"] = "other"
    other["seed"] += 100
    assert recipe_key(other) == recipe_key(recipe)
    expected = digest((recipe["library_id"] + "\n" + recipe["library_version"] + "\n" + canonical(recipe)).encode())
    assert recipe_key(recipe) == expected
    other["fills"][0]["part_id"] += "_different"
    assert recipe_key(other) != recipe_key(recipe)


@pytest.mark.parametrize("path", ["../secret.png", "parts/../../secret.png", "C:/secret.png", "/tmp/secret.png", "parts\\secret.png", "parts//secret.png"])
def test_dependencies_cannot_escape_portable_library(tmp_path, path):
    with pytest.raises(ValueError, match="CC_BAKE_PATH"):
        confined(tmp_path, path)


def _indexed(root):
    catalog = {"library_id": "fixture", "version": "0.3.0"}
    catalog_bytes = compact(catalog)
    (root / "catalog.json").write_bytes(catalog_bytes)
    key = digest(b"fixture\n0.3.0\nskeleton:composition")
    folder = root / "baked_creatures" / key
    folder.mkdir(parents=True)
    (root / "source.blend").write_bytes(b"authoritative bind source")
    (root / "source.png").write_bytes(b"original texture")
    material = f"baked_creatures/{key}/material.json"
    (root / material).write_bytes(compact({"opaque": True, "atlas_size": 4096}))
    paths = sorted(["catalog.json", "source.blend", "source.png", material])
    rows = [{"path": path, "sha256": digest((root / path).read_bytes())} for path in paths]
    entry = {"key": key, "skeleton_id": "skeleton", "composition": "skeleton:composition",
             "inputs": rows, "source_sha256": digest(compact(rows)), "producer_sha256": "a" * 64,
             "material_sha256": digest((root / material).read_bytes()),
             "fbx": f"baked_creatures/{key}/{key}.fbx", "albedo_png": f"baked_creatures/{key}/{key}_albedo.png"}
    for field, hash_field in (("fbx", "asset_sha256"), ("albedo_png", "texture_sha256")):
        (root / entry[field]).write_bytes(field.encode())
        entry[hash_field] = digest((root / entry[field]).read_bytes())
    index = {"schema_version": SCHEMA, "library_id": "fixture", "library_version": "0.3.0",
             "source_catalog_sha256": digest(catalog_bytes), "entries": [entry]}
    (root / "baked_creatures/index.json").write_bytes(compact(index))
    return entry, index


def test_slim_package_retains_bind_and_texture_dependencies(tmp_path):
    entry, _ = _indexed(tmp_path)
    assert set(index_input_paths(tmp_path)) == {"baked_creatures/index.json", entry["fbx"], entry["albedo_png"]} | {row["path"] for row in entry["inputs"]}


@pytest.mark.parametrize("changed", ["source.blend", "source.png", "catalog.json", "material", "fbx", "albedo_png"])
def test_pack_rejects_stale_source_material_or_output(tmp_path, changed):
    entry, _ = _indexed(tmp_path)
    path = (f"baked_creatures/{entry['key']}/material.json" if changed == "material" else entry.get(changed, changed))
    (tmp_path / path).write_bytes(b"changed" if changed != "catalog.json" else compact({"library_id": "fixture", "version": "0.3.0", "changed": True}))
    with pytest.raises(ValueError, match="CC_BAKE_STALE"):
        index_input_paths(tmp_path)


def test_cache_invalidates_on_producer_source_material_and_output_changes(tmp_path):
    entry, _ = _indexed(tmp_path)
    expected = {key: entry[key] for key in ("source_sha256", "producer_sha256", "material_sha256")}
    assert _fresh(tmp_path, entry, expected)
    for key in expected:
        assert not _fresh(tmp_path, entry, {**expected, key: "b" * 64})
    (tmp_path / entry["fbx"]).write_bytes(b"modified mesh")
    assert not _fresh(tmp_path, entry, expected)


def test_index_rejects_forged_material_claim_and_duplicate_keys(tmp_path):
    entry, index = _indexed(tmp_path)
    entry["material_sha256"] = "c" * 64
    (tmp_path / "baked_creatures/index.json").write_bytes(compact(index))
    with pytest.raises(ValueError, match="CC_BAKE_STALE"):
        index_input_paths(tmp_path)
    index["entries"].append(copy.deepcopy(entry))
    (tmp_path / "baked_creatures/index.json").write_bytes(compact(index))
    with pytest.raises(ValueError, match="duplicate"):
        index_input_paths(tmp_path)


def _publication_library(root, review, monkeypatch):
    catalog, first = review
    second = generate_for_skeleton(catalog, catalog["skeletons"][1]["skeleton_id"], 1)
    recipes = [first, second]
    selected_skeletons = {recipe["skeleton_id"] for recipe in recipes}
    selected_parts = {fill[field] for recipe in recipes for fill in recipe["fills"]
                      for field in ("part_id", "connector_part_id") if fill[field]}
    for skeleton in catalog["skeletons"]:
        if skeleton["skeleton_id"] in selected_skeletons:
            name = skeleton["skeleton_id"]
            skeleton["asset"] = {"fbx": f"skeletons/{name}/{name}.fbx",
                                 "blend": f"skeletons/{name}/{name}.blend"}
            for path in skeleton["asset"].values():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(b"authoritative skeleton bind and clips")
    for part in catalog["parts"]:
        if part["part_id"] in selected_parts:
            path = f"parts/{part['part_id']}/{part['part_id']}.fbx"
            part["asset"] = {"fbx": path}
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(b"source part geometry and material")
    (root / "catalog.json").write_bytes(compact(catalog))

    def offline_geometry(op, args):
        if args.get("inspect"):
            return {"result": {"materials": [{"opaque": True, "color": [.3, .2, .1]}],
                               "texture_paths": []}}
        Path(args["out_fbx"]).write_bytes(b"offline optimized body")
        Path(args["out_png"]).write_bytes(b"offline opaque atlas")
        return {"result": {"triangles": 12, "bones": 4, "materials": 1, "max_influences": 2}}

    monkeypatch.setattr("critter_crafter.library.bake.run_op", offline_geometry)
    for recipe in recipes:
        bake_recipe(recipe, root, allow_review=True)
    return catalog, recipes


def test_catalog_repair_retains_other_entry_until_every_composition_is_revalidated(tmp_path, review, monkeypatch):
    catalog, recipes = _publication_library(tmp_path, review, monkeypatch)
    index_path = tmp_path / "baked_creatures/index.json"
    original = json.loads(index_path.read_bytes())
    original_entries = {entry["key"]: entry for entry in original["entries"]}
    catalog["limits"]["target_triangles"] += 1
    (tmp_path / "catalog.json").write_bytes(compact(catalog))
    current_catalog_hash = digest((tmp_path / "catalog.json").read_bytes())

    bake_recipe(recipes[0], tmp_path, allow_review=True)
    partial = json.loads(index_path.read_bytes())
    partial_entries = {entry["key"]: entry for entry in partial["entries"]}
    assert set(partial_entries) == set(original_entries)
    assert partial_entries[recipe_key(recipes[1])] == original_entries[recipe_key(recipes[1])]
    assert partial["source_catalog_sha256"] == original["source_catalog_sha256"]
    with pytest.raises(ValueError, match="CC_BAKE_STALE: catalog changed"):
        index_input_paths(tmp_path)

    bake_recipe(recipes[1], tmp_path, allow_review=True)
    completed = json.loads(index_path.read_bytes())
    assert completed["source_catalog_sha256"] == current_catalog_hash
    assert {entry["key"] for entry in completed["entries"]} == set(original_entries)
    assert all(next(row["sha256"] for row in entry["inputs"] if row["path"] == "catalog.json")
               == current_catalog_hash for entry in completed["entries"])
    expected_paths = {"baked_creatures/index.json"}
    for entry in completed["entries"]:
        expected_paths.update(row["path"] for row in entry["inputs"])
        expected_paths.update((entry["fbx"], entry["albedo_png"]))
    assert set(index_input_paths(tmp_path)) == expected_paths


def test_repairing_one_entry_never_hides_another_stale_composition_as_a_missing_bake(tmp_path, review, monkeypatch):
    catalog, recipes = _publication_library(tmp_path, review, monkeypatch)
    index_path = tmp_path / "baked_creatures/index.json"
    original = json.loads(index_path.read_bytes())
    original_entries = {entry["key"]: entry for entry in original["entries"]}
    second_skeleton = next(s for s in catalog["skeletons"] if s["skeleton_id"] == recipes[1]["skeleton_id"])
    (tmp_path / second_skeleton["asset"]["blend"]).write_bytes(b"changed authoritative bind and clips")

    bake_recipe(recipes[0], tmp_path, allow_review=True, force=True)
    partial = json.loads(index_path.read_bytes())
    partial_entries = {entry["key"]: entry for entry in partial["entries"]}
    assert set(partial_entries) == set(original_entries)
    assert partial_entries[recipe_key(recipes[1])] == original_entries[recipe_key(recipes[1])]
    assert partial["source_catalog_sha256"] == original["source_catalog_sha256"]
    with pytest.raises(ValueError, match="CC_BAKE_STALE: source dependency changed"):
        index_input_paths(tmp_path)

    repaired = bake_recipe(recipes[1], tmp_path, allow_review=True)
    assert repaired["source_sha256"] != original_entries[recipe_key(recipes[1])]["source_sha256"]
    assert {entry["key"] for entry in json.loads(index_path.read_bytes())["entries"]} == set(original_entries)
    packaged = set(index_input_paths(tmp_path))
    assert {entry["fbx"] for entry in original_entries.values()} <= packaged


@pytest.mark.parametrize("slim", [False, True])
@pytest.mark.parametrize("corruption,error", [("format", "CC_BAKE_INDEX"), ("source", "CC_BAKE_STALE")])
def test_both_package_modes_reject_malformed_or_stale_indexed_dependencies(tmp_path, slim, corruption, error):
    entry, index = _indexed(tmp_path)
    valid_package = {path.relative_to(tmp_path).as_posix() for path in pack_files(tmp_path, slim=slim)}
    assert {"baked_creatures/index.json", entry["fbx"], entry["albedo_png"],
            *[row["path"] for row in entry["inputs"]]} <= valid_package
    if corruption == "format":
        index["schema_version"] = "unsupported-bake-format"
        (tmp_path / "baked_creatures/index.json").write_bytes(compact(index))
    else:
        (tmp_path / "source.png").write_bytes(b"changed original material texture")
    with pytest.raises(ValueError, match=error):
        pack_files(tmp_path, slim=slim)
