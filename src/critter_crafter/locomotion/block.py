"""Compile the per-skeleton ``locomotion`` catalog block (catalog frame, metres).

The block is everything the runtime step planner needs: IK-able legs with their
neutral home contacts, usable stroke, gait phase patterns, duty factors and the
natural speed band.  Speeds follow dynamic similarity (Froude number v^2 / g h):
walk near Fr 0.25, run near Fr 1.0, capped by stroke and a leg-size step-rate limit.
"""

from __future__ import annotations

import math
from typing import Any

from .. import mathutil as mu
from ..skeletons.actions import AttackPlanError, resolve_attack
from ..skeletons.kinematics import contact_world, neutral_pose_world

LOCOMOTION_VERSION = "1.0.0"
G = 9.81
FR_WALK = .25
FR_RUN = 1.0
REACH_FRACTION = .95      # never plan targets past 95% of straight chain reach
STROKE_FRACTION = .9      # stance stroke may use 90% of the usable stroke
MIN_DUTY_RUN = .45
STEP_LIFT_FRACTION = .18
FOUR_LEG_FAMILIES = ("quadruped", "hexapod", "crawler", "radial")


def cadence_max_hz(leg_length_m: float) -> float:
    """Full gait cycles per second a leg of this size may reach (small legs scurry)."""
    return round(min(6.0, max(2.0, 3.0 / math.sqrt(max(.05, leg_length_m)))), 6)


def _stroke(hip: mu.Vec, home: mu.Vec, reach: float) -> float:
    """Symmetric forward/back stroke (+Z) through home that stays within reach."""
    dy = hip[1] - home[1]
    r2 = (REACH_FRACTION * reach) ** 2 - dy * dy
    dx = home[0] - hip[0]
    disc = r2 - dx * dx
    if disc <= 0.0:
        return 0.0
    root = math.sqrt(disc)
    dz = home[2] - hip[2]
    forward, backward = -dz + root, dz + root   # s+ and -s-
    return max(0.0, 2.0 * min(forward, backward))


def minimum_support(family: str) -> int:
    return 2 if family in FOUR_LEG_FAMILIES else 1


def support_holds(phases: list[float], duty: float, minimum: int, samples: int = 400) -> bool:
    for i in range(samples):
        t = i / samples
        if sum(1 for p in phases if (t + p) % 1.0 < duty) < minimum:
            return False
    return True


def _min_duty(phases: list[float], floor: float, minimum: int) -> float:
    duty = floor
    while duty < .95 and not support_holds(phases, duty, minimum):
        duty = round(duty + .01, 2)
    return duty


def _patterns(skeleton: dict[str, Any], legs: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    """Walk phases from gait.phase_rad; run phases from gait.run_phase_rad (e.g. a quadruped trot)."""
    def cycles(rad: float) -> float:
        return round((rad / (2 * math.pi)) % 1.0, 6)
    walk = [cycles(float(b.get("gait_phase_rad", b.get("gait", {}).get("phase_rad", 0.0)))) for b in legs]
    run = [cycles(float(b.get("gait", {}).get("run_phase_rad", b.get("gait_phase_rad", 0.0)))) for b in legs]
    return walk, run


SLIDE_TRAVEL_FRACTION = .6   # body travel per undulation cycle, as a fraction of the sliding chain
SLIDE_CADENCE_WALK = 1.0
SLIDE_CADENCE_RUN = 2.0
SLIDE_CADENCE_MAX = 3.0


def _slide_block(skeleton: dict[str, Any]) -> dict[str, Any]:
    """Limbless travel (serpentine, tentacle radial): the baked undulation is phase-driven at runtime,
    advancing one cycle per ``travel_per_cycle_m`` of ground travel."""
    sliding = [b for b in skeleton["branches"]
               if b.get("gait_role") == "locomotor"
               and any(c.get("kind") in ("body", "sliding") for c in b.get("contacts") or [])]
    if not sliding:
        return {"version": LOCOMOTION_VERSION, "mode": "none", "legs": []}
    travel = SLIDE_TRAVEL_FRACTION * max(float(b["length_m"]) for b in sliding)
    return {
        "version": LOCOMOTION_VERSION, "mode": "slide", "attack_branch_id": "",
        "travel_per_cycle_m": mu.r6(travel), "cadence_max_hz": SLIDE_CADENCE_MAX,
        "v_walk_mps": mu.r6(travel * SLIDE_CADENCE_WALK), "v_run_mps": mu.r6(travel * SLIDE_CADENCE_RUN),
        "v_max_mps": mu.r6(travel * SLIDE_CADENCE_MAX), "legs": [],
    }


DRAG_DUTY_WALK = .72     # long, slow pull; quick reach
DRAG_DUTY_RUN = .62
DRAG_CADENCE_WALK = 1.0
DRAG_CADENCE_RUN = 1.8
DRAG_LIFT_FRACTION = .25


def _drag_block(skeleton: dict[str, Any], legs: list[dict[str, Any]], legs_src: list[dict[str, Any]]) -> dict[str, Any]:
    """Grounded torso hauled by the arms: every pull uses the full stroke, the torso carries the weight
    (no minimum hand support), and the body stays on the ground."""
    pose = neutral_pose_world(skeleton)
    by_id = {b["branch_id"]: b for b in legs_src}
    for leg in legs:
        leg["clearance_m"] = mu.r6(max(leg["clearance_m"], DRAG_LIFT_FRACTION * leg["reach_m"]))
        # A pull is lopsided: from as far ahead as the arm reaches back to the shoulder plane. Use that
        # whole chord (through the wrist, which the hinge must keep in reach) and centre the stance on it.
        chain = by_id[leg["branch_id"]]["bone_names"]
        hip = pose[chain[0]]["head"]
        wrist = pose[chain[2]]["head"]
        radius2 = (REACH_FRACTION * (pose[chain[0]]["length"] + pose[chain[1]]["length"])) ** 2 - (hip[1] - wrist[1]) ** 2
        disc = radius2 - (wrist[0] - hip[0]) ** 2
        ahead = wrist[2] - hip[2]
        if disc > 0.0:
            root = math.sqrt(disc)
            forward, backward = -ahead + root, min(ahead + root, ahead)
            leg["stroke_m"] = mu.r6(max(0.0, forward + backward))
            leg["stance_shift_m"] = mu.r6((forward - backward) * .5)
    stroke = min(l["stroke_m"] for l in legs)
    stride_walk = STROKE_FRACTION * stroke / DRAG_DUTY_WALK
    stride_run = STROKE_FRACTION * stroke / DRAG_DUTY_RUN
    leg_length = sum(l["reach_m"] for l in legs) / len(legs)
    cadence_max = cadence_max_hz(leg_length)
    attack_branch = ""
    try:
        attack_branch = resolve_attack(skeleton)["effector"]["branch_id"]
    except (AttackPlanError, KeyError):
        pass
    return {
        "version": LOCOMOTION_VERSION, "mode": "legs", "gait": "drag", "body_on_ground": True,
        "attack_branch_id": attack_branch,
        "hip_height_m": mu.r6(sum(l["hip_m"][1] - l["home_m"][1] for l in legs) / len(legs)),
        "leg_length_m": mu.r6(leg_length), "usable_stroke_m": mu.r6(stroke), "min_support": 0,
        "duty_walk": DRAG_DUTY_WALK, "duty_run": DRAG_DUTY_RUN, "cadence_max_hz": cadence_max,
        "v_walk_mps": mu.r6(stride_walk * DRAG_CADENCE_WALK), "v_run_mps": mu.r6(stride_run * DRAG_CADENCE_RUN),
        "v_max_mps": mu.r6(stride_run * cadence_max), "legs": legs,
    }


def locomotion_block(skeleton: dict[str, Any]) -> dict[str, Any]:
    pose = neutral_pose_world(skeleton)
    legs_src = [b for b in skeleton["branches"]
                if b.get("gait_role") == "locomotor" and len(b.get("contacts") or []) == 1
                and b["contacts"][0].get("kind") in ("foot", "hand")]
    family = skeleton.get("family", "")
    minimum = minimum_support(family)
    walk_phases, run_phases = _patterns(skeleton, legs_src)
    legs: list[dict[str, Any]] = []
    for branch, walk_phase, run_phase in zip(legs_src, walk_phases, run_phases):
        contact = branch["contacts"][0]
        index = int(contact["bone_index"])
        chain = branch["bone_names"][:index + 1]
        hip = pose[chain[0]]["head"]
        home = contact_world(pose, branch, contact)
        reach = sum(pose[name]["length"] for name in chain)
        # The runtime two-bone hinge (limb3) holds the foot's angle, so what must stay within reach is the
        # ankle, from the hip, with thigh + shin. The stroke is measured for that point.
        if branch.get("template") == "limb3" and len(chain) == 3:
            ankle = pose[chain[2]]["head"]
            stroke_m = _stroke(hip, ankle, pose[chain[0]]["length"] + pose[chain[1]]["length"])
        else:
            stroke_m = _stroke(hip, home, reach)
        legs.append({
            "branch_id": branch["branch_id"],
            # limb3: two-bone (thigh, shin; foot keeps its angle). insect_leg4: hinge over femur,
            # tibia, tarsus with the coxa fixed. Longer single-contact chains fall back to chain IK.
            "solver": {"limb3": "two_bone", "insect_leg4": "hinge4"}.get(branch.get("template", ""), "chain"),
            "chain_bones": chain,
            "tip_local_m": mu.r6v(contact["local_point_m"]),
            "hip_m": mu.r6v(hip),
            "home_m": mu.r6v(home),
            "reach_m": mu.r6(reach),
            "stroke_m": mu.r6(stroke_m),
            # Swing lift must read from an isometric camera: at least 18% of reach.
            "clearance_m": mu.r6(max(STEP_LIFT_FRACTION * reach,
                                     float(branch.get("gait", {}).get("clearance_m", 0.0)))),
            "walk_phase": walk_phase,
            "run_phase": run_phase,
            "support": branch["branch_id"] in set(skeleton.get("anatomy", {}).get("support_branches", [])),
        })
    if not legs:
        return _slide_block(skeleton)
    # Legs present: any sliding/body contacts (a dragger's belly) are passive and ride along.
    # Degenerate fixtures (hip at or below the contact) still compile; they simply cannot walk.
    hip_height = max(0.0, sum(l["hip_m"][1] - l["home_m"][1] for l in legs) / len(legs))
    leg_length = sum(l["reach_m"] for l in legs) / len(legs)
    stroke = min(l["stroke_m"] for l in legs)
    supports = [l for l in legs if l["support"]] or legs
    if family == "dragger":
        return _drag_block(skeleton, legs, legs_src)
    walk_support = sum(float(next(b for b in legs_src if b["branch_id"] == l["branch_id"])
                             .get("gait", {}).get("support_phase", .6)) for l in legs) / len(legs)
    duty_walk = _min_duty([l["walk_phase"] for l in supports], max(.55, min(.85, walk_support)), minimum)
    duty_run = _min_duty([l["run_phase"] for l in supports], MIN_DUTY_RUN, minimum)
    cadence_max = cadence_max_hz(leg_length)
    stride_max_run = STROKE_FRACTION * stroke / duty_run
    v_max = cadence_max * stride_max_run
    v_walk = min(math.sqrt(FR_WALK * G * hip_height), .5 * v_max)
    v_run = min(math.sqrt(FR_RUN * G * hip_height), .9 * v_max)
    attack_branch = ""
    if skeleton.get("anatomy", {}).get("archetype_id"):
        try:
            attack_branch = resolve_attack(skeleton)["effector"]["branch_id"]
        except (AttackPlanError, KeyError):
            attack_branch = ""
    return {
        "version": LOCOMOTION_VERSION, "mode": "legs", "attack_branch_id": attack_branch,
        "hip_height_m": mu.r6(hip_height), "leg_length_m": mu.r6(leg_length),
        "usable_stroke_m": mu.r6(stroke), "min_support": minimum,
        "duty_walk": mu.r6(duty_walk), "duty_run": mu.r6(duty_run),
        "cadence_max_hz": cadence_max,
        "v_walk_mps": mu.r6(v_walk), "v_run_mps": mu.r6(v_run), "v_max_mps": mu.r6(v_max),
        "legs": legs,
    }
