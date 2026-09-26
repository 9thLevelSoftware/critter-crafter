import json
import copy
from pathlib import Path

import pytest

from critter_crafter.config import paths
from critter_crafter.library.catalog import compile_catalog, load_sources, reference_part_id
from critter_crafter.recipes.generator import (
    GenerationError,
    canonical,
    generate,
    generate_for_skeleton,
    length_fits,
)
from critter_crafter.recipes.rng import SplitMix64
from critter_crafter.recipes.validate import validate_recipe
from critter_crafter.schema.validate import validate_sources

GOLDEN_LEGACY = Path(__file__).parent / "golden"
GOLDEN_V3 = Path(__file__).parent / "golden_v3"
_EXTRAS_ID_TAGS = ("_extras_v3", "_armed_v3", "_finned_v3")


def _is_extras_id(skeleton_id: str) -> bool:
    return skeleton_id.endswith(_EXTRAS_ID_TAGS)


def _without_extras_catalog(catalog: dict) -> dict:
    result = copy.deepcopy(catalog)
    result["skeletons"] = [skeleton for skeleton in result["skeletons"] if not _is_extras_id(skeleton["skeleton_id"])]
    result["parts"] = [
        part for part in result["parts"]
        if not any(tag in part["part_id"] for tag in _EXTRAS_ID_TAGS)
    ]
    return result


@pytest.fixture(scope="module")
def authored_catalog():
    return compile_catalog(load_sources(paths().data))


@pytest.fixture(scope="module")
def catalog(authored_catalog):
    """Fixture-only approvals exercise generation without approving source candidates."""
    result = copy.deepcopy(authored_catalog)
    for skeleton in result["skeletons"]:
        skeleton["status"] = "approved"
    return result


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
    assert diags == []
    assert {s["status"] for s in cat["skeletons"]} == {"draft"}


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


def test_review_generation_uses_the_selected_skeletons_reference_inventory(authored_catalog):
    for skeleton in authored_catalog["skeletons"]:
        skeleton_id = skeleton["skeleton_id"]
        recipe = generate_for_skeleton(authored_catalog, skeleton_id, 17)

        assert recipe["skeleton_id"] == skeleton_id
        assert recipe["fills"]
        assert validate_recipe(authored_catalog, recipe, allow_review=True) == []
        for fill in recipe["fills"]:
            branch_id = fill["branch_id"]
            assert fill["part_id"] == reference_part_id(skeleton_id, branch_id)
            if fill["connector_part_id"]:
                assert fill["connector_part_id"] == reference_part_id(
                    skeleton_id, branch_id, connector=True
                )

    serpent_id = "serpentine_limbless_articulated_balanced_v3"
    serpent = generate_for_skeleton(authored_catalog, serpent_id, 17)
    assert all(
        fill["connector_part_id"]
        == reference_part_id(serpent_id, fill["branch_id"], connector=True)
        for fill in serpent["fills"]
    )


def test_unknown_pool(catalog):
    with pytest.raises(GenerationError):
        generate(catalog, "nope", 1)


def test_validator_rejects_bad_fills(catalog):
    r = generate(catalog, "biped", 3)
    removed = r["fills"].pop(0)
    assert any(d.startswith(f"CC_REQUIRED_UNFILLED: {removed['branch_id']}") for d in validate_recipe(catalog, r))
    r = generate(catalog, "biped", 3)
    r["fills"][0]["binding_profile_hash"] = "0" * 64
    assert any(d.startswith("CC_BINDING_IDENTITY") for d in validate_recipe(catalog, r))
    r = generate(catalog, "biped", 3)
    branch = next(b for b in next(s for s in catalog["skeletons"] if s["skeleton_id"] == r["skeleton_id"])["branches"] if b["branch_id"] == r["fills"][0]["branch_id"])
    incompatible = next(p for p in catalog["parts"] if p["binding_profile_hash"] != branch["binding_profile_hash"])
    r["fills"][0]["part_id"] = incompatible["part_id"]
    assert any(d.startswith("CC_PART_REJECTED") for d in validate_recipe(catalog, r))


def test_snap_frames_match_first_bone(catalog):
    for s in catalog["skeletons"]:
        bones = {b["name"]: b for b in s["bones"]}
        for br in s["branches"]:
            assert bones[br["bone_names"][0]]["head_m"] == br["snap"]["position_m"]


def test_legacy_golden_remains_explicitly_v2():
    assert json.loads((GOLDEN_LEGACY / "catalog.json").read_text(encoding="utf-8"))["schema_version"] == "2.0.0"
    assert json.loads((GOLDEN_LEGACY / "recipes.json").read_text(encoding="utf-8"))["generator"] == "cc-gen-2"


def test_golden_matches_current_generator(authored_catalog):
    """golden_v3 is shared with Unity; regenerate with `critter recipe golden`."""
    golden_cat = json.loads((GOLDEN_V3 / "catalog.json").read_text(encoding="utf-8"))
    assert golden_cat == json.loads(json.dumps(_without_extras_catalog(authored_catalog))), "catalog changed: run `critter recipe golden`"
    recipes = json.loads((GOLDEN_V3 / "recipes.json").read_text(encoding="utf-8"))
    assert recipes["generator"] == "cc-gen-3"
    assert recipes["fixture_only_approval"] is True
    rows = recipes["rows"]
    assert len(rows) >= 500
    approved_golden = copy.deepcopy(golden_cat)
    for skeleton in approved_golden["skeletons"]:
        skeleton["status"] = "approved"
    for row in rows:
        assert canonical(generate(approved_golden, row["pool_id"], row["seed"])) == row["canonical"]
