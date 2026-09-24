"""Headless Blender integration: builds the whole placeholder library into a temp dir."""

import json

import pytest

from critter_crafter.blender.runner import run_op
from critter_crafter.config import find_blender, paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.library.commands import apply_results, build_jobs, real_part_source
from critter_crafter.library.export_validation import validate_glb_motion, validate_glb_rest, validate_glb_surface
from critter_crafter.skeletons.action_qa import evaluate_actions
from critter_crafter.skeletons.qa import evaluate_motion

pytestmark = pytest.mark.blender
if not find_blender():
    pytest.skip("Blender 5.x not found", allow_module_level=True)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("lib")
    catalog = compile_catalog(load_sources(paths().data))
    # Real parts need the private asset archive; without it the rest of the library is still built.
    catalog["parts"] = [p for p in catalog["parts"] if not p.get("real") or real_part_source(p)]
    jobs = build_jobs(catalog, out)
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
        assert (out / s["asset"]["blend"]).exists()
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


def test_all_skeleton_glbs_keep_the_compiled_rest_bind(built):
    catalog, out, _ = built
    for skeleton in catalog["skeletons"]:
        report = validate_glb_rest(out / skeleton["asset"]["glb"], skeleton)
        assert report["passed"], f"{skeleton['skeleton_id']}: {report['diagnostics']}"


def test_all_baked_motion_and_skinned_surfaces_pass_complete_qa(built):
    catalog, out, _ = built
    assert len(catalog["skeletons"]) == 39
    profiles = catalog.get("binding_profiles", [])
    for skeleton in catalog["skeletons"]:
        motion = json.loads((out / skeleton["asset"]["motion"]).read_text(encoding="utf-8"))
        glb = out / skeleton["asset"]["glb"]
        reports = {
            "motion": evaluate_motion(skeleton, motion, profiles=profiles),
            "actions": evaluate_actions(skeleton, motion),
            "glb_motion": validate_glb_motion(glb, skeleton, motion),
            "glb_surface": validate_glb_surface(glb, skeleton, motion),
        }
        for name in ("motion", "glb_motion", "glb_surface"):
            assert reports[name]["clips_checked"] == 8, (
                f"{skeleton['skeleton_id']} {name}: incomplete clip coverage: {reports[name]}"
            )
        for name, report in reports.items():
            assert report["diagnostics"] == [], (
                f"{skeleton['skeleton_id']} {name}: {report['diagnostics']}"
            )


def test_assembled_creature_renders_and_bakes_a_draft_review_recipe(built, tmp_path):
    from critter_crafter.recipes.generator import generate_for_skeleton

    catalog, out, _ = built
    skel = next(s for s in catalog["skeletons"] if s["status"] == "draft")
    recipe = generate_for_skeleton(catalog, skel["skeleton_id"], 3)
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


def test_reference_belly_hauler_arms_clear_ground_in_every_assembled_clip(built, tmp_path):
    from critter_crafter.recipes.generator import generate_for_skeleton

    catalog, out, _ = built
    part_by_id = {part["part_id"]: part for part in catalog["parts"]}
    for seed, skeleton_id in enumerate((
        "dragger_belly_hauler_balanced_v3",
        "dragger_belly_hauler_compact_v3",
        "dragger_belly_hauler_elongated_v3",
    ), start=1):
        skeleton = next(item for item in catalog["skeletons"] if item["skeleton_id"] == skeleton_id)
        recipe = generate_for_skeleton(catalog, skeleton_id, seed)
        used = {fill["part_id"] for fill in recipe["fills"]}
        used.update(fill["connector_part_id"] for fill in recipe["fills"] if fill["connector_part_id"])
        assembled = tmp_path / f"{skeleton_id}.glb"
        run_op("assemble", {
            "skeleton": skeleton,
            "parts": {part_id: part_by_id[part_id] for part_id in used},
            "library_dir": str(out), "recipe": recipe,
            "gait_profile": next(gait for gait in catalog["gait_profiles"]
                                 if gait["hint"] == skeleton["locomotion_hint"]),
            "out_glb": str(assembled),
        })
        motion = json.loads((out / skeleton["asset"]["motion"]).read_text(encoding="utf-8"))
        report = validate_glb_surface(assembled, skeleton, motion)
        assert report["clips_checked"] == 8
        assert report["passed"], report["diagnostics"]
