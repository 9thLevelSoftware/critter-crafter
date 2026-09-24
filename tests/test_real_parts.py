"""Real (sourced) parts: the pure-Python fit, source records and build wiring. No Blender needed."""

import copy
import json
import math

import pytest

from critter_crafter.config import paths
from critter_crafter.library import commands as library_commands
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.parts import commands as part_commands
from critter_crafter.parts.fit import FitError, chain_weights, fit, joint_planes, resolve_params
from critter_crafter.recipes.generator import generate, part_accepted

LIMB = [0.45, 0.45, 0.1]


def bent_tube(length=2.0, radius=0.1, bend_deg=80.0, bend_radius=0.3, rings=60, seg=12, spike=None):
    """A tube along +x that bends toward -y (its convex side is +y); optional radial spike ring index."""
    verts, faces = [], []
    theta = math.radians(bend_deg)
    straight = (length - bend_radius * theta) / 2
    ring_centres = []
    for i in range(rings + 1):
        s = length * i / rings
        if s <= straight:
            c, t = (s, 0.0, 0.0), (1.0, 0.0, 0.0)
        elif s <= straight + bend_radius * theta:
            a = (s - straight) / bend_radius
            c = (straight + bend_radius * math.sin(a), -bend_radius * (1 - math.cos(a)), 0.0)
            t = (math.cos(a), -math.sin(a), 0.0)
        else:
            e = s - straight - bend_radius * theta
            cx, cy = straight + bend_radius * math.sin(theta), -bend_radius * (1 - math.cos(theta))
            c, t = (cx + e * math.cos(theta), cy - e * math.sin(theta), 0.0), (math.cos(theta), -math.sin(theta), 0.0)
        n, b = (-t[1], t[0], 0.0), (0.0, 0.0, 1.0)
        ring_centres.append((c, n))
        for j in range(seg):
            a = 2 * math.pi * j / seg
            verts.append(tuple(c[k] + radius * (math.cos(a) * n[k] + math.sin(a) * b[k]) for k in range(3)))
    for i in range(rings):
        for j in range(seg):
            a, b2 = i * seg + j, i * seg + (j + 1) % seg
            faces.append((a, b2, b2 + seg, a + seg))
    tip = None
    if spike is not None:
        # A thin spike standing on ring `spike`, vertex 0 (the +n side), 3x the tube radius long.
        base = spike * seg
        c, n = ring_centres[spike]
        tip = len(verts)
        verts.append(tuple(c[k] + 4.0 * radius * n[k] for k in range(3)))
        for j in (seg - 1, 0):
            a, b2 = base + j, base + (j + 1) % seg
            faces.append((a, b2, tip))
    return verts, faces, tip


def test_straightens_a_bent_tube_to_the_declared_length_and_radius():
    verts, faces, _ = bent_tube()
    result = fit(verts, faces, {"axis": "+x", "radial_scale": 1.0}, "limb3", LIMB, 1.0, 0.22)
    m = result["metrics"]
    assert m["up_source"] == "bend"
    assert m["end_to_end_bend_deg"] == pytest.approx(80.0, abs=6.0)
    assert m["centerline_length_source"] == pytest.approx(2.0, rel=0.12)
    zs = [p[2] for p in result["positions"]]
    assert min(zs) == pytest.approx(0.0, abs=0.02) and max(zs) == pytest.approx(1.0, abs=0.02)
    mid = [math.hypot(p[0], p[1]) for p in result["positions"] if 0.3 < p[2] < 0.7]
    assert sum(mid) / len(mid) == pytest.approx(0.1 * m["uniform_scale"], rel=0.12)
    assert m["strain_max"] < 1.6 and m["strain_p01"] > 0.6


def test_bend_convex_side_becomes_dorsal_so_profile_flexion_rebends_it_the_same_way():
    verts, faces, _ = bent_tube(seg=12)
    result = fit(verts, faces, {"axis": "+x"}, "limb3", LIMB, 1.0, 0.22)
    # Ring vertex 0 lies on the +n side, which is the convex (+y) side before the bend.
    convex = [result["positions"][i * 12] for i in range(5, 55)]
    concave = [result["positions"][i * 12 + 6] for i in range(5, 55)]
    assert all(p[1] > 0 for p in convex)
    assert all(p[1] < 0 for p in concave)


def test_protrusions_ride_rigidly_with_their_cross_section():
    verts, faces, tip = bent_tube(spike=30)
    result = fit(verts, faces, {"axis": "+x", "radial_scale": 1.0}, "limb3", LIMB, 1.0, 0.22)
    k = result["metrics"]["uniform_scale"]
    base = [result["positions"][30 * 12 + j] for j in (11, 0, 1)]
    tip_pos = result["positions"][tip]
    spike_len = min(math.dist(tip_pos, b) for b in base)
    assert spike_len == pytest.approx(0.3 * k, rel=0.25)


def test_joint_remap_moves_the_natural_elbow_onto_the_profile_joint():
    verts, faces, _ = bent_tube(bend_deg=1.0)
    plain = fit(verts, faces, {"axis": "+x", "radial_scale": 1.0}, "limb3", LIMB, 1.0, 0.22)
    remapped = fit(verts, faces, {"axis": "+x", "radial_scale": 1.0, "joints_n": [0.6, 0.9]}, "limb3", LIMB, 1.0, 0.22)
    ring = [i * 12 for i in range(61) if abs(plain["positions"][i * 12][2] - 0.6) < 0.02]
    assert ring and all(abs(remapped["positions"][i][2] - 0.45) < 0.03 for i in ring)


def test_weights_are_normalised_and_follow_the_template_mode():
    verts, faces, _ = bent_tube()
    for template, fractions, most in (("limb3", LIMB, 2), ("insect_leg4", [0.2, 0.35, 0.35, 0.1], 2),
                                      ("tentacle8", [0.125] * 8, 2), ("head1", [1.0], 1)):
        fit_block = {"axis": "+x"} if template != "head1" else {"axis": "+x", "up": "+y"}
        result = fit(verts, faces, fit_block, template, fractions, 1.0, 0.2)
        assert all(abs(sum(w.values()) - 1.0) < 1e-9 for w in result["weights"])
        assert max(len(w) for w in result["weights"]) <= most
        assert {name for w in result["weights"] for name in w} <= {f"b{i}" for i in range(len(fractions))}
    bones = [(0.0, 0.45), (0.45, 0.9), (0.9, 1.0)]
    assert chain_weights(0.2, bones, "jointed") == {"b0": 1.0}
    blended = chain_weights(0.45, bones, "jointed")
    assert blended["b0"] == pytest.approx(0.5) and blended["b1"] == pytest.approx(0.5)


def test_straight_parts_centre_on_the_bounding_box_and_keep_their_shape():
    verts, faces, _ = bent_tube(bend_deg=1.0)
    result = fit(verts, faces, {"axis": "+x", "up": "+y"}, "head1", [1.0], 0.5, 0.14)
    assert result["metrics"]["strain_max"] == pytest.approx(1.0)
    assert result["metrics"]["radial_scale"] == 1.0


@pytest.mark.parametrize("block, code", [
    ({"axis": "x"}, "CC_FIT_AXIS"),
    ({"axis": "+x", "up": "sideways"}, "CC_FIT_UP"),
    ({"axis": "+x", "trim_n": [0.5, 0.2]}, "CC_FIT_TRIM"),
    ({"axis": "+x", "weights": "stiff"}, "CC_FIT_WEIGHTS"),
    ({"axis": "+x", "radial_scale": 9}, "CC_FIT_RADIAL"),
])
def test_invalid_fit_blocks_are_rejected(block, code):
    with pytest.raises(FitError, match=code):
        resolve_params(block, "limb3")


def test_fit_rejects_up_parallel_to_the_root():
    verts, faces, _ = bent_tube(bend_deg=1.0)
    with pytest.raises(FitError, match="CC_FIT_UP"):
        fit(verts, faces, {"axis": "+x", "up": "+x"}, "limb3", LIMB, 1.0, 0.22)


def _catalog():
    return compile_catalog(load_sources(paths().data))


def real_parts(catalog):
    return [p for p in catalog["parts"] if p.get("real")]


def test_real_part_records_compile_without_machine_paths_or_mesh_bytes():
    catalog = _catalog()
    parts = real_parts(catalog)
    assert len(parts) >= 3
    for part in parts:
        real = part["real"]
        assert set(real) == {"archive_path", "sha256", "fit", "texture_size"}
        assert not real["archive_path"].startswith("/") and ":" not in real["archive_path"]
        assert part["inventory_kind"] == "production" and part["source"] == "meshy"
        assert part["status"] in ("draft", "approved", "rejected")
        assert part["girth_m"] == pytest.approx(
            next(p["girth_ratio"] for p in catalog["binding_profiles"]
                 if p["binding_profile_id"] == part["binding_profile_id"]) * part["length_m"], abs=1e-6)
    for path in (paths().data / "parts").glob("*.part.json"):
        assert path.stat().st_size < 16_000, f"{path} looks like it carries mesh data"


def test_each_real_part_fits_branches_of_its_profile():
    catalog = _catalog()
    for part in real_parts(catalog):
        accepted = [b for s in catalog["skeletons"] for b in s["branches"] if part_accepted(part, b)]
        assert accepted, f"{part['part_id']} fits no branch"


def test_draft_real_parts_never_generate_but_approved_ones_can():
    catalog = _catalog()
    for skeleton in catalog["skeletons"]:
        skeleton["status"] = "approved"
    ids = {p["part_id"] for p in real_parts(catalog)}
    pools = [p["pool_id"] for p in catalog["pools"]]
    drafts = {f["part_id"] for pool in pools for seed in range(1, 41) for f in generate(catalog, pool, seed)["fills"]}
    assert not drafts & ids
    approved = copy.deepcopy(catalog)
    for part in real_parts(approved):
        part["status"] = "approved"
    used = {f["part_id"] for pool in pools for seed in range(1, 201) for f in generate(approved, pool, seed)["fills"]}
    assert used & ids


def test_build_jobs_carry_the_resolved_archive_source(tmp_path, monkeypatch):
    catalog = _catalog()
    part = real_parts(catalog)[0]
    archive = tmp_path / "archive"
    source = archive / part["real"]["archive_path"]
    source.parent.mkdir(parents=True)
    source.write_bytes(b"glTF")
    monkeypatch.setattr(library_commands, "find_asset_archive", lambda: archive)
    job = next(j for j in library_commands.build_jobs(catalog, tmp_path / "lib", {part["part_id"]}))
    assert job["op"] == "realpart" and job["args"]["source_path"] == str(source)
    assert job["args"]["out_albedo_png"].endswith(f"{part['part_id']}_albedo.png")
    monkeypatch.setattr(library_commands, "find_asset_archive", lambda: None)
    job = next(j for j in library_commands.build_jobs(catalog, tmp_path / "lib", {part["part_id"]}))
    assert job["args"]["source_path"] == ""


def test_real_part_pipeline_is_fingerprinted_separately_from_placeholders():
    catalog = _catalog()
    real = real_parts(catalog)[0]
    reference = next(p for p in catalog["parts"] if not p.get("real"))
    assert library_commands.part_pipeline_for(real) == library_commands.realpart_pipeline_fingerprint()
    assert library_commands.part_pipeline_for(reference) == library_commands.part_pipeline_fingerprint()
    assert library_commands.realpart_pipeline_fingerprint() != library_commands.part_pipeline_fingerprint()


def test_rebuilds_must_reproduce_the_declared_envelope_and_source():
    part = real_parts(_catalog())[0]
    good = {"source_sha256": part["real"]["sha256"], "fit": {"dimensions_m": list(part["dimensions_m"])},
            "max_influences": 2, "min_weight_sum": 1.0, "max_weight_sum": 1.0}
    assert library_commands.real_part_problems(part, good) == []
    drift = copy.deepcopy(good)
    drift["fit"]["dimensions_m"][2] += 0.01
    assert any("CC_PART_FIT_DRIFT" in p for p in library_commands.real_part_problems(part, drift))
    changed = dict(good, source_sha256="0" * 64)
    assert any("CC_REALPART_SOURCE_CHANGED" in p for p in library_commands.real_part_problems(part, changed))


def test_default_length_is_the_median_branch_length_rounded():
    catalog = _catalog()
    profile = next(p for p in catalog["binding_profiles"] if p["binding_profile_id"] == "insect_leg4_articulated")
    length = part_commands.default_length(catalog, profile)
    assert 0.6 < length < 1.2 and round(length / 0.05, 6) == round(length / 0.05)


def test_deformation_judge_is_relative_to_the_replaced_placeholder():
    reference = {"strain_p99": 1.3, "strain_p01": 0.7, "flipped": 0.0}
    assert part_commands.judge({"strain_p99": 1.5, "strain_p01": 0.65, "flipped": 0.001}, reference) == []
    assert part_commands.judge({"strain_p99": 1.9, "strain_p01": 0.65, "flipped": 0.0}, reference)
    assert part_commands.judge({"strain_p99": 1.2, "strain_p01": 0.4, "flipped": 0.0}, reference)
    assert part_commands.judge({"strain_p99": 1.2, "strain_p01": 0.7, "flipped": 0.02}, reference)
    lenient = {"strain_p99": 2.0, "strain_p01": 0.5, "flipped": 0.01}
    assert part_commands.judge({"strain_p99": 2.4, "strain_p01": 0.42, "flipped": 0.016}, lenient) == []


def test_part_record_uses_the_profile_girth_and_keeps_only_references():
    catalog = _catalog()
    profile = next(p for p in catalog["binding_profiles"] if p["binding_profile_id"] == "limb3_plantigrade")
    record = part_commands.part_record(
        part_id="x_v1", profile=profile, category="limb", length_m=0.8, side="symmetric", tags=["b", "a"],
        roles=["locomotor"], archive_path="a/b", sha256="0" * 64, fit={"axis": "+x"}, max_triangles=2500,
        albedo="#123456", provenance={"source": "meshy"})
    assert record["girth_m"] == pytest.approx(0.8 * profile["girth_ratio"])
    assert record["species_tags"] == ["a", "b"] and record["status"] == "draft"
    json.dumps(record)


def test_edge_loops_sit_where_the_weights_change_slope():
    bones = [(0.0, 0.45), (0.45, 0.9), (0.9, 1.0)]
    assert joint_planes(bones, [0.05, 0.03], "flesh") == [0.4, 0.45, 0.5, 0.87, 0.9, 0.93]
    assert joint_planes(bones, None, "smooth") == [0.225, 0.45, 0.675, 0.9, 0.95]
    assert joint_planes([(0.0, 1.0)], None, "single") == []


@pytest.mark.parametrize("archive_path", ["../outside.glb", "/etc/outside.glb", "a/../../outside.glb"])
def test_real_part_sources_cannot_leave_the_asset_archive(tmp_path, archive_path):
    import click

    archive = tmp_path / "archive"
    archive.mkdir()
    (tmp_path / "outside.glb").write_bytes(b"glTF")
    part = {"part_id": "x_v1", "real": {"archive_path": archive_path}}
    with pytest.raises(click.ClickException, match="CC_ARCHIVE_PATH"):
        library_commands.real_part_source(part, archive)


@pytest.mark.parametrize("archive_path, ok", [
    ("procedural-biomass-assembly/artifacts/x/01a0", True), ("../x", False), ("/x", False),
    ("a/../b", False), ("a\\b", False), ("C:/x", False),
])
def test_schema_pins_archive_paths_inside_the_archive(archive_path, ok):
    import jsonschema

    schema = json.loads((paths().schemas / "part.v3.schema.json").read_text(encoding="utf-8"))
    record = json.loads(next((paths().data / "parts").glob("meshy_*.part.json")).read_text(encoding="utf-8"))
    record["real"]["archive_path"] = archive_path
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(record))
    assert (not errors) == ok, errors


def test_refit_resolves_the_records_pinned_profile_version():
    catalog = _catalog()
    newer = copy.deepcopy(next(p for p in catalog["binding_profiles"] if p["binding_profile_id"] == "limb3_plantigrade"))
    newer["binding_profile_version"] = "9.0.0"
    newer["bone_fractions"] = [0.3, 0.5, 0.2]
    catalog["binding_profiles"].append(newer)
    assert part_commands._profile(catalog, "limb3_plantigrade")["binding_profile_version"] == "9.0.0"
    pinned = part_commands._profile(catalog, "limb3_plantigrade", "1.0.0")
    assert pinned["binding_profile_version"] == "1.0.0" and pinned["bone_fractions"] == [0.45, 0.45, 0.1]


def test_a_review_goes_stale_when_any_reviewed_input_changes(tmp_path, monkeypatch):
    catalog = _catalog()
    monkeypatch.setattr(library_commands, "part_build_state", lambda part, out, **_: {"part": part["part_id"]})
    for skeleton in catalog["skeletons"]:
        skeleton["content_fingerprint"] = "built-" + skeleton["skeleton_id"]
    part = next(p for p in real_parts(catalog) if p["binding_profile_id"] == "insect_leg4_articulated")
    plans = part_commands.plan_review(catalog, part, 3)
    assert len(plans) == 3 and all(replaced for *_, replaced in plans)
    recorded = part_commands.review_inputs(catalog, tmp_path, part, 3, plans)
    assert recorded["parts"] and part["part_id"] not in recorded["parts"]
    assert part_commands.stale_review_inputs(recorded, recorded) == []
    assert part_commands.stale_review_inputs(None, recorded) == sorted(recorded)

    reviewed = plans[0][0]["skeleton_id"]
    next(s for s in catalog["skeletons"] if s["skeleton_id"] == reviewed)["content_fingerprint"] = "rebaked"
    current = part_commands.review_inputs(catalog, tmp_path, part, 3, part_commands.plan_review(catalog, part, 3))
    assert part_commands.stale_review_inputs(recorded, current) == ["skeletons"]

    monkeypatch.setattr(part_commands, "qa_pipeline_fingerprint", lambda: "changed judge")
    current = part_commands.review_inputs(catalog, tmp_path, part, 3, plans)
    assert "qa_pipeline" in part_commands.stale_review_inputs(recorded, current)


def test_default_length_uses_only_branches_of_the_exact_profile_version():
    catalog = _catalog()
    old = next(p for p in catalog["binding_profiles"] if p["binding_profile_id"] == "insect_leg4_articulated")
    before = part_commands.default_length(catalog, old)
    newer = dict(copy.deepcopy(old), binding_profile_version="9.0.0", binding_profile_hash="f" * 64)
    catalog["binding_profiles"].append(newer)
    moved = 0
    for skeleton in catalog["skeletons"]:
        for branch in skeleton["branches"]:
            if branch["binding_profile_id"] == old["binding_profile_id"] and moved < 60:
                branch.update(binding_profile_version="9.0.0", binding_profile_hash="f" * 64, length_m=4.0)
                moved += 1
    assert part_commands.default_length(catalog, newer) == 4.0
    remaining = part_commands.default_length(catalog, old)
    assert remaining != 4.0 and abs(remaining - before) < 0.3


def test_qa_fingerprint_covers_the_whole_verdict_module(monkeypatch):
    from pathlib import Path

    before = part_commands.qa_pipeline_fingerprint()
    original = Path.read_bytes

    def edited(self):
        data = original(self)
        return data + b"\n# verdict logic changed\n" if self.as_posix().endswith("parts/commands.py") else data

    monkeypatch.setattr(Path, "read_bytes", edited)
    assert part_commands.qa_pipeline_fingerprint() != before
