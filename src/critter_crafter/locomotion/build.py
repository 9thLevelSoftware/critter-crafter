"""Per-creature gait and speed from the assembled build (model ``build-1``).

A skeleton publishes one locomotion block; a creature built on it is heavier or lighter, more or
less leg-driven, better or worse balanced.  ``creature_locomotion`` turns the recipe's fills into a
mass, a centre of mass and a performance factor P, and rescales the skeleton block's speeds with it.
Leg geometry, duties, phases and strokes are untouched, so the stroke limit still keeps feet planted.

Parity: the C# port (Runtime/Generation/CreatureLocomotion.cs) matches this within 1e-6.  Only
arithmetic, min, max, clamp, sqrt and pow are used, fills are visited in recipe order, nothing
branches on a float, and values are rounded (r6) only when written out.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from .. import mathutil as mu
from .block import G, STROKE_FRACTION

MODEL = "build-1"
# Category -> (density kg/m^3, shape factor).  Connectors add no mass.
CATEGORY_MASS = {
    "limb": (1050.0, .60),
    "core": (1000.0, .70),
    "head": (1100.0, .52),
    "tail": (1000.0, .55),
    "appendage": (1000.0, .55),
}
DEFAULT_MASS = (1000.0, .55)
ARM_PROFILE = "limb3_brachial"
MUSCLE_REFERENCE = .45
REFERENCE_MASS_KG = 60.0
PERFORMANCE_MIN, PERFORMANCE_MAX = .4, 1.8
CADENCE_MIN, CADENCE_MAX = 2.0, 6.0
SUPPORT_MARGIN_M = .05
RUN_FROUDE = 2.2            # a build at P = 1 runs a little above the skeleton's Fr 1.0
WALK_FROUDE_ROOT = .5       # sqrt(Fr 0.25)
WALK_FRACTION_OF_MAX = .5
WALK_FRACTION_OF_RUN = .6
RUN_FRACTION_OF_MAX = .9
MIN_RUN_MPS = 1.0
MIN_SWING_S = .12           # a swing shorter than ~4 frames cannot land its foot: planted feet slide
DRAG_CADENCE_WALK = 1.0     # pulls per second at P = 1 (matches block.DRAG_CADENCE_WALK)
DRAG_CADENCE_RUN = 1.8
SLIDE_CADENCE_WALK = 1.0
SLIDE_CADENCE_RUN = 2.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _fill_mass(fill: dict[str, Any], part: dict[str, Any], branch: dict[str, Any]) -> float:
    density, shape = CATEGORY_MASS.get(part["category"], DEFAULT_MASS)
    thickness = float(part["dimensions_m"][1]) * float(fill["length_scale"]) * float(fill["girth_scale"])
    return density * shape * (int(branch["length_mm"]) / 1000.0) * (int(branch["girth_mm"]) / 1000.0) * thickness


def creature_locomotion(catalog: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    """The skeleton's locomotion block with speeds and ``build`` replaced by this creature's."""
    skeleton = next(s for s in catalog["skeletons"] if s["skeleton_id"] == recipe["skeleton_id"])
    parts = {p["part_id"]: p for p in catalog["parts"]}
    branches = {b["branch_id"]: b for b in skeleton["branches"]}
    block = copy.deepcopy(skeleton["locomotion"])
    segments = {s["branch_id"]: s["centroid_m"] for s in block.get("segments", [])}
    mode = block.get("mode", "none")
    leg_ids = {leg["branch_id"] for leg in block.get("legs", [])}

    total = 0.0
    muscle_mass = 0.0
    weighted = (0.0, 0.0, 0.0)
    limb_fills = 0
    arm_fills = 0
    for fill in recipe["fills"]:
        part = parts[fill["part_id"]]
        mass = _fill_mass(fill, part, branches[fill["branch_id"]])
        total += mass
        if fill["branch_id"] in leg_ids:
            muscle_mass += mass
        weighted = mu.add(weighted, mu.scale(segments.get(fill["branch_id"], (0.0, 0.0, 0.0)), mass))
        if part["category"] == "limb":
            limb_fills += 1
            if fill["binding_profile_id"] == ARM_PROFILE:
                arm_fills += 1
    total = max(total, 1e-9)
    com = mu.scale(weighted, 1.0 / total)
    muscle = muscle_mass / total
    arm = arm_fills / max(1, limb_fills)
    mass_term = math.pow(REFERENCE_MASS_KG / total, .1)

    imbalance = 0.0
    if mode == "legs" and block.get("gait") != "drag" and block["legs"]:
        homes = [leg["home_m"] for leg in block["legs"]]
        cx = sum(h[0] for h in homes) / len(homes)
        cz = sum(h[2] for h in homes) / len(homes)
        radius = max(math.hypot(h[0] - cx, h[2] - cz) for h in homes)
        imbalance = math.hypot(com[0] - cx, com[2] - cz) / (radius + SUPPORT_MARGIN_M)

    if mode == "legs":
        performance = (math.sqrt(muscle / MUSCLE_REFERENCE) * (1.0 - .4 * min(1.0, imbalance))
                       * (1.0 - .25 * arm) * mass_term)
    elif mode == "slide":
        performance = mass_term
    else:
        performance = 1.0
    performance = _clamp(performance, PERFORMANCE_MIN, PERFORMANCE_MAX) if mode != "none" else 1.0

    if mode == "legs":
        cadence = _clamp(float(block["cadence_max_hz"]) * performance, CADENCE_MIN, CADENCE_MAX)
        stroke = float(block["usable_stroke_m"])
        duty_run = float(block["duty_run"])
        if block.get("gait") == "drag":
            stride_walk = STROKE_FRACTION * stroke / float(block["duty_walk"])
            stride_run = STROKE_FRACTION * stroke / duty_run
            v_max = stride_run * cadence
            v_run = min(stride_run * DRAG_CADENCE_RUN * performance * performance, v_max)
            v_walk = min(stride_walk * DRAG_CADENCE_WALK * performance,
                         WALK_FRACTION_OF_MAX * v_max, WALK_FRACTION_OF_RUN * v_run)
        else:
            h = float(block["hip_height_m"])
            # High run duty (a tripod) leaves little swing time: cap the run cadence so a swing takes MIN_SWING_S.
            run_cadence = min(cadence, (1.0 - duty_run) / MIN_SWING_S)
            v_max = run_cadence * STROKE_FRACTION * stroke / duty_run
            # The planner's stride is min(2.3 h Fr^.3, stroke stride); cadence stays under its cap only
            # up to the speed where v = cadence * 2.3 h (v^2/(g h))^.3, i.e. v_froude.
            v_froude = math.pow(run_cadence * 2.3 * h * math.pow(G * h, -.3), 2.5)
            v_cap = RUN_FRACTION_OF_MAX * min(v_max, v_froude)
            v_run = max(min(MIN_RUN_MPS, v_cap),
                        min(performance * performance * math.sqrt(RUN_FROUDE * G * h), v_cap))
            v_walk = min(WALK_FROUDE_ROOT * math.sqrt(G * h) * math.sqrt(performance),
                         WALK_FRACTION_OF_MAX * v_max, WALK_FRACTION_OF_RUN * v_run)
    elif mode == "slide":
        travel = float(block["travel_per_cycle_m"])
        cadence = _clamp(float(block["cadence_max_hz"]) * performance, CADENCE_MIN, CADENCE_MAX)
        v_max = travel * cadence
        v_run = min(travel * SLIDE_CADENCE_RUN * performance * performance, v_max)
        v_walk = min(travel * SLIDE_CADENCE_WALK * performance,
                     WALK_FRACTION_OF_MAX * v_max, WALK_FRACTION_OF_RUN * v_run)
    else:
        cadence = float(block.get("cadence_max_hz", 0.0))
        v_walk, v_run, v_max = (float(block.get(k, 0.0)) for k in ("v_walk_mps", "v_run_mps", "v_max_mps"))

    block["cadence_max_hz"] = mu.r6(cadence)
    block["v_walk_mps"], block["v_run_mps"], block["v_max_mps"] = mu.r6(v_walk), mu.r6(v_run), mu.r6(v_max)
    block["move_speed_mps"] = block["v_run_mps"]
    block["build"] = {
        "model": MODEL, "mass_kg": mu.r6(total), "com_m": mu.r6v(com),
        "muscle_fraction": mu.r6(muscle), "load_imbalance": mu.r6(imbalance),
        "arm_fraction": mu.r6(arm), "performance": mu.r6(performance),
    }
    return block


def creature_row(catalog: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    """One flat golden row (Unity's JsonUtility cannot read nested arrays)."""
    block = creature_locomotion(catalog, recipe)
    build = block["build"]
    return {
        "skeleton_id": recipe["skeleton_id"], "recipe_id": recipe["recipe_id"],
        "branch_ids": [f["branch_id"] for f in recipe["fills"]],
        "part_ids": [f["part_id"] for f in recipe["fills"]],
        "length_scales": [f["length_scale"] for f in recipe["fills"]],
        "girth_scales": [f["girth_scale"] for f in recipe["fills"]],
        "mass_kg": build["mass_kg"], "com_m": build["com_m"], "performance": build["performance"],
        "v_walk": block["v_walk_mps"], "v_run": block["v_run_mps"], "v_max": block["v_max_mps"],
        "cadence_max": block["cadence_max_hz"],
    }


GOLDEN_POOL_SEEDS = range(1, 11)


def golden_rows(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Reference recipe of every skeleton, then every pool at seeds 1-10.  ``catalog`` must already
    carry the fixture-only approval of drafts and the Meshy parts (``recipe golden`` does that)."""
    from ..recipes.generator import generate, generate_for_skeleton
    rows = [creature_row(catalog, generate_for_skeleton(catalog, s["skeleton_id"], 1))
            for s in catalog["skeletons"]]
    for pool in catalog["pools"]:
        for seed in GOLDEN_POOL_SEEDS:
            rows.append(creature_row(catalog, generate(catalog, pool["pool_id"], seed)))
    return rows
