"""Reference gait clock and step planner (catalog frame, metres, seconds).

The Unity ``StepPlanner`` (Runtime/Locomotion) is a line-for-line port in double
precision; golden tests compare the continuous quantities and per-step landing
targets, never long simulated traces (step events amplify floating-point drift).

Vocabulary
- stride (lambda): body travel per full gait cycle.
- duty: fraction of the cycle a leg is planted.
- cadence: cycles per second, ``v / stride``.
- leg phase: ``(clock + offset) mod 1``; stance while ``phase < duty``.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from .block import G, STROKE_FRACTION

EPSILON_SPEED = 1e-4
WALK_RUN_SWITCH = .5   # pattern switches at this blend weight (runtime adds hysteresis)


def smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def gait_weight(block: dict[str, Any], speed: float) -> float:
    """0 = walk, 1 = run."""
    v_walk, v_run = float(block["v_walk_mps"]), float(block["v_run_mps"])
    if v_run <= v_walk:
        return 0.0 if speed <= v_walk else 1.0
    return smoothstep((speed - v_walk) / (v_run - v_walk))


def gait_params(block: dict[str, Any], speed: float) -> dict[str, Any]:
    """Stride, cadence and duty for a ground speed; cadence is capped at cadence_max."""
    w = gait_weight(block, speed)
    duty = float(block["duty_walk"]) + (float(block["duty_run"]) - float(block["duty_walk"])) * w
    run = w >= WALK_RUN_SWITCH
    if speed <= EPSILON_SPEED:
        return {"weight": 0.0, "duty": duty, "stride_m": 0.0, "cadence_hz": 0.0, "run": False,
                "overspeed": False}
    h = float(block["hip_height_m"])
    if h <= 0.0 or float(block["usable_stroke_m"]) <= 0.0:
        return {"weight": w, "duty": duty, "stride_m": 0.0, "cadence_hz": 0.0, "run": run,
                "overspeed": True}
    stride_max = STROKE_FRACTION * float(block["usable_stroke_m"]) / duty
    if block.get("gait") == "drag":
        # Hauling: every pull uses the whole reach; speed changes the pull rate, not its length.
        stride = stride_max
    else:
        froude = speed * speed / (G * h)
        stride = min(h * 2.3 * froude ** .3, stride_max)
    cadence = speed / stride if stride > 0.0 else float(block["cadence_max_hz"])
    overspeed = cadence > float(block["cadence_max_hz"])
    if overspeed:
        cadence = float(block["cadence_max_hz"])
    return {"weight": w, "duty": duty, "stride_m": stride, "cadence_hz": cadence, "run": run,
            "overspeed": overspeed}


def slide_params(block: dict[str, Any], speed: float) -> dict[str, Any]:
    """Undulation rate for a sliding body: one cycle per travel_per_cycle_m, capped at cadence_max."""
    w = gait_weight(block, speed)
    if speed <= EPSILON_SPEED:
        return {"weight": 0.0, "cadence_hz": 0.0, "overspeed": False}
    cadence = speed / float(block["travel_per_cycle_m"])
    overspeed = cadence > float(block["cadence_max_hz"])
    return {"weight": w, "cadence_hz": min(cadence, float(block["cadence_max_hz"])), "overspeed": overspeed}


def leg_offset(leg: dict[str, Any], run: bool) -> float:
    return float(leg["run_phase"] if run else leg["walk_phase"])


def leg_phase(clock: float, offset: float) -> float:
    return (clock + offset) % 1.0


def in_stance(phase: float, duty: float) -> bool:
    return phase < duty


def landing_lead(speed: float, cadence: float, duty: float) -> float:
    """Distance ahead of home to land so the foot passes under home mid-stance."""
    if cadence <= 0.0:
        return 0.0
    return speed * duty / cadence * .5


def swing_time(cadence: float, duty: float) -> float:
    return (1.0 - duty) / cadence if cadence > 0.0 else 0.0


def landing_target_local(leg: dict[str, Any], speed: float, cadence: float, duty: float) -> list[float]:
    """Landing point in the creature's local (catalog) frame at touchdown, flat ground."""
    home = leg["home_m"]
    lead = landing_lead(speed, cadence, duty)
    return [home[0], home[1], home[2] + float(leg.get("stance_shift_m", 0.0)) + lead]


def swing_point(start: Sequence[float], end: Sequence[float], u: float, clearance: float) -> list[float]:
    """Swing trajectory: smoothstep ground-plane interpolation plus sin^2 lift."""
    s = smoothstep(u)
    lift = clearance * math.sin(math.pi * min(1.0, max(0.0, u))) ** 2
    return [start[0] + (end[0] - start[0]) * s,
            start[1] + (end[1] - start[1]) * s + lift,
            start[2] + (end[2] - start[2]) * s]


def support_count(block: dict[str, Any], clock: float, duty: float, run: bool) -> int:
    return sum(1 for leg in block["legs"] if leg.get("support", True)
               and in_stance(leg_phase(clock, leg_offset(leg, run)), duty))


def candidate_offset(index: int, stride: float) -> tuple[float, float]:
    """Lateral/forward offsets in the current planar travel frame, in stable order."""
    if not 0 <= index < 5:
        raise ValueError("landing candidate index must be in [0,5)")
    distance = .10 * max(0.0, stride)
    return ((0.0, 0.0), (0.0, distance), (0.0, -distance),
            (distance, 0.0), (-distance, 0.0))[index]


def can_lift(support: bool, remaining_supports: int, minimum_supports: int,
             early: bool, body_on_ground: bool, swinging: int, leg_count: int) -> bool:
    if support and remaining_supports < minimum_supports:
        return False
    return not early or body_on_ground or swinging < max(1, leg_count // 2)


def landing_reach_fraction(dx: float, dy: float, dz: float, reach: float) -> float:
    return math.sqrt(dx * dx + dy * dy + dz * dz) / reach if reach > 0.0 else math.inf


def choose_landing(candidates: Sequence[dict[str, Any]], stride: float, lift_allowed: bool) -> int:
    """Return the input slot of the best legal projected candidate, or -1.

    Physics supplies ground validity and the projected reach/yaw; no fallback height is a hit.
    Reach fractions use the full physical span; .95/.97 remain the landing legality bounds.
    """
    best, best_key = -1, None
    if not lift_allowed:
        return best
    for slot, candidate in enumerate(candidates):
        index = int(candidate["index"])
        if not 0 <= index < 5 or (stride <= 0.0 and index != 0):
            continue
        reach = float(candidate["reach_fraction"])
        limit = .97 if candidate["hinge"] else .95
        yaw = float(candidate.get("coxa_yaw_deg", 0.0))
        if (not candidate["ground_valid"] or not math.isfinite(reach) or reach < 0.0
                or reach > limit or not math.isfinite(yaw) or abs(yaw) > 45.0):
            continue
        lateral, forward = candidate_offset(index, stride)
        cost = max(0.0, reach - .8) ** 2
        if stride > 0.0:
            cost += (lateral * lateral + forward * forward) / (stride * stride)
        key = (cost, index, int(candidate["contact_index"]))
        if best_key is None or key < best_key:
            best, best_key = slot, key
    return best
