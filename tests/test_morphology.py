"""Topology authoring, functional metrics and deterministic proposal boundaries."""
import math
from dataclasses import replace

import pytest

from critter_crafter.skeletons.morphology import MorphologyError, MorphologyParams, build_morphology, morphology_identity
from critter_crafter.skeletons.search import SearchError, SearchSettings, descriptor, functional_score, initial_samples


def test_changed_bilateral_count_authors_front_striker_and_remainder_supports():
    source = build_morphology("crawler_bilateral_eight_legged", 1, MorphologyParams(topology_count=3, appendages=False))
    supports = source["anatomy"]["support_branches"]
    assert supports == ["leg0_L", "leg0_R", "leg1_L", "leg1_R", "leg2_L", "leg2_R"]
    effector = next(branch for branch in source["branches"] if branch.get("effector_slot"))
    assert effector["branch_id"] == "leg2_L"
    assert effector["effector_slot"] == "forward_outer_left"
    assert source["anatomy"]["action_support"] == {"released_contact_ids": ["leg2_L:0"], "minimum_preserved": 5}
    left = [branch for branch in source["branches"] if branch["branch_id"] in {"leg0_L", "leg1_L", "leg2_L"}]
    assert left[1]["origin_m"][2] == pytest.approx((left[0]["origin_m"][2] + left[2]["origin_m"][2]) / 2, abs=1e-4)
    assert [branch["gait"]["phase_rad"] for branch in left] == [0., round(math.pi, 6), 0.]


@pytest.mark.parametrize("count", [3, 4, 5, 6, 7])
def test_generated_radial_counts_keep_off_axis_effector_and_topology_phases(count):
    source = build_morphology("radial_raised_articulated_walker", 1, MorphologyParams(topology_count=count, appendages=False))
    arms = [branch for branch in source["branches"] if branch["gait"]["role"] == "locomotor"]
    assert len(arms) == count
    assert all(abs(branch["direction"][0]) > 1e-6 for branch in arms)
    assert arms[0]["effector_slot"] == "radial_sector_0"
    assert source["anatomy"]["action_support"]["minimum_preserved"] == count - 1
    if count % 2:
        assert [branch["gait"]["phase_rad"] for branch in arms] == [round(index * math.tau / count, 6) for index in range(count)]
        assert all(branch["gait"]["support_phase"] >= (count - 1) / count + .01 - 1e-6 for branch in arms)
    else:
        assert [branch["gait"]["run_phase_rad"] for branch in arms] == [round((index % 2) * math.pi, 6) for index in range(count)]


def test_canonical_parameters_quantize_before_geometry_and_identity():
    params = MorphologyParams(topology_count=3, appendages=False, height=1.00001)
    expected = MorphologyParams(topology_count=3, appendages=False)
    assert build_morphology("crawler_bilateral_eight_legged", 7, params) == build_morphology("crawler_bilateral_eight_legged", 7, expected)
    first, _ = morphology_identity("crawler_bilateral_eight_legged", 7, expected)
    second, _ = morphology_identity("crawler_bilateral_eight_legged", 7, replace(expected, height=1.0001))
    assert first != second
    with pytest.raises(MorphologyError, match="PARAMETER"):
        build_morphology("crawler_bilateral_eight_legged", 7, replace(params, topology_count=2))
    with pytest.raises(MorphologyError, match="BUDGET"):
        build_morphology("radial_raised_articulated_walker", 7, MorphologyParams(topology_count=8, appendages=False))


def test_archive_descriptor_rejects_domain_boundary_without_clamping():
    source = build_morphology("crawler_bilateral_eight_legged", 1, MorphologyParams(topology_count=3, appendages=False))
    assert descriptor(source)[0] == 6
    source["anatomy"]["silhouette"]["height_m"] = 5 * source["anatomy"]["silhouette"]["length_m"]
    with pytest.raises(SearchError, match="DESCRIPTOR"):
        descriptor(source)


def _rows(slip=.02):
    row = {"frames": 100, "grounding_measurement_complete": True, "support_measurement_complete": True,
        "all_frames_max_planted_slip_m": slip, "all_frames_max_planted_hover_m": .003,
        "all_frames_max_stance_drift_m": .04, "all_frames_max_body_rise_m": .02,
        "max_body_contact_hover_m": .004, "max_surface_penetration_m": .002,
        "max_penetration_m": .001, "max_reach_fraction": .94, "support_violations": 0}
    return {course: [{**row, "speed_label": label} for label in labels]
            for course, labels in (("review", ("walk", "run", "max")),
                                   ("turns", ("walk", "run", "max")), ("reactions", ("run",)))}


def test_functional_score_uses_all_frame_maxima_and_fails_closed_on_coverage():
    baseline = _rows()
    rows = _rows(.03)
    score, diagnostics = functional_score(rows, 1., baseline)
    assert diagnostics == []
    assert score == pytest.approx(1 / (1 + .3 + .04 + .02))
    rows["turns"][2]["grounding_measurement_complete"] = False
    score, diagnostics = functional_score(rows, 1., baseline)
    assert score is None and any("MEASUREMENT_INCOMPLETE" in value for value in diagnostics)


def test_functional_score_rejects_support_and_drift_regressions():
    baseline, rows = _rows(), _rows()
    rows["turns"][0]["all_frames_max_stance_drift_m"] += .001
    rows["reactions"][0]["support_violations"] = 1
    score, diagnostics = functional_score(rows, 1., baseline)
    assert score is None
    assert any("REGRESSION" in value for value in diagnostics)
    assert any("SUPPORT_REACH" in value for value in diagnostics)


def test_lineage_latin_hypercube_replay_and_cycle():
    settings = SearchSettings(initial=34, mutations=0)
    rows = initial_samples(settings)
    assert rows == initial_samples(settings)
    assert [lineage for lineage, _, _ in rows[:17]] == list(settings.lineages)
    for lineage in settings.lineages:
        params = [params for name, _, params in rows if name == lineage]
        field, mid = ("body_length", 1.3) if lineage.startswith("amalgam_") else ("blend", 1.)
        assert sum(getattr(item, field) < mid for item in params) == 1


@pytest.mark.parametrize("count", [5, 7])
def test_compiled_odd_wave_preserves_authored_single_sector_support(count):
    from critter_crafter.config import paths
    from critter_crafter.library.catalog import compile_catalog, load_sources
    from critter_crafter.locomotion.block import support_holds
    source = build_morphology("radial_raised_articulated_walker", 1,
                              MorphologyParams(topology_count=count, appendages=False))
    sources = load_sources(paths().data)
    sources["skeletons"], sources["parts"], sources["pools"] = [source], [], []
    skeleton = compile_catalog(sources)["skeletons"][0]
    block = skeleton["locomotion"]
    for mode in ("walk", "run"):
        assert block[f"duty_{mode}"] >= (count - 1) / count + .01 - 1e-6
        assert support_holds([leg[f"{mode}_phase"] for leg in block["legs"]], block[f"duty_{mode}"], count - 1)


def test_five_limb_amalgam_source_preparation_compiles_functional_supports(tmp_path):
    from critter_crafter.skeletons.actions import resolve_attack
    from critter_crafter.skeletons.search import screen
    params = MorphologyParams(topology_count=5, appendages=False, body_length=.9, fat_ratio=.26, clearance_ratio=.45)
    source = build_morphology("amalgam_walker", 2, params)
    catalog, diagnostics = screen(source, tmp_path / "source")
    assert diagnostics == []
    skeleton = catalog["skeletons"][0]
    assert skeleton["skeleton_id"] == source["skeleton_id"]
    assert skeleton["locomotion"]["min_support"] == 2
    assert [leg["branch_id"] for leg in skeleton["locomotion"]["legs"]] == ["leg1", "leg2", "leg3", "leg4", "leg5"]
    action = resolve_attack(skeleton)
    assert action["support_release"]["contact_ids"] == []
    assert action["support_release"]["preserved_contact_ids"] == [f"leg{index}:0" for index in range(1, 6)]
    assert action["support_release"]["minimum_preserved"] == 5
    working = [branch for branch in source["branches"] if branch["branch_id"] in source["anatomy"]["support_branches"]]
    assert [branch["branch_id"] for branch in working] == ["leg1", "leg2", "leg3", "leg4", "leg5"]
    assert source["anatomy"]["traits"]["min_support"] == 2
    assert source["anatomy"]["action_support"] == {"released_contact_ids": [], "minimum_preserved": 5}
    assert sorted(branch["gait"]["phase_rad"] for branch in working) == [round(index * math.tau / 5, 6) for index in range(5)]
    assert all(branch["gait"]["support_phase"] >= .81 for branch in working)
    assert source["anatomy"]["budgets"]["parts"] <= 16
    assert source["anatomy"]["budgets"]["parts"] == len(source["branches"])
    assert len(source["branches"]) + sum(branch.get("connector_size_class") is not None
                                        for branch in source["branches"]) <= 16


def test_compact_cache_refuses_same_prefix_different_full_identity(tmp_path):
    from critter_crafter.skeletons.search import _short_directory
    first = "a" * 64
    second = "a" * 12 + "b" * 52
    directory = _short_directory(tmp_path, "c", first)
    asset = directory / "published.fbx"
    asset.write_bytes(b"first evaluated asset")
    with pytest.raises(SearchError, match="HASH_COLLISION"):
        _short_directory(tmp_path, "c", second)
    assert asset.read_bytes() == b"first evaluated asset"


def test_long_custom_search_root_rejected_before_blender(tmp_path):
    from critter_crafter.config import paths
    from critter_crafter.library.catalog import compile_catalog, load_sources
    from critter_crafter.skeletons.search import asset_path_length
    source = build_morphology("crawler_bilateral_eight_legged", 1,
                              MorphologyParams(topology_count=3, appendages=False))
    sources = load_sources(paths().data)
    sources["skeletons"], sources["parts"], sources["pools"] = [source], [], []
    catalog = compile_catalog(sources)
    with pytest.raises(SearchError, match="CC_SEARCH_PATH"):
        asset_path_length(catalog, tmp_path / ("long-root-" * 20), windows=True)
