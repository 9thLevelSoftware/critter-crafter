"""Static QA of a compiled ``locomotion`` block (no Blender): speed bands, support and step rates.

The runtime planner is exercised at the published walk and run speeds; anything a game would hit
at those speeds (overspeed, lost support, zero stroke) is a diagnostic.
"""

from __future__ import annotations

import math
from typing import Any

from . import stepper
from .block import support_holds


def evaluate_locomotion(skeleton: dict[str, Any]) -> list[dict[str, Any]]:
    if "locomotion" not in skeleton:
        return []   # legacy/fixture skeletons compiled without a locomotion block
    block = skeleton["locomotion"] or {}
    out: list[dict[str, Any]] = []

    def fail(code: str, detail: str) -> None:
        out.append({"code": code, "clip": "", "frame": None, "detail": detail})

    mode = block.get("mode")
    if mode not in ("legs", "slide"):
        fail("CC_LOCOMOTION_MODE", f"expected legs or slide, got {mode!r}")
        return out
    v_walk, v_run, v_max = (float(block.get(k, 0.0)) for k in ("v_walk_mps", "v_run_mps", "v_max_mps"))
    if not 0.0 < v_walk < v_run <= v_max + 1e-9:
        fail("CC_LOCOMOTION_SPEEDS", f"walk {v_walk:.3f} < run {v_run:.3f} <= max {v_max:.3f} violated")
    if mode == "slide":
        if float(block.get("travel_per_cycle_m", 0.0)) <= 0.0:
            fail("CC_LOCOMOTION_SLIDE", "travel_per_cycle_m must be positive")
        for speed in (v_walk, v_run):
            if stepper.slide_params(block, speed)["overspeed"]:
                fail("CC_LOCOMOTION_OVERSPEED", f"slide overspeed at {speed:.3f} m/s")
        return out

    legs = block.get("legs") or []
    if not legs:
        fail("CC_LOCOMOTION_LEGS", "legs mode without legs")
        return out
    for leg in legs:
        if float(leg.get("stroke_m", 0.0)) <= 0.0:
            fail("CC_LOCOMOTION_STROKE", f"{leg['branch_id']}: no usable stroke")
        if float(leg.get("clearance_m", 0.0)) <= 0.0:
            fail("CC_LOCOMOTION_CLEARANCE", f"{leg['branch_id']}: no swing clearance")
    supports = [leg for leg in legs if leg.get("support")] or legs
    minimum = int(block.get("min_support", 0))
    for key, duty in (("walk_phase", block["duty_walk"]), ("run_phase", block["duty_run"])):
        if not support_holds([leg[key] for leg in supports], float(duty), minimum):
            fail("CC_LOCOMOTION_SUPPORT", f"{key} pattern at duty {duty} leaves fewer than {minimum} supports")
    for label, speed in (("walk", v_walk), ("run", v_run)):
        params = stepper.gait_params(block, speed)
        if params["overspeed"]:
            fail("CC_LOCOMOTION_OVERSPEED", f"{label} speed {speed:.3f} m/s exceeds the step-rate cap")
        if params["stride_m"] * params["duty"] > stepper.STROKE_FRACTION * float(block["usable_stroke_m"]) + 1e-9:
            fail("CC_LOCOMOTION_STRIDE", f"{label} stance stroke exceeds the usable stroke")
    if block.get("body_on_ground") and not block.get("body_pivot_m"):
        fail("CC_LOCOMOTION_PIVOT", "grounded body without a pivot")
    return out


def golden_rows(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Planner values the C# StepPlanner must reproduce (continuous quantities, per-step landings)."""
    rows = []
    rows.extend(landing_golden_rows())
    for skeleton in catalog["skeletons"]:
        block = skeleton.get("locomotion") or {}
        mode = block.get("mode")
        if mode not in ("legs", "slide"):
            continue
        v_walk, v_run = float(block["v_walk_mps"]), float(block["v_run_mps"])
        for speed in (0.3, v_walk, .5 * (v_walk + v_run), v_run, 2.5):
            speed = round(speed, 6)
            if mode == "slide":
                params = stepper.slide_params(block, speed)
                rows.append({"skeleton_id": skeleton["skeleton_id"], "speed": speed, "mode": mode,
                             "cadence_hz": params["cadence_hz"], "overspeed": params["overspeed"]})
                continue
            params = stepper.gait_params(block, speed)
            rows.append({
                "skeleton_id": skeleton["skeleton_id"], "speed": speed, "mode": mode,
                "weight": params["weight"], "duty": params["duty"], "stride_m": params["stride_m"],
                "cadence_hz": params["cadence_hz"], "run": params["run"], "overspeed": params["overspeed"],
                # Flattened x, y, z per leg (Unity's JsonUtility cannot read nested arrays).
                "landings": [v for leg in block["legs"]
                             for v in stepper.landing_target_local(leg, speed, params["cadence_hz"], params["duty"])],
            })
    return rows


def landing_golden_rows() -> list[dict[str, Any]]:
    """Physical projections and support states shared with the allocation-free C# chooser."""
    fixtures = [
        ("neutral", .5, (0.0, 0.0, .5), False, 0.0, [True] * 5, True, 2, 2, False, False, 0, 6),
        ("support_release_blocked", 1.0, (0.0, 0.0, .5), False, 0.0, [True] * 5, True, 1, 2, False, False, 0, 4),
        ("grounded_overrun", 1.0, (0.0, 0.0, .9405), False, 0.0, [True] * 5, True, 0, 0, True, True, 2, 2),
        ("early_swing_cap_blocked", 1.0, (0.0, 0.0, .9405), False, 0.0, [True] * 5, False, 2, 2, True, False, 2, 4),
        ("instant_turn", 1.0, (0.0, 0.0, .9405), False, 0.0, [True] * 5, True, 3, 2, True, False, 1, 6),
        ("ramp", 1.0, (0.0, .5, .8), False, 0.0, [True] * 5, True, 2, 2, False, False, 0, 4),
        ("no_ground", 1.0, (0.0, 0.0, .5), False, 0.0, [False] * 5, True, 2, 2, False, False, 0, 4),
        ("zero_stride_no_zero_hit", 0.0, (0.0, 0.0, .5), False, 0.0, [False, True, True, True, True], True, 2, 2, False, False, 0, 4),
        ("hinge_yaw_blocked", 1.0, (0.0, 0.0, .5), True, 45.001, [True] * 5, True, 2, 2, False, False, 0, 6),
        ("hinge_limit", 0.0, (0.0, 0.0, .97), True, 45.0, [True] * 5, True, 2, 2, False, False, 0, 6),
        ("asymmetric_group_tie", 1.0, (0.0, 0.0, 0.0), False, 0.0, [False, True, True, True, True], True, 2, 2, False, False, 0, 5),
        ("degenerate_reach", 0.0, (0.0, 0.0, 0.0), False, 0.0, [True] * 5, True, 2, 2, False, False, 0, 4),
        ("physical_cost", 1.0, (0.0, -.72, .54), True, 0.0, [True] * 5, True, 2, 2, False, False, 0, 6),
    ]
    rows = []
    for name, stride, base, hinge, yaw, hits, support, remaining, minimum, early, grounded, swinging, count in fixtures:
        reach = 0.0 if name == "degenerate_reach" else 1.0
        candidates = []
        for index in range(5):
            lateral, forward = stepper.candidate_offset(index, stride)
            dx, dy, dz = base[0] + lateral, base[1], base[2] + forward
            if name == "ramp":
                dy += forward * math.tan(math.radians(20.0))
            fraction = stepper.landing_reach_fraction(dx, dy, dz, reach)
            # JSON forbids infinity; an invalid negative fraction denotes the degenerate fixture.
            if reach <= 0.0:
                fraction = -1.0
            candidates.append({"index": index, "contact_index": 3, "dx": dx, "dy": dy, "dz": dz,
                               "hinge": hinge, "reach_fraction": fraction,
                               "coxa_yaw_deg": yaw, "ground_valid": hits[index]})
        if name == "asymmetric_group_tie":
            candidates = [candidates[4], candidates[2], candidates[1], candidates[3], candidates[0],
                          {**candidates[1], "contact_index": 1}]
        allowed = stepper.can_lift(support, remaining, minimum, early, grounded, swinging, count)
        rows.append({"mode": "landing", "case_id": name, "stride_m": stride, "hinge": hinge, "reach_m": reach,
                     "support": support, "remaining_supports": remaining, "minimum_supports": minimum,
                     "early": early, "body_on_ground": grounded, "swinging": swinging, "leg_count": count,
                     "candidates": candidates, "chosen_slot": stepper.choose_landing(candidates, stride, allowed)})
    return rows
