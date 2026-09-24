"""Headless Blender: committed real-part records rebuild reproducibly and deform like placeholders.

Skipped without Blender or without the private asset archive (the source meshes never live here).
"""

import json

import pytest

from critter_crafter.blender.runner import run_op
from critter_crafter.config import find_asset_archive, find_blender, paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.library.commands import build_jobs, real_part_problems

pytestmark = pytest.mark.blender
if not find_blender():
    pytest.skip("Blender 5.x not found", allow_module_level=True)
if not find_asset_archive():
    pytest.skip("asset archive not found (set CRITTER_ASSET_ARCHIVE)", allow_module_level=True)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("real")
    catalog = compile_catalog(load_sources(paths().data))
    ids = {p["part_id"] for p in catalog["parts"] if p.get("real")}
    jobs = [j for j in build_jobs(catalog, out, ids) if j["op"] == "realpart"]
    results = run_op("batch", {"jobs": jobs})["results"]
    return catalog, out, {j["args"]["part"]["part_id"]: (j, r) for j, r in zip(jobs, results)}


def test_every_committed_record_rebuilds_to_its_declared_envelope(built):
    catalog, _, results = built
    parts = {p["part_id"]: p for p in catalog["parts"]}
    assert len(results) >= 3
    for part_id, (_, result) in results.items():
        assert result["ok"], result.get("error")
        res = result["result"]
        assert real_part_problems(parts[part_id], res) == []
        assert 0 < res["triangles"] <= parts[part_id]["max_triangles"]
        assert res["bones"] == [f"b{i}" for i in range(len(res["bones"]))]


def test_rebuilt_parts_are_straight_rooted_and_textured(built):
    catalog, _, results = built
    parts = {p["part_id"]: p for p in catalog["parts"]}
    for part_id, (job, result) in results.items():
        res = result["result"]
        lower, upper = res["part_space_bounds_m"]
        length = parts[part_id]["length_m"]
        assert lower[2] > -0.1 * length and upper[2] < 1.1 * length, part_id
        assert res["fit"]["strain_max"] < 3.0, part_id
        assert res["texture"]["size"] == [1024, 1024]
        from pathlib import Path
        assert Path(job["args"]["out_albedo_png"]).stat().st_size > 10_000


def test_real_leg_deforms_through_hexapod_clips_no_worse_than_its_placeholder(built):
    from critter_crafter.library.commands import _gait
    from critter_crafter.parts.commands import _review_recipe, judge

    catalog, out, results = built
    part_id = "meshy_insect_leg_a_v1"
    if part_id not in results:
        pytest.skip("insect leg record not present")
    lib = paths().library_out / f"{catalog['library_id']}-v{catalog['version']}"
    built_catalog = lib / "catalog.json"
    if not built_catalog.is_file():
        pytest.skip("library not built; run `critter library build`")
    library = json.loads(built_catalog.read_text(encoding="utf-8"))
    part = next(p for p in library["parts"] if p["part_id"] == part_id)
    skeleton = next(s for s in library["skeletons"] if s["family"] == "hexapod")
    index = [s["skeleton_id"] for s in library["skeletons"]].index(skeleton["skeleton_id"])
    base, mixed, replaced = _review_recipe(library, skeleton, index, part)
    parts = {p["part_id"]: p for p in library["parts"]}
    common = {"skeleton": skeleton, "library_dir": str(lib), "gait_profile": _gait(library, skeleton["locomotion_hint"]),
              "frame_step": 6, "clips": ["idle", "walk", "attack"], "stride": True}
    def used(recipe):
        return {f["part_id"] for f in recipe["fills"]} | {f["connector_part_id"] for f in recipe["fills"] if f["connector_part_id"]}
    res = run_op("batch", {"jobs": [
        {"op": "partqa", "args": {**common, "recipe": mixed, "measure": [part_id], "parts": {p: parts[p] for p in used(mixed)}}},
        {"op": "partqa", "args": {**common, "recipe": base, "measure": sorted(set(replaced)), "parts": {p: parts[p] for p in used(base)}}},
    ]})["results"]
    real = res[0]["result"]["parts"][part_id]
    assert "stride_ik" in real["clips"]
    refs = [res[1]["result"]["parts"][r] for r in sorted(set(replaced))]
    reference = {"strain_p99": max(r["strain_p99"] for r in refs), "strain_p01": min(r["strain_p01"] for r in refs),
                 "flipped": max(r["flipped"] for r in refs)}
    assert judge(real, reference) == [], (real, reference)
