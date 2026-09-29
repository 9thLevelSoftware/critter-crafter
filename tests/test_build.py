"""Per-creature gait and speed from the assembled build (model build-1)."""

from __future__ import annotations

import copy
import json

import pytest

from critter_crafter.config import paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.locomotion import stepper
from critter_crafter.locomotion.block import STROKE_FRACTION
from critter_crafter.locomotion.build import creature_locomotion, golden_rows
from critter_crafter.locomotion.qa import evaluate_locomotion
from critter_crafter.recipes.generator import generate_for_skeleton


@pytest.fixture(scope="module")
def catalog():
    cat = compile_catalog(load_sources(paths().data))
    for skeleton in cat["skeletons"]:   # fixture-only approval, as `recipe golden` does
        if skeleton["status"] == "draft":
            skeleton["status"] = "approved"
    for part in cat["parts"]:
        if part["inventory_kind"] == "production" and part["status"] == "draft":
            part["status"] = "approved"
    return cat


@pytest.fixture(scope="module")
def rows(catalog):
    return golden_rows(catalog)


def _block(catalog, skeleton_id, seed=1):
    return creature_locomotion(catalog, generate_for_skeleton(catalog, skeleton_id, seed))


def test_golden_matches_current_model(rows):
    golden = json.loads((paths().root / "tests" / "golden_v3" / "creatures.json").read_text(encoding="utf-8"))
    assert golden["model"] == "build-1"
    assert golden["rows"] == json.loads(json.dumps(rows)), "run `critter recipe golden`"


def test_every_row_keeps_the_speed_band(rows):
    for row in rows:
        assert 0 < row["v_walk"] < row["v_run"] <= row["v_max"], row["recipe_id"]
        assert row["mass_kg"] > 0 and 0.4 <= row["performance"] <= 1.8


def test_speeds_spread_across_the_game_range(rows):
    runs = [row["v_run"] for row in rows]
    assert min(runs) <= 1.1
    assert max(runs) >= 4.0


def test_built_blocks_pass_static_qa_and_stride_limit(catalog):
    for skeleton in catalog["skeletons"]:
        block = _block(catalog, skeleton["skeleton_id"])
        assert evaluate_locomotion({"locomotion": block}) == [], skeleton["skeleton_id"]
        if block["mode"] != "legs":
            continue
        for speed in (block["v_walk_mps"], block["v_run_mps"]):
            params = stepper.gait_params(block, speed)
            assert params["stride_m"] * params["duty"] <= STROKE_FRACTION * block["usable_stroke_m"] + 1e-9


def test_leg_geometry_is_untouched(catalog):
    for skeleton in catalog["skeletons"]:
        built = _block(catalog, skeleton["skeleton_id"])
        assert built["legs"] == skeleton["locomotion"]["legs"]
        assert built.get("duty_run") == skeleton["locomotion"].get("duty_run")


def test_move_speed_is_the_builds_run_speed(catalog):
    block = _block(catalog, "quadruped_stocky_plantigrade_balanced_v3")
    assert block["move_speed_mps"] == block["v_run_mps"]
    assert block["build"]["model"] == "build-1"


def test_result_is_deterministic(catalog):
    sid = "hexapod_compact_insect_balanced_v3"
    assert _block(catalog, sid) == _block(catalog, sid)


def test_thicker_body_part_adds_mass_and_never_speeds_the_creature_up(catalog):
    slide = next(s["skeleton_id"] for s in catalog["skeletons"] if s["locomotion"]["mode"] == "slide")
    recipe = generate_for_skeleton(catalog, slide, 1)
    base = creature_locomotion(catalog, recipe)
    thick = copy.copy(catalog)
    thick["parts"] = copy.deepcopy(catalog["parts"])
    for fill in recipe["fills"]:
        part = next(p for p in thick["parts"] if p["part_id"] == fill["part_id"])
        part["dimensions_m"][1] *= 2.0
    heavy = creature_locomotion(thick, recipe)
    assert heavy["build"]["mass_kg"] > base["build"]["mass_kg"]
    assert heavy["v_run_mps"] <= base["v_run_mps"]
