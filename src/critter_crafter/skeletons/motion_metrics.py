"""How much each clip actually moves, from a built ``motion.json`` (degrees and metres, pure Python).

``skeleton qa`` reports these ranges and ``qa.MIN_MOTION_DEG`` enforces a floor per clip: nothing else
notices a clip that doesn't move, since a fully static idle passes every other check.
"""

from __future__ import annotations

import math
from typing import Any


def _angle_deg(a: list[float], b: list[float]) -> float:
    dot = abs(sum(x * y for x, y in zip(a, b)))
    return math.degrees(2.0 * math.acos(min(1.0, dot)))


def _bones(sample: dict[str, Any]) -> dict[str, Any]:
    value = sample.get("bones", [])
    return value if isinstance(value, dict) else {b["name"]: b for b in value}


def motion_ranges(skeleton: dict[str, Any], motion: dict[str, Any]) -> dict[str, Any]:
    """Per clip: the largest excursion from frame 0 of any bone of each gait role, and the root's vertical range."""
    role_of: dict[str, str] = {}
    for branch in skeleton["branches"]:
        role = branch.get("gait_role") or branch.get("gait", {}).get("role", "none")
        for name in branch["bone_names"] if "bone_names" in branch else []:
            role_of[name] = role
    result: dict[str, Any] = {}
    for clip in motion.get("clips", []):
        samples = clip.get("samples", [])
        if not samples:
            continue
        first = _bones(samples[0])
        by_role: dict[str, float] = {}
        for sample in samples[1:]:
            for name, bone in _bones(sample).items():
                if name == "root" or name not in first:
                    continue
                role = role_of.get(name, "none")
                excursion = _angle_deg(first[name]["rotation_xyzw"], bone["rotation_xyzw"])
                by_role[role] = max(by_role.get(role, 0.0), excursion)
        heights = [s["root_position_m"][1] for s in samples if "root_position_m" in s]
        result[clip["name"]] = {
            "by_role_deg": {role: round(v, 3) for role, v in sorted(by_role.items())},
            "max_deg": round(max(by_role.values(), default=0.0), 3),
            "root_vertical_m": round(max(heights) - min(heights), 5) if heights else 0.0,
        }
    return result
