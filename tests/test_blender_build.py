"""Headless Blender integration: builds the whole placeholder library into a temp dir."""

import json

import pytest

from critter_crafter.blender.runner import run_op
from critter_crafter.config import find_blender, paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.library.commands import apply_results, build_jobs

pytestmark = pytest.mark.blender
if not find_blender():
    pytest.skip("Blender 5.x not found", allow_module_level=True)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("lib")
    catalog = compile_catalog(load_sources(paths().data))
    jobs = build_jobs(catalog, out)
    for j in jobs:
        j["args"].pop("out_blend", None)
    result = run_op("batch", {"jobs": jobs})
    problems = apply_results(catalog, out, result["results"])
    return catalog, out, problems


def test_build_has_no_problems(built):
    _, _, problems = built
    assert problems == []


def test_every_asset_exported(built):
    catalog, out, _ = built
    for s in catalog["skeletons"]:
        assert (out / s["asset"]["fbx"]).stat().st_size > 1000
        assert (out / s["asset"]["glb"]).exists()
    for p in catalog["parts"]:
        assert (out / p["asset"]["fbx"]).exists()
        assert 0 < p["asset"]["triangles"] <= p["max_triangles"]


def test_skeletons_carry_full_clip_set(built):
    catalog, _, _ = built
    expected = {"idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death"}
    for s in catalog["skeletons"]:
        assert {c["name"] for c in s["asset"]["clips"]} == expected
        loops = {c["name"] for c in s["asset"]["clips"] if c["loop"]}
        assert loops == {"idle", "walk", "run", "stun"}


def test_assembled_creature_renders_and_bakes(built, tmp_path):
    from critter_crafter.recipes.generator import generate

    catalog, out, _ = built
    recipe = generate(catalog, "any", 3)
    skel = next(s for s in catalog["skeletons"] if s["skeleton_id"] == recipe["skeleton_id"])
    used = {f["part_id"] for f in recipe["fills"]} | {f["connector_part_id"] for f in recipe["fills"] if f["connector_part_id"]}
    res = run_op("assemble", {
        "skeleton": skel, "parts": {p["part_id"]: p for p in catalog["parts"] if p["part_id"] in used},
        "library_dir": str(out), "recipe": recipe,
        "gait_profile": next(g for g in catalog["gait_profiles"] if g["hint"] == skel["locomotion_hint"]),
        "out_fbx": str(tmp_path / "baked.fbx"), "out_png": str(tmp_path / "p.png"), "size": 128,
    })["result"]
    assert (tmp_path / "baked.fbx").stat().st_size > 1000
    assert (tmp_path / "p.png").exists()
    expected = sum(p["asset"]["triangles"] for p in catalog["parts"] if p["part_id"] in used
                   for f in recipe["fills"] if p["part_id"] in (f["part_id"], f["connector_part_id"]))
    assert res["triangles"] == expected
