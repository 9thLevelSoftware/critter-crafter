"""Seeded variants of the curated archetypes: new proportions, the same anatomy."""

from __future__ import annotations

import pytest

from critter_crafter.skeletons.archetypes import (
    ARCHETYPES, PRESETS, build_candidate, build_variant, generate_all, variant_id, variant_seeds_for,
)


def test_variants_are_deterministic_and_carry_the_seed_in_their_id():
    for archetype in ARCHETYPES:
        for seed in variant_seeds_for(archetype):
            variant = build_variant(archetype, seed)
            assert variant == build_variant(archetype, seed)
            assert variant["skeleton_id"] == variant_id(archetype, seed) == f"{archetype}_s{seed:03d}_v3"
            assert variant["provenance"]["seed"] == seed and variant["provenance"]["preset"] == "blend"


def test_variants_keep_the_anatomy_of_their_archetype():
    """Topology never changes: same branches, roles, contacts, supports and attack effector names."""
    for archetype in ARCHETYPES:
        anchor = build_candidate(archetype, "balanced")
        for seed in variant_seeds_for(archetype):
            variant = build_variant(archetype, seed)
            assert [b["branch_id"] for b in variant["branches"]] == [b["branch_id"] for b in anchor["branches"]]
            assert [b["gait"]["role"] for b in variant["branches"]] == [b["gait"]["role"] for b in anchor["branches"]]
            assert variant["anatomy"]["support_branches"] == anchor["anatomy"]["support_branches"]
            assert variant["anatomy"]["archetype_id"] == archetype
            assert variant["anatomy"]["traits"] == anchor["anatomy"]["traits"]


def test_variants_are_not_copies_of_the_presets():
    for archetype in ARCHETYPES:
        presets = {tuple(round(b["length_m"], 3) for b in build_candidate(archetype, p)["branches"]) for p in PRESETS}
        for seed in variant_seeds_for(archetype):
            lengths = tuple(round(b["length_m"], 3) for b in build_variant(archetype, seed)["branches"])
            assert lengths not in presets, (archetype, seed)


def test_different_seeds_make_different_creatures():
    for archetype in ARCHETYPES:
        variants = [build_variant(archetype, seed) for seed in (2, 3, 4, 5)]
        silhouettes = {(v["anatomy"]["silhouette"]["height_m"], v["anatomy"]["silhouette"]["length_m"]) for v in variants}
        assert len(silhouettes) == len(variants), archetype


def test_the_recorded_knobs_stay_in_range():
    for archetype in ARCHETYPES:
        for seed in range(2, 12):
            knobs = build_variant(archetype, seed)["provenance"]["variant"]
            assert 0.0 <= knobs["blend"] <= 2.0
            assert .94 <= knobs["height"] <= 1.06 and .92 <= knobs["length"] <= 1.10
            assert .92 <= knobs["width"] <= 1.10 and .93 <= knobs["limb"] <= 1.08 and .95 <= knobs["stance"] <= 1.05


def test_generate_all_includes_every_committed_variant_once():
    ids = [s["skeleton_id"] for s in generate_all()]
    assert len(ids) == len(set(ids))
    for archetype in ARCHETYPES:
        for seed in variant_seeds_for(archetype):
            assert variant_id(archetype, seed) in ids


@pytest.mark.parametrize("style", ["horror"])
def test_variants_are_only_generated_for_the_anatomical_style(style):
    assert not any("_s0" in s["skeleton_id"] for s in generate_all(style=style))
