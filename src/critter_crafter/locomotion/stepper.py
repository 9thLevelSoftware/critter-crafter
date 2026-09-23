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
    froude = speed * speed / (G * h)
    stride = h * 2.3 * froude ** .3
    stride_max = STROKE_FRACTION * float(block["usable_stroke_m"]) / duty
    stride = min(stride, stride_max)
    cadence = speed / stride if stride > 0.0 else float(block["cadence_max_hz"])
    overspeed = cadence > float(block["cadence_max_hz"])
    if overspeed:
        cadence = float(block["cadence_max_hz"])
    return {"weight": w, "duty": duty, "stride_m": stride, "cadence_hz": cadence, "run": run,
            "overspeed": overspeed}


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
    return [home[0], home[1], home[2] + lead]


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
