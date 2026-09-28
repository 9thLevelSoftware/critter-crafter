from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from critter_crafter.library.catalog import (
    REFERENCE_GROUND_CLEARANCE_M,
    CatalogError,
    _reference_profile_radius,
    compile_catalog,
    load_sources,
)
from critter_crafter.binding.profiles import canonical_profile_hash
from critter_crafter.recipes.generator import GenerationError, connector_accepted, generate, part_accepted
from critter_crafter.recipes.validate import validate_recipe
from critter_crafter.schema.validate import validate_sources
from critter_crafter.skeletons.motion import build_motion

ROOT = Path(__file__).resolve().parents[1]


def _profile() -> dict:
    return {
        "schema_version": "3.0.0",
        "binding_profile_id": "test_chain",
        "binding_profile_version": "1.0.0",
        "template": "limb1",
        "joint_order": ["proximal"],
        "bone_fractions": [1.0],
        "girth_ratio": 0.2,
        "joints": [{
            "joint_id": "proximal",
            "parent_joint": "",
            "canonical_position_n": [0.0, 0.0, 0.0],
            "canonical_rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
            "primary_axis": [0.0, 0.0, 1.0],
            "secondary_axis": [0.0, 1.0, 0.0],
            "limits_deg": {"swing_x": [-45.0, 45.0], "swing_y": [-30.0, 30.0], "twist": [-20.0, 20.0]},
        }],
        "landmarks": [{
            "landmark_id": "tip",
            "parent_joint": "proximal",
            "position_n": [0.0, 0.0, 1.0],
            "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
        }],
    }


def _sources() -> dict:
    branch = {
        "branch_id": "limb_L",
        "template": "limb1",
        "parent_branch": None,
        "direction": [0.0, 0.0, 1.0],
        "up": [0.0, 1.0, 0.0],
        "length_m": 1.0,
        "girth_m": 0.2,
        "size_class": "M",
        "side": "L",
        "required": True,
        "accepts": {"categories": ["limb"]},
        "connector_size_class": None,
        "binding_profile_id": "test_chain",
        "binding_profile_version": "1.0.0",
        "socket": {"parent_joint": "root", "position_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
        "gait": {
            "role": "locomotor",
            "phase_rad": 0.0,
            "support_phase": 0.6,
            "bend_pole_m": [0.0, 1.0, 0.0],
            "stride_m": 0.5,
            "clearance_m": 0.1,
            "cadence_hz": 1.5,
        },
        "contacts": [{"kind": "foot", "bone_index": 0, "local_point_m": [0.0, 0.0, 1.0]}],
    }
    sources = {
        "library": {
            "schema_version": "3.0.0",
            "library_id": "test_library",
            "version": "0.2.0",
            "frame": "gltf_rh_yup_zfwd_m",
            "limits": {
                "max_triangles": 30000,
                "target_triangles": 24000,
                "max_bones": 120,
                "max_parts": 16,
                "max_influences": 4,
                "min_length_scale_pct": 80,
                "max_length_scale_pct": 125,
                "girth_tolerance_pct": 10,
            },
            "generator": {"algorithm": "cc-gen-3", "rng": "splitmix64"},
        },
        "gait": {"schema_version": "3.0.0", "gait_profiles": [{
            "hint": "biped",
            "frequency_hz": 1.2,
            "amplitude_deg": 18.0,
            "chain_lag_rad": 0.35,
            "bob_m": 0.008,
        }]},
        "templates": [{
            "schema_version": "3.0.0",
            "template_id": "limb1",
            "chain_kind": "limb",
            "nominal_length_m": 1.0,
            "bone_fractions": [1.0],
        }],
        "binding_profiles": [_profile()],
        "skeletons": [{
            "schema_version": "3.0.0",
            "skeleton_id": "test_skeleton",
            "family": "biped",
            "locomotion_hint": "biped",
            "status": "approved",
            "symmetry_pct": 100,
            "anatomy": {
                "archetype_id": "test",
                "body_plan": "biped",
                "support_branches": ["limb_L"],
                "contact_branches": ["limb_L"],
                "symmetry": {"kind": "bilateral", "pairs": []},
                "landmarks": {},
                "budgets": {"bones": 2, "parts": 1, "triangles": 100},
            },
            "neutral_pose": {
                "root_offset_m": [0.0, 0.0, 0.0],
                "rotations": [
                    {"bone_name": "root", "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
                    {"bone_name": "limb_L_b0", "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
                ],
            },
            "branches": [branch],
        }],
        "parts": [{
            "schema_version": "3.0.0",
            "part_id": "draft_limb_l_v1",
            "category": "limb",
            "template": "limb1",
            "binding_profile_id": "test_chain",
            "binding_profile_version": "1.0.0",
            "side": "L",
            "girth_m": 0.2,
            "species_tags": [],
            "roles": ["locomotor"],
            "status": "draft",
            "dimensions_m": [0.2, 0.2, 1.0],
            "length_m": 1.0,
            "budget": {"max_triangles": 100},
        }],
        "pools": [{"pool_id": "any", "families": ["biped"], "skeleton_ids": []}],
    }
    profile = sources["binding_profiles"][0]
    sources["binding_profile_registry"] = [{
        "binding_profile_id": profile["binding_profile_id"],
        "binding_profile_version": profile["binding_profile_version"],
        "binding_profile_hash": canonical_profile_hash(profile),
    }]
    sources["binding_profile_baseline"] = copy.deepcopy(sources["binding_profile_registry"])
    return sources


def _connector_source() -> dict:
    return {
        "schema_version": "3.0.0",
        "part_id": "test_connector_v1",
        "category": "connector",
        "template": "connector2",
        "binding_profile_id": "test_chain",
        "binding_profile_version": "1.0.0",
        "side": "L",
        "girth_m": 0.2,
        "species_tags": [],
        "roles": ["connector"],
        "size_class": "M",
        "status": "approved",
        "dimensions_m": [0.2, 0.2, 0.1],
        "length_m": 0.1,
        "budget": {"max_triangles": 50},
        "connector": {
            "radius_m": 0.1,
            "span_m": [-0.05, 0.05],
            "interface": {
                "interface_id": "skinned_parent_child",
                "interface_version": "1.0.0",
                "bone_groups": [{"group": "b0", "role": "parent"}, {"group": "b1", "role": "child"}],
                "max_influences": 2,
                "weights_normalized": True,
                "position_m": [0.0, 0.0, 0.0],
                "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
        },
    }


def _relock_profile(sources: dict, *, include_baseline: bool = True) -> None:
    profile = sources["binding_profiles"][0]
    digest = canonical_profile_hash(profile)
    sources["binding_profile_registry"][0]["binding_profile_hash"] = digest
    if include_baseline:
        sources["binding_profile_baseline"][0]["binding_profile_hash"] = digest


def test_compile_v3_exposes_profiles_and_reference_inventory():
    catalog = compile_catalog(_sources())

    assert catalog["schema_version"] == "3.0.0"
    assert catalog["generator"]["algorithm"] == "cc-gen-3"
    assert len(catalog["binding_profiles"][0]["binding_profile_hash"]) == 64
    branch = catalog["skeletons"][0]["branches"][0]
    assert branch["joint_order"] == ["proximal"]
    assert branch["bone_fractions"] == [1.0]
    assert branch["gait"]["support_phase"] == 0.6
    assert branch["contacts"][0]["kind"] == "foot"
    references = [p for p in catalog["parts"] if p["inventory_kind"] == "reference"]
    assert len(references) == 1
    assert part_accepted(references[0], branch)


def test_low_sliding_reference_volume_keeps_nominal_girth_but_caps_physical_thickness():
    sources = _sources()
    source_skeleton = sources["skeletons"][0]
    source_branch = source_skeleton["branches"][0]
    source_branch["contacts"] = [
        {"kind": "sliding", "bone_index": 0, "local_point_m": [0.0, 0.0, 0.5]}
    ]
    source_branch["socket"]["position_m"] = [0.0, 0.1, 0.0]
    source_branch["connector_size_class"] = "M"
    catalog = compile_catalog(sources)
    branch = catalog["skeletons"][0]["branches"][0]
    parts = {part["part_id"]: part for part in catalog["parts"]}
    body = parts["reference_test_skeleton_limb_l_v1"]
    connector = parts["reference_connector_test_skeleton_limb_l_v1"]

    assert body["dimensions_m"][0] == branch["girth_m"] == body["girth_m"]
    assert 0 < body["dimensions_m"][1] < body["dimensions_m"][0]
    assert body["dimensions_m"][2] == branch["length_m"]
    assert connector["dimensions_m"][0] == branch["girth_m"] == connector["girth_m"]
    assert 0 < connector["dimensions_m"][1] < connector["dimensions_m"][0]
    assert connector["connector_radius_m"] == pytest.approx(branch["girth_m"] / 2, abs=1e-6)
    assert connector["dimensions_m"][2] == pytest.approx(
        connector["connector_span_m"][1] - connector["connector_span_m"][0]
    )
    body_low = min(
        0.1 - _reference_profile_radius(body["category"], index / 16) * body["dimensions_m"][1] / 2
        for index in range(17)
    )
    connector_low = min(
        0.1 - _reference_profile_radius("connector", index / 10) * connector["dimensions_m"][1] / 2
        for index in range(11)
    )
    assert body_low >= REFERENCE_GROUND_CLEARANCE_M
    assert connector_low >= REFERENCE_GROUND_CLEARANCE_M


def test_non_body_contact_reference_volume_retains_round_authored_section():
    catalog = compile_catalog(_sources())
    part = next(item for item in catalog["parts"] if item["inventory_kind"] == "reference")
    assert part["dimensions_m"][0] == part["dimensions_m"][1] == part["girth_m"]


def test_low_noncontact_body_chain_uses_the_same_ground_envelope():
    sources = _sources()
    skeleton = sources["skeletons"][0]
    branch = skeleton["branches"][0]
    branch["contacts"] = []
    branch["socket"]["position_m"] = [0.0, 0.1, 0.0]
    catalog = compile_catalog(sources)
    part = next(item for item in catalog["parts"] if item["inventory_kind"] == "reference")

    assert 0 < part["dimensions_m"][1] < part["dimensions_m"][0]


def test_production_part_cannot_collide_with_a_generated_reference_identity():
    sources = _sources()
    sources["parts"][0]["part_id"] = "reference_test_skeleton_limb_l_v1"

    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == "CC_DUP_PART"


def test_generated_reference_identities_cannot_collapse_case_distinct_branch_ids():
    sources = _sources()
    skeleton = sources["skeletons"][0]
    branch = copy.deepcopy(skeleton["branches"][0])
    branch["branch_id"] = "limb_l"
    skeleton["branches"].append(branch)
    skeleton["neutral_pose"]["rotations"].append(
        {"bone_name": "limb_l_b0", "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]}
    )

    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == "CC_DUP_PART"


@pytest.mark.parametrize("centre_y", [REFERENCE_GROUND_CLEARANCE_M, -0.1])
def test_low_reference_volume_rejects_a_centreline_at_or_below_ground_clearance(centre_y: float):
    sources = _sources()
    branch = sources["skeletons"][0]["branches"][0]
    branch["contacts"] = [
        {"kind": "sliding", "bone_index": 0, "local_point_m": [0.0, 0.0, 0.5]}
    ]
    branch["socket"]["position_m"] = [0.0, centre_y, 0.0]

    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == "CC_REFERENCE_ENVELOPE"


def test_part_acceptance_requires_binding_side_uniform_scale_and_scaled_girth():
    catalog = compile_catalog(_sources())
    branch = catalog["skeletons"][0]["branches"][0]
    part = next(p for p in catalog["parts"] if p["inventory_kind"] == "reference")
    assert part_accepted(part, branch)

    for field in ("binding_profile_id", "binding_profile_version", "binding_profile_hash"):
        bad = copy.deepcopy(part)
        bad[field] = "wrong"
        assert not part_accepted(bad, branch)

    bad = copy.deepcopy(part)
    bad["side"] = "R"
    assert not part_accepted(bad, branch)
    symmetric = copy.deepcopy(part)
    symmetric["side"] = "symmetric"
    assert part_accepted(symmetric, branch)

    too_short = copy.deepcopy(part)
    too_short["length_mm"] = 799
    assert not part_accepted(too_short, branch)
    bad_girth = copy.deepcopy(part)
    bad_girth["girth_mm"] = 150
    assert not part_accepted(bad_girth, branch)


def test_v3_generator_uses_reference_parts_and_emits_binding_identity():
    catalog = compile_catalog(_sources())
    recipe = generate(catalog, "any", 7)

    assert recipe["schema_version"] == "3.0.0"
    assert recipe["generator"] == "cc-gen-3"
    fill = recipe["fills"][0]
    assert fill["binding_profile_id"] == "test_chain"
    assert fill["binding_profile_version"] == "1.0.0"
    assert len(fill["binding_profile_hash"]) == 64
    assert fill["length_scale"] == 1.0
    assert fill["girth_scale"] == 1.0
    assert validate_recipe(catalog, recipe) == []


def test_generation_rejects_v2_catalog_before_record_access():
    with pytest.raises(GenerationError) as exc:
        generate({"schema_version": "2.0.0"}, "any", 1)
    assert exc.value.code == "CC_GEN_SCHEMA_VERSION"


def test_compilation_and_generation_reject_wrong_library_release_early():
    sources = _sources()
    sources["library"]["version"] = "0.1.0"
    with pytest.raises(CatalogError) as compile_error:
        compile_catalog(sources)
    assert compile_error.value.code == "CC_LIBRARY_VERSION"

    unsupported = {
        "schema_version": "3.0.0",
        "version": "0.1.0",
        "generator": {"algorithm": "cc-gen-3"},
    }
    with pytest.raises(GenerationError) as generation_error:
        generate(unsupported, "any", 1)
    assert generation_error.value.code == "CC_GEN_LIBRARY_VERSION"
    assert validate_recipe(unsupported, {"schema_version": "3.0.0"}) == [
        "CC_LIBRARY_VERSION: expected 0.2.0, got 0.1.0"
    ]


def test_recipe_validation_rejects_versions_before_record_access():
    catalog = compile_catalog(_sources())
    assert validate_recipe(catalog, {"schema_version": "2.0.0"}) == [
        "CC_RECIPE_SCHEMA_VERSION: expected 3.0.0, got 2.0.0"
    ]
    assert validate_recipe({"schema_version": "2.0.0"}, {}) == [
        "CC_LIBRARY_SCHEMA_VERSION: expected 3.0.0, got 2.0.0"
    ]


def test_load_sources_filters_frozen_v2_documents(tmp_path: Path):
    (tmp_path / "branch_templates").mkdir()
    (tmp_path / "binding_profiles").mkdir()
    (tmp_path / "skeletons").mkdir()
    (tmp_path / "parts").mkdir()
    (tmp_path / "pools").mkdir()
    (tmp_path / "library.json").write_text(json.dumps(_sources()["library"]), encoding="utf-8")
    (tmp_path / "gait_profiles.json").write_text(json.dumps(_sources()["gait"]), encoding="utf-8")
    preferred_gait = {"schema_version": "3.0.0", "gait_profiles": [{"hint": "preferred"}]}
    (tmp_path / "gait_profiles.v3.json").write_text(json.dumps(preferred_gait), encoding="utf-8")
    for version in ("2.0.0", "3.0.0"):
        suffix = version[0]
        template = dict(_sources()["templates"][0], schema_version=version, template_id=f"limb{suffix}")
        skeleton = dict(_sources()["skeletons"][0], schema_version=version, skeleton_id=f"skeleton_v{suffix}")
        part = dict(_sources()["parts"][0], schema_version=version, part_id=f"part_v{suffix}")
        (tmp_path / "branch_templates" / f"limb{suffix}.json").write_text(json.dumps(template), encoding="utf-8")
        (tmp_path / "skeletons" / f"skeleton_v{suffix}.skeleton.json").write_text(json.dumps(skeleton), encoding="utf-8")
        (tmp_path / "parts" / f"part_v{suffix}.part.json").write_text(json.dumps(part), encoding="utf-8")
    (tmp_path / "binding_profiles" / "test_chain.binding.json").write_text(json.dumps(_profile()), encoding="utf-8")
    pools = {"schema_version": "3.0.0", "pools": _sources()["pools"]}
    (tmp_path / "pools" / "pools.json").write_text(json.dumps(pools), encoding="utf-8")

    loaded = load_sources(tmp_path)

    assert [x["schema_version"] for x in loaded["templates"]] == ["3.0.0"]
    assert [x["schema_version"] for x in loaded["skeletons"]] == ["3.0.0"]
    assert [x["schema_version"] for x in loaded["parts"]] == ["3.0.0"]
    assert loaded["gait"]["gait_profiles"] == [{"hint": "preferred"}]


def _write_source_tree(root: Path, sources: dict) -> None:
    for name in ("branch_templates", "binding_profiles", "skeletons", "parts", "pools"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "library.json").write_text(json.dumps(sources["library"]), encoding="utf-8")
    (root / "gait_profiles.json").write_text(json.dumps(sources["gait"]), encoding="utf-8")
    (root / "binding_profiles" / "registry.json").write_text(
        json.dumps({"schema_version": "3.0.0", "profiles": sources["binding_profile_registry"]}),
        encoding="utf-8",
    )
    (root / "binding_profiles" / "released_registry.json").write_text(
        json.dumps({"schema_version": "3.0.0", "profiles": sources["binding_profile_baseline"]}),
        encoding="utf-8",
    )
    (root / "pools" / "pools.json").write_text(
        json.dumps({"schema_version": "3.0.0", "pools": sources["pools"]}), encoding="utf-8"
    )
    for template in sources["templates"]:
        (root / "branch_templates" / f"{template['template_id']}.json").write_text(
            json.dumps(template), encoding="utf-8"
        )
    for profile in sources["binding_profiles"]:
        (root / "binding_profiles" / f"{profile['binding_profile_id']}.binding.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )
    for skeleton in sources["skeletons"]:
        (root / "skeletons" / f"{skeleton['skeleton_id']}.skeleton.json").write_text(
            json.dumps(skeleton), encoding="utf-8"
        )
    for part in sources["parts"]:
        (root / "parts" / f"{part['part_id']}.part.json").write_text(json.dumps(part), encoding="utf-8")


def test_source_validation_accepts_complete_v3_contract(tmp_path: Path):
    _write_source_tree(tmp_path, _sources())

    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")

    assert diagnostics == []
    assert catalog is not None


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda sources: sources["binding_profiles"][0].update(bone_fractions=[0.9]), "CC_BINDING_FRACTIONS"),
        (
            lambda sources: sources["binding_profiles"][0]["joints"][0].update(
                canonical_rotation_xyzw=[0.0, 0.0, 0.0, 2.0]
            ),
            "CC_BINDING_QUATERNION",
        ),
        (
            lambda sources: sources["skeletons"][0]["branches"][0]["socket"].update(
                rotation_xyzw=[0.0, 0.0, 0.0, 2.0]
            ),
            "CC_SOCKET_QUATERNION",
        ),
        (
            lambda sources: sources["skeletons"][0]["neutral_pose"]["rotations"].reverse(),
            "CC_NEUTRAL_POSE_ORDER",
        ),
    ],
)
def test_source_validation_rejects_noncanonical_binding_contract(tmp_path: Path, mutation, code: str):
    sources = _sources()
    mutation(sources)
    _write_source_tree(tmp_path, sources)

    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")

    assert catalog is None
    assert any(item.startswith(code) for item in diagnostics), diagnostics


def test_profile_registry_rejects_mutation_without_version_bump(tmp_path: Path):
    sources = _sources()
    sources["binding_profiles"][0]["girth_ratio"] = 0.25
    _write_source_tree(tmp_path, sources)

    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")

    assert catalog is None
    assert diagnostics == ["CC_BINDING_PROFILE_IMMUTABLE: test_chain@1.0.0"]


def test_source_validation_requires_each_skeleton_gait_hint(tmp_path: Path):
    sources = _sources()
    sources["gait"]["gait_profiles"][0]["hint"] = "crawl"
    _write_source_tree(tmp_path, sources)

    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")

    assert catalog is None
    assert diagnostics == ["CC_UNKNOWN_GAIT_PROFILE: test_skeleton=biped"]


def test_source_validation_rejects_incomplete_gait_profile(tmp_path: Path):
    sources = _sources()
    del sources["gait"]["gait_profiles"][0]["frequency_hz"]
    _write_source_tree(tmp_path, sources)

    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")

    assert catalog is None
    assert any(item.startswith("CC_SCHEMA: gait_profiles") for item in diagnostics)


def test_skinned_connector_requires_exact_chain_and_parent_child_interface():
    sources = _sources()
    sources["skeletons"][0]["branches"][0]["connector_size_class"] = "M"
    sources["parts"].append(_connector_source())

    catalog = compile_catalog(sources)
    recipe = generate(catalog, "any", 7)

    connector = next(p for p in catalog["parts"] if p["part_id"] == "test_connector_v1")
    branch = catalog["skeletons"][0]["branches"][0]
    assert connector["binding_profile_id"] == branch["binding_profile_id"]
    assert connector["binding_profile_hash"] == branch["binding_profile_hash"]
    assert connector["connector_interface"]["bone_groups"] == [
        {"group": "b0", "role": "parent"}, {"group": "b1", "role": "child"}
    ]
    assert branch["connector_interface"]["parent_bone"] == "root"
    assert branch["connector_interface"]["child_bone"] == "limb_L_b0"
    assert connector_accepted(connector, branch)
    selected = next(p for p in catalog["parts"] if p["part_id"] == recipe["fills"][0]["connector_part_id"])
    assert connector_accepted(selected, branch)
    assert validate_recipe(catalog, recipe) == []


def test_skinned_connector_source_passes_v3_schema_and_semantic_validation(tmp_path: Path):
    sources = _sources()
    sources["skeletons"][0]["branches"][0]["connector_size_class"] = "M"
    sources["parts"].append(_connector_source())
    _write_source_tree(tmp_path, sources)
    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")
    assert diagnostics == []
    assert catalog is not None


def test_optional_connector_never_exceeds_hard_part_budget():
    sources = _sources()
    sources["library"]["limits"]["max_parts"] = 1
    sources["skeletons"][0]["branches"][0]["connector_size_class"] = "M"
    sources["parts"].append(_connector_source())
    catalog = compile_catalog(sources)

    recipe = generate(catalog, "any", 7)

    assert recipe["fills"][0]["connector_part_id"] == ""
    assert validate_recipe(catalog, recipe) == []


def test_compiled_library_schema_rejects_unknown_contract_fields():
    catalog = compile_catalog(_sources())
    schema = json.loads((ROOT / "schemas" / "library.v3.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(catalog)) == []

    catalog["unexpected"] = True
    assert any(error.validator == "additionalProperties" for error in validator.iter_errors(catalog))


def test_compiled_library_schema_rejects_malformed_nested_identity():
    catalog = compile_catalog(_sources())
    del catalog["parts"][0]["binding_profile_hash"]
    schema = json.loads((ROOT / "schemas" / "library.v3.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(catalog))
    assert any("binding_profile_hash" in error.message for error in errors)


def test_compiled_library_schema_rejects_malformed_consumed_nested_records():
    catalog = compile_catalog(_sources())
    catalog["skeletons"][0]["bones"] = [1]
    catalog["skeletons"][0]["branches"][0]["socket"] = "bad"
    catalog["binding_profiles"][0]["joints"] = [None]
    catalog["skeletons"][0]["asset"]["motion"] = 42
    schema = json.loads((ROOT / "schemas" / "library.v3.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(catalog))
    assert len(errors) >= 4


def _compiled_clips_with_contact_schedule(skeleton: dict, gait: dict) -> list[dict]:
    planned_clips = build_motion(skeleton, gait)["clips"]
    keys = (
        "name", "loop", "frames", "fps", "duration_s", "cadence_hz", "speed_mps",
        "stride_m", "playback", "support_policy", "contact_schedule",
    )
    return [
        {key: copy.deepcopy(planned[key]) for key in keys} | {
            "nominal_speed_mps": planned["speed_mps"],
            "ground_offset_m": 0.0,
        }
        for planned in planned_clips
    ]


def _compiled_clip_with_contact_schedule(catalog: dict) -> dict:
    skeleton = catalog["skeletons"][0]
    gait = next(item for item in catalog["gait_profiles"] if item["hint"] == skeleton["locomotion_hint"])
    return _compiled_clips_with_contact_schedule(skeleton, gait)[0]


def test_compiled_clip_schema_accepts_closed_contact_schedule_from_motion():
    catalog = compile_catalog(load_sources(ROOT / "data"))
    clip = _compiled_clip_with_contact_schedule(catalog)
    catalog["skeletons"][0]["asset"]["clips"] = [clip]
    schema = json.loads((ROOT / "schemas" / "library.v3.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(catalog))
    assert errors == []
    entry = clip["contact_schedule"][0]
    branch = next(item for item in catalog["skeletons"][0]["branches"] if item["branch_id"] == entry["branch_id"])
    contact = branch["contacts"][entry["contact_index"]]
    assert entry["contact_id"] == f"{branch['branch_id']}:{entry['contact_index']}"
    assert entry["kind"] == contact["kind"]
    assert clip["playback"]["rate_range"] == [0.5, 1.5]


def test_all_curated_compiled_clips_and_contact_schedules_match_library_schema():
    catalog = compile_catalog(load_sources(ROOT / "data"))
    gaits = {item["hint"]: item for item in catalog["gait_profiles"]}
    for skeleton in catalog["skeletons"]:
        skeleton["asset"]["clips"] = _compiled_clips_with_contact_schedule(
            skeleton, gaits[skeleton["locomotion_hint"]]
        )
    schema = json.loads((ROOT / "schemas" / "library.v3.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(catalog))
    assert errors == []
    assert sum(len(skeleton["asset"]["clips"]) for skeleton in catalog["skeletons"]) == 336


@pytest.mark.parametrize(
    "mutation",
    [
        lambda clip: clip.pop("contact_schedule"),
        lambda clip: clip["contact_schedule"][0].update(unexpected=True),
        lambda clip: clip["contact_schedule"][0].update(contact_id="invalid"),
        lambda clip: clip["contact_schedule"][0].update(contact_index=-1),
        lambda clip: clip["contact_schedule"][0].update(kind="hoof"),
        lambda clip: clip["contact_schedule"][0].update(phase_offset=1.01),
        lambda clip: clip["contact_schedule"][0].update(stance_fraction=None),
        lambda clip: clip["contact_schedule"][0].update(support=1),
        lambda clip: clip["contact_schedule"].append(copy.deepcopy(clip["contact_schedule"][0])),
    ],
)
def test_compiled_clip_schema_rejects_malformed_contact_schedule(mutation):
    catalog = compile_catalog(load_sources(ROOT / "data"))
    clip = _compiled_clip_with_contact_schedule(catalog)
    mutation(clip)
    catalog["skeletons"][0]["asset"]["clips"] = [clip]
    schema = json.loads((ROOT / "schemas" / "library.v3.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(catalog))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda playback: playback.pop("rate_range"),
        lambda playback: playback.update(rate_range=[0.5]),
        lambda playback: playback.update(rate_range=[0.0, 1.5]),
        lambda playback: playback.update(rate_range=[0.5, 2.0]),
        lambda playback: playback.update(rate_range=None),
    ],
)
def test_compiled_clip_schema_requires_release_playback_rate_range(mutation):
    catalog = compile_catalog(load_sources(ROOT / "data"))
    clip = _compiled_clip_with_contact_schedule(catalog)
    mutation(clip["playback"])
    catalog["skeletons"][0]["asset"]["clips"] = [clip]
    schema = json.loads((ROOT / "schemas" / "library.v3.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(catalog))


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda p: p["joints"][0].update(canonical_position_n=[0.0, 0.0, 0.25]), "CC_BINDING_CANONICAL_POSITION"),
        (lambda p: p["joints"][0].update(primary_axis=[0.0, 0.0, -1.0]), "CC_BINDING_AXES"),
        (lambda p: p["joints"][0]["limits_deg"].update(swing_x=[45.0, -45.0]), "CC_BINDING_LIMITS"),
        (lambda p: p.update(bone_fractions=[float("nan")]), "CC_NONFINITE"),
    ],
)
def test_profile_semantics_reject_noncanonical_or_nonfinite_data(tmp_path: Path, mutation, code: str):
    sources = _sources()
    mutation(sources["binding_profiles"][0])
    _relock_profile(sources)
    _write_source_tree(tmp_path, sources)

    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")

    assert catalog is None
    assert any(item.startswith(code) for item in diagnostics), diagnostics


def test_profile_template_must_match_branch_and_template_topology():
    sources = _sources()
    sources["binding_profiles"][0]["template"] = "different"
    _relock_profile(sources)
    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == "CC_BINDING_TEMPLATE"


def test_profile_branch_and_part_cannot_coordinate_on_missing_template():
    sources = _sources()
    sources["binding_profiles"][0]["template"] = "missing"
    sources["skeletons"][0]["branches"][0]["template"] = "missing"
    sources["parts"][0]["template"] = "missing"
    _relock_profile(sources)
    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == "CC_BINDING_TEMPLATE"


def test_released_profile_baseline_rejects_registry_and_profile_mutation():
    sources = _sources()
    sources["binding_profiles"][0]["girth_ratio"] = 0.25
    _relock_profile(sources, include_baseline=False)
    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == "CC_BINDING_PROFILE_IMMUTABLE"


def test_duplicate_registry_identity_is_rejected_before_index_collapse():
    sources = _sources()
    sources["binding_profile_registry"].append(copy.deepcopy(sources["binding_profile_registry"][0]))
    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == "CC_DUP_BINDING_PROFILE_REGISTRY"


def test_duplicate_json_object_key_is_rejected_before_value_collapse(tmp_path: Path):
    sources = _sources()
    _write_source_tree(tmp_path, sources)
    (tmp_path / "library.json").write_text(
        '{"schema_version":"3.0.0","library_id":"first","library_id":"second"}',
        encoding="utf-8",
    )
    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")
    assert catalog is None
    assert diagnostics and diagnostics[0].startswith("CC_DUP_JSON_KEY")


def test_released_profile_cannot_be_removed_from_profile_and_current_registry():
    sources = _sources()
    sources["binding_profiles"] = []
    sources["binding_profile_registry"] = []
    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == "CC_BINDING_PROFILE_BASELINE"


def test_socket_is_authoritative_and_redundant_global_geometry_must_agree():
    sources = _sources()
    sources["skeletons"][0]["branches"][0]["origin_m"] = [0.0, 0.0, 0.0]
    sources["skeletons"][0]["branches"][0]["socket"]["position_m"] = [9.0, 8.0, 7.0]
    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == "CC_SOCKET_GEOMETRY"


def test_neutral_pose_must_stay_within_profile_limits(tmp_path: Path):
    sources = _sources()
    half = 2 ** -0.5
    sources["skeletons"][0]["neutral_pose"]["rotations"][1]["rotation_xyzw"] = [half, 0.0, 0.0, half]
    _write_source_tree(tmp_path, sources)

    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")

    assert catalog is None
    assert any(item.startswith("CC_NEUTRAL_LIMIT") for item in diagnostics), diagnostics


def test_required_branch_cannot_descend_from_optional_branch():
    sources = _sources()
    parent = sources["skeletons"][0]["branches"][0]
    parent["required"] = False
    child = copy.deepcopy(parent)
    child.update(branch_id="child", parent_branch="limb_L", required=True, mirror_of="")
    child["socket"] = {"parent_joint": "proximal", "position_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]}
    sources["skeletons"][0]["branches"].append(child)
    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == "CC_REQUIRED_ANCESTOR_OPTIONAL"


def test_recipe_rejects_unusable_inventory_malformed_shape_and_unknown_pool():
    catalog = compile_catalog(_sources())
    recipe = generate(catalog, "any", 7)
    selected = next(part for part in catalog["parts"] if part["part_id"] == recipe["fills"][0]["part_id"])
    selected["inventory_kind"] = "production"
    selected["status"] = "rejected"
    assert any(item.startswith("CC_PART_NOT_APPROVED") for item in validate_recipe(catalog, recipe))

    malformed = copy.deepcopy(recipe)
    malformed["fills"] = [{}]
    assert all(item.startswith("CC_RECIPE_SCHEMA") for item in validate_recipe(catalog, malformed))
    malformed = copy.deepcopy(recipe)
    malformed["seed"] = "7"
    malformed["unexpected"] = True
    diagnostics = validate_recipe(catalog, malformed)
    assert diagnostics and all(item.startswith("CC_RECIPE_SCHEMA") for item in diagnostics)

    unknown_pool = copy.deepcopy(recipe)
    unknown_pool["pool_id"] = "does_not_exist"
    assert "CC_UNKNOWN_POOL: does_not_exist" in validate_recipe(catalog, unknown_pool)


@pytest.mark.parametrize("reference_status", ["draft", "rejected", "approved"])
def test_generator_and_validator_reject_reference_inventory_without_reference_status(
    reference_status: str,
):
    catalog = compile_catalog(_sources())
    reference = next(
        part for part in catalog["parts"] if part["inventory_kind"] == "reference"
    )
    valid_recipe = generate(catalog, "any", 7)
    reference["status"] = reference_status

    with pytest.raises(GenerationError) as exc:
        generate(catalog, "any", 7)
    assert exc.value.code == "CC_GEN_NO_CANDIDATE"
    assert any(
        item.startswith("CC_PART_NOT_APPROVED")
        for item in validate_recipe(catalog, valid_recipe)
    )


def test_generator_and_validator_accept_exact_reference_and_production_status_pairs():
    reference_catalog = compile_catalog(_sources())
    reference_recipe = generate(reference_catalog, "any", 7)
    selected_reference = next(
        part
        for part in reference_catalog["parts"]
        if part["part_id"] == reference_recipe["fills"][0]["part_id"]
    )
    assert (selected_reference["inventory_kind"], selected_reference["status"]) == (
        "reference",
        "reference",
    )
    assert validate_recipe(reference_catalog, reference_recipe) == []

    production_catalog = compile_catalog(_sources())
    production = next(
        part for part in production_catalog["parts"] if part["inventory_kind"] == "production"
    )
    production["status"] = "approved"
    for part in production_catalog["parts"]:
        if part["inventory_kind"] == "reference":
            part["status"] = "rejected"
    production_recipe = generate(production_catalog, "any", 7)
    assert production_recipe["fills"][0]["part_id"] == production["part_id"]
    assert validate_recipe(production_catalog, production_recipe) == []


def test_review_pool_string_cannot_spoof_explicit_review_context():
    catalog = compile_catalog(_sources())
    catalog["skeletons"][0]["status"] = "draft"
    recipe = generate(catalog | {"skeletons": [catalog["skeletons"][0] | {"status": "approved"}]}, "any", 7)
    recipe["pool_id"] = "review_test_skeleton"
    diagnostics = validate_recipe(catalog, recipe)
    assert "CC_SKELETON_NOT_APPROVED: test_skeleton" in diagnostics
    assert "CC_UNKNOWN_POOL: review_test_skeleton" in diagnostics
    assert validate_recipe(catalog, recipe, allow_review=True) == []


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [("frame", "wrong", "frame"), ("library_id", "INVALID ID", "library_id")],
)
def test_source_library_envelope_is_schema_validated(tmp_path: Path, field: str, value, fragment: str):
    sources = _sources()
    sources["library"][field] = value
    _write_source_tree(tmp_path, sources)
    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")
    assert catalog is None
    assert any(item.startswith("CC_SCHEMA: library.json") and fragment in item for item in diagnostics)


def test_source_library_rejects_wrong_rng(tmp_path: Path):
    sources = _sources()
    sources["library"]["generator"]["rng"] = "wrong"
    _write_source_tree(tmp_path, sources)
    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")
    assert catalog is None
    assert any(item.startswith("CC_SCHEMA: library.json") and "rng" in item for item in diagnostics)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda c: c["connector"]["interface"].update(bone_groups=[{"group": "b0", "role": "parent"}]), "CC_CONNECTOR_TOPOLOGY"),
        (lambda c: c["connector"]["interface"].update(weights_normalized=False), "CC_CONNECTOR_WEIGHTS"),
        (lambda c: c["connector"]["interface"].update(rotation_xyzw=[0.0, 0.0, 1.0, 0.0]), "CC_CONNECTOR_TRANSFORM"),
    ],
)
def test_connector_compile_rejects_invalid_topology_weights_and_transform(mutate, code: str):
    sources = _sources()
    sources["skeletons"][0]["branches"][0]["connector_size_class"] = "M"
    connector = _connector_source()
    mutate(connector)
    sources["parts"].append(connector)
    with pytest.raises(CatalogError) as exc:
        compile_catalog(sources)
    assert exc.value.code == code


def test_connector_matching_rejects_profile_side_girth_and_transform_mismatch():
    sources = _sources()
    sources["skeletons"][0]["branches"][0]["connector_size_class"] = "M"
    sources["parts"].append(_connector_source())
    catalog = compile_catalog(sources)
    branch = catalog["skeletons"][0]["branches"][0]
    connector = next(part for part in catalog["parts"] if part["part_id"] == "test_connector_v1")
    for mutation in (
        lambda part: part.update(binding_profile_hash="0" * 64),
        lambda part: part.update(side="R"),
        lambda part: part.update(girth_mm=199),
        lambda part: part["connector_interface"].update(position_m=[0.0, 0.0, 0.01]),
    ):
        bad = copy.deepcopy(connector)
        mutation(bad)
        assert not connector_accepted(bad, branch)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda s: s["skeletons"][0]["anatomy"].update(contact_branches=[]), "CC_ANATOMY_CONTACT_SET"),
        (lambda s: s["skeletons"][0]["anatomy"].update(support_branches=[]), "CC_ANATOMY_SUPPORT_COUNT"),
        (
            lambda s: s["skeletons"][0]["anatomy"]["symmetry"].update(
                pairs=[["limb_L", "missing"]]
            ),
            "CC_ANATOMY_SYMMETRY_PAIR",
        ),
    ],
)
def test_anatomy_contact_support_and_symmetry_metadata_are_consistent(
    tmp_path: Path, mutation, code: str
):
    sources = _sources()
    mutation(sources)
    _write_source_tree(tmp_path, sources)
    diagnostics, catalog = validate_sources(tmp_path, ROOT / "schemas")
    assert catalog is None
    assert any(item.startswith(code) for item in diagnostics), diagnostics
