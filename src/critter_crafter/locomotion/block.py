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
    walk = [round((float(b.get("gait_phase_rad", 0.0)) / (2 * math.pi)) % 1.0, 6) for b in legs]
    return walk, list(walk)


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
        legs.append({
            "branch_id": branch["branch_id"],
            "solver": "two_bone" if branch.get("template") == "limb3" else "chain",
            "chain_bones": chain,
            "tip_local_m": mu.r6v(contact["local_point_m"]),
            "hip_m": mu.r6v(hip),
            "home_m": mu.r6v(home),
            "reach_m": mu.r6(reach),
            "stroke_m": mu.r6(_stroke(hip, home, reach)),
            "clearance_m": mu.r6(float(branch.get("gait", {}).get("clearance_m", .12 * reach))),
            "walk_phase": walk_phase,
            "run_phase": run_phase,
            "support": branch["branch_id"] in set(skeleton.get("anatomy", {}).get("support_branches", [])),
        })
    if not legs:
        return {"version": LOCOMOTION_VERSION, "mode": "none", "legs": []}
    # Degenerate fixtures (hip at or below the contact) still compile; they simply cannot walk.
    hip_height = max(0.0, sum(l["hip_m"][1] - l["home_m"][1] for l in legs) / len(legs))
    leg_length = sum(l["reach_m"] for l in legs) / len(legs)
    stroke = min(l["stroke_m"] for l in legs)
    supports = [l for l in legs if l["support"]] or legs
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
