import json
from pathlib import Path

import pytest

from critter_crafter.config import paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.recipes.generator import GenerationError, canonical, generate, length_fits
from critter_crafter.recipes.rng import SplitMix64
from critter_crafter.recipes.validate import validate_recipe
from critter_crafter.schema.validate import validate_sources

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture(scope="module")
def catalog():
    return compile_catalog(load_sources(paths().data))


def test_splitmix64_reference_vector():
    # Published SplitMix64 outputs for seed 1234567.
    r = SplitMix64(1234567)
    assert [r.next() for _ in range(3)] == [6457827717110365317, 3203168211198807973, 9817491932198370423]


def test_seed_zero_is_seed_one():
    assert SplitMix64(0).next() == SplitMix64(1).next()


def test_length_fit_is_integer_and_inclusive():
    assert length_fits(1000, 800)      # scale 0.8
    assert length_fits(800, 1000)      # scale 1.25
    assert not length_fits(1000, 799)
    assert not length_fits(799, 1000)


def test_sources_validate_clean():
    diags, cat = validate_sources(paths().data, paths().schemas)
    assert cat is not None
    assert [d for d in diags if not d.startswith("CC_NO_CANDIDATE_OPTIONAL")] == []


@pytest.mark.parametrize("pool", ["any", "biped", "quadruped", "crawler"])
def test_hundred_seeds_valid_and_varied(catalog, pool):
    forms = set()
    for seed in range(1, 101):
        r = generate(catalog, pool, seed)
        assert validate_recipe(catalog, r) == [], (pool, seed)
        forms.add(canonical(r))
    assert len(forms) >= 30


def test_generation_is_deterministic(catalog):
    assert generate(catalog, "any", 7) == generate(catalog, "any", 7)


def test_unknown_pool(catalog):
    with pytest.raises(GenerationError):
        generate(catalog, "nope", 1)


def test_validator_rejects_bad_fills(catalog):
    r = generate(catalog, "biped", 3)
    r["fills"] = [f for f in r["fills"] if f["branch_id"] != "head"]
    assert any(d.startswith("CC_REQUIRED_UNFILLED: head") for d in validate_recipe(catalog, r))
    r = generate(catalog, "biped", 3)
    r["fills"][1]["connector_part_id"] = "gunk_collar_s_v1"  # leg wants an M collar
    assert any(d.startswith("CC_CONNECTOR_MISMATCH") for d in validate_recipe(catalog, r))
    r = generate(catalog, "biped", 3)
    r["fills"][0]["part_id"] = "claw_v1"  # appendage cannot be a core
    assert any(d.startswith("CC_PART_REJECTED") for d in validate_recipe(catalog, r))


def test_snap_frames_match_first_bone(catalog):
    for s in catalog["skeletons"]:
        bones = {b["name"]: b for b in s["bones"]}
        for br in s["branches"]:
            assert bones[br["bone_names"][0]]["head_m"] == br["snap"]["position_m"]


def test_golden_matches_current_generator(catalog):
    """tests/golden is shared with the Unity C# tests; regenerate with `critter recipe golden`."""
    golden_cat = json.loads((GOLDEN / "catalog.json").read_text(encoding="utf-8"))
    assert golden_cat == json.loads(json.dumps(catalog)), "catalog changed: run `critter recipe golden`"
    rows = json.loads((GOLDEN / "recipes.json").read_text(encoding="utf-8"))["rows"]
    assert len(rows) >= 500
    for row in rows:
        assert canonical(generate(golden_cat, row["pool_id"], row["seed"])) == row["canonical"]
