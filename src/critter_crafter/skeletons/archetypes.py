"""Curated, deterministic anatomy candidates for the v3 source library.

The geometry here is deliberately authored from a small set of body plans.  A seed
only makes bounded dimensional choices; it never changes topology, support limbs,
or symmetry.  That keeps each candidate reviewable and motion-ready.
"""

from __future__ import annotations

import math
import json
from pathlib import Path
from typing import Any

from ..config import paths
from .. import mathutil as mu
from ..recipes.rng import SplitMix64

PRESETS = ("compact", "balanced", "elongated")
FAMILIES = ("biped", "quadruped", "crawler", "hexapod", "radial", "serpentine", "dragger")
_GROUND_CLEARANCE_M = .0075
# Hands rest a centimetre higher than feet: a forearm sloping down to a planted hand would otherwise
# dip its rounded volume below the floor.
_HAND_CLEARANCE_M = .0175


def _contact_clearance(kind: str) -> float:
    return 0.0 if kind in {"sliding", "body"} else _HAND_CLEARANCE_M if kind == "hand" else _GROUND_CLEARANCE_M

# Neutral hip-to-foot distance as a fraction of chain reach (stepping margin).
_INSECT_EXTENSION = .70
_LIMB_EXTENSION = .84

ARCHETYPES: dict[str, dict[str, Any]] = {
    "biped_plantigrade_humanoid": {"family": "biped", "plan": "biped", "variant": "plantigrade", "height": 1.72, "length": 0.66, "width": 0.46},
    "biped_digitigrade_creature": {"family": "biped", "plan": "biped", "variant": "digitigrade", "height": 1.63, "length": 0.92, "width": 0.52},
    "quadruped_stocky_plantigrade": {"family": "quadruped", "plan": "quadruped", "variant": "stocky", "height": 0.95, "length": 1.62, "width": 0.74},
    "quadruped_lean_digitigrade": {"family": "quadruped", "plan": "quadruped", "variant": "lean", "height": 1.08, "length": 2.02, "width": 0.52},
    "crawler_bilateral_eight_legged": {"family": "crawler", "plan": "crawler", "variant": "eight", "height": 0.54, "length": 1.25, "width": 1.34},
    "crawler_alien_tripod": {"family": "crawler", "plan": "crawler", "variant": "tripod", "height": 0.68, "length": 1.12, "width": 1.22},
    "hexapod_compact_insect": {"family": "hexapod", "plan": "hexapod", "variant": "compact", "height": 0.48, "length": 1.02, "width": 1.08},
    "hexapod_elongated_insect": {"family": "hexapod", "plan": "hexapod", "variant": "elongated", "height": 0.58, "length": 1.86, "width": 1.30},
    "radial_raised_articulated_walker": {"family": "radial", "plan": "radial", "variant": "raised", "height": 1.02, "length": 1.42, "width": 1.42},
    "serpentine_limbless_articulated": {"family": "serpentine", "plan": "serpentine", "variant": "limbless", "height": 0.55, "length": 2.75, "width": 0.52},
    "serpentine_segmented_paired_legs": {"family": "serpentine", "plan": "serpentine", "variant": "paired", "height": 0.66, "length": 2.45, "width": 1.08},
    "dragger_forelimb_puller": {"family": "dragger", "plan": "dragger", "variant": "pull", "height": 0.82, "length": 1.46, "width": 0.68},
    "dragger_belly_hauler": {"family": "dragger", "plan": "dragger", "variant": "belly", "height": 0.46, "length": 1.72, "width": 0.82},
}

_FRACTIONS = {"core1": 1, "spine3": 3, "limb3": 3, "insect_leg4": 4, "tentacle8": 8, "head1": 1, "appendage1": 1}
_PROFILES = {
    "core1": "core1_body", "spine3": "spine3_axial", "limb3": "limb3_plantigrade",
    "insect_leg4": "insect_leg4_articulated", "tentacle8": "tentacle8_flexible",
    "head1": "head1_neck", "appendage1": "appendage1_terminal",
}
_PROFILE_FRACTIONS = {
    "core1_body": (1.0,), "spine3_axial": (.34, .33, .33),
    "limb3_plantigrade": (.45, .45, .10), "limb3_digitigrade": (.35, .45, .20),
    "insect_leg4_articulated": (.20, .35, .35, .10), "tentacle8_flexible": (.125,) * 8,
    "head1_neck": (1.0,), "appendage1_terminal": (1.0,),
}
_PROFILE_GIRTH = {
    "core1_body": .30, "spine3_axial": .30,
    "limb3_plantigrade": .22, "limb3_digitigrade": .18,
    "insect_leg4_articulated": .15, "tentacle8_flexible": .18,
    "head1_neck": .28, "appendage1_terminal": .16,
}
_JOINTS = {
    "core1_body": ("upper",), "spine3_axial": ("pelvis", "lower", "end"),
    "limb3_plantigrade": ("upper", "lower", "end"), "limb3_digitigrade": ("upper", "lower", "end"),
    "insect_leg4_articulated": ("upper", "femur", "tibia", "end"),
    "tentacle8_flexible": ("seg0", "seg1", "seg2", "seg3", "seg4", "seg5", "seg6", "end"),
    "head1_neck": ("upper",), "appendage1_terminal": ("upper",),
}


def _r(seed: int, key: int) -> float:
    return 0.97 + SplitMix64(seed * 257 + key).below(60_001) / 1_000_000


def _v(x: float, y: float, z: float) -> list[float]:
    return [round(x, 4), round(y, 4), round(z, 4)]


def _reference_radius(segment_length: float, girth_m: float) -> float:
    """Radius of the reference mannequin around one deformation-bone segment."""
    return max(.018, min(segment_length * .32, girth_m * .5))


def _contact_point(kind: str, length: float, fraction: float, girth_m: float) -> list[float]:
    segment_length = length * fraction
    ventral = -_reference_radius(segment_length, girth_m) if kind in {"sliding", "body"} else 0.0
    return [0.0, round(segment_length, 4), round(ventral, 4)]


def _branch(branch_id: str, template: str, parent: str | None, *, origin: list[float], direction: list[float],
            up: list[float], length: float, side: str = "C", attach: int = 0, mirror_of: str = "",
            role: str = "none", phase: float = 0.0, support: float = 0.5, contact: str | None = None,
            parent_joint: str = "root", gait: dict[str, float] | None = None, profile_id: str | None = None,
            required: bool = True, optional_fill_pct: int | None = None) -> dict[str, Any]:
    joints = _FRACTIONS[template]
    profile_id = profile_id or _PROFILES[template]
    length = round(length, 4)
    category = ("core" if role == "core" else "head" if role == "head" else
                "tail" if branch_id == "body" else "appendage" if template in {"tentacle8", "appendage1"} else "limb")
    stance = {
        "limb3_plantigrade": [12.0, -28.0, 16.0],
        "limb3_digitigrade": [14.0, -34.0, 19.0],
        "insect_leg4_articulated": [10.0, -32.0, -38.0, 12.0],
        "tentacle8_flexible": [0.0] * 8,
    }.get(profile_id, [0.0] * joints)
    stance_z = ([10.0, -18.0, 16.0, -14.0, 12.0, -10.0, 8.0, -6.0]
                if profile_id == "tentacle8_flexible" and contact in {"sliding", "body"} else [0.0] * joints)
    record: dict[str, Any] = {
        "branch_id": branch_id, "template": template, "parent_branch": parent, "attach_bone_index": attach,
        "origin_m": origin, "direction": direction, "up": up, "length_m": length,
        "girth_m": round(length * _PROFILE_GIRTH[profile_id], 4),
        "size_class": "L" if template in {"core1", "spine3"} else "M", "side": side,
        "required": required, "accepts": {"categories": [category], "templates": [template]},
        "connector_size_class": None if role == "core" else "M", "binding_profile_id": profile_id,
        "binding_profile_version": "1.0.0", "socket": {"parent_joint": parent_joint, "position_m": origin,
        "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
        "gait": {"role": role, "phase_rad": round(phase, 6), "support_phase": support,
                 "bend_pole_m": _v(origin[0], origin[1] + 0.2, origin[2] - 0.25),
                 "stride_m": round(max(0.12, length * 0.42), 4), "clearance_m": round(max(0.03, length * 0.12), 4),
                  "cadence_hz": 1.5 if role == "locomotor" else 0.0},
        "stance_deg": stance, "stance_z_deg": stance_z,
    }
    if not required or optional_fill_pct is not None:
        record["optional_fill_pct"] = int(100 if optional_fill_pct is None else optional_fill_pct)
    if mirror_of:
        record["mirror_of"] = mirror_of
    if contact:
        indices = (1, 4, joints - 1) if contact in {"sliding", "body"} and joints >= 6 else (joints - 1,)
        record["contacts"] = [
            {"kind": contact, "bone_index": index,
             "local_point_m": _contact_point(contact, length, _PROFILE_FRACTIONS[profile_id][index], record["girth_m"])}
            for index in indices
        ]
    return record


def _core(height: float, length: float, width: float, *, upright: bool = False) -> dict[str, Any]:
    if upright:
        return _branch("core", "spine3", None, origin=_v(0, height * 0.48, 0), direction=[0, 1, 0], up=[0, 0, 1], length=length, role="core")
    return _branch("core", "spine3", None, origin=_v(0, height, -length / 2), direction=[0, 0, 1], up=[0, 1, 0], length=length, role="core")


def _paired_legs(branches: list[dict[str, Any]], *, prefix: str, parent: str, positions: list[tuple[float, float, float]],
                 length: float, template: str, contact: str = "foot", attach: int = 0,
                 phases: tuple[float, float] = (0.0, math.pi), profile_id: str | None = None,
                 direction: Any = None, up: list[float] | None = None) -> list[dict[str, Any]]:
    """Append mirrored leg pairs.  ``direction`` may be a callable ``(sx, index) -> vec``."""
    added: list[dict[str, Any]] = []
    for index, (_, y, z) in enumerate(positions):
        for side, sx, phase in (("L", 1, phases[0]), ("R", -1, phases[1])):
            bid = f"{prefix}_{side}{index}" if len(positions) > 1 else f"{prefix}_{side}"
            leg_direction = (direction(sx, index) if callable(direction) else
                             direction if direction is not None else [sx * 0.25, -1, 0.08])
            record = _branch(bid, template, parent, origin=_v(sx * positions[index][0], y, z),
                             direction=leg_direction, up=list(up or [0, 0, 1]), length=length, side=side,
                             attach=attach, mirror_of=f"{prefix}_L{index}" if side == "R" and len(positions) > 1 else (f"{prefix}_L" if side == "R" else ""),
                             role="locomotor", phase=phase, support=0.58, contact=contact,
                             parent_joint="lower" if attach == 1 else "upper", profile_id=profile_id)
            branches.append(record)
            added.append(record)
    return added


def _insect_leg(leg: dict[str, Any]) -> None:
    """Sprawled insect/arachnid leg bending in its vertical plane: the coxa rises, femur and tibia flex
    downward (profile flexion is bone -X), so the knee sits above the body."""
    leg["up"] = [0, 1, 0]
    leg["stance_deg"] = [45.0, -60.0, -70.0, 10.0]
    leg["_extension_target"] = _INSECT_EXTENSION


def _mammal_leg(leg: dict[str, Any], *, fore: bool = False, fit_hip: bool = True) -> None:
    """Crouch a limb3 leg to the stepping extension and (optionally) size it so the hip keeps its height.

    Fore legs flip their bend reference (``up`` backwards) so the elbow points back while the
    profile's one-way knee flexion (bone -X) is preserved.
    """
    if fore:
        leg["up"] = [0, 0, -1]
    leg["_extension_target"] = _LIMB_EXTENSION
    if fit_hip:
        leg["_hip_height"] = leg["origin_m"][1]


def _ground_by_first_joint(branch: dict[str, Any]) -> None:
    """Swing the first joint (within its limits) until the contact meets the ground, keeping length."""
    lo, hi = _bone_limits(branch["binding_profile_id"])[0]
    def height(angle: float) -> float:
        angles = [angle] + list(branch["stance_deg"][1:])
        return _contact_height(branch, angles, None, branch["stance_z_deg"]) - _contact_clearance(branch["contacts"][0]["kind"])
    # Raising the first joint (toward ``up``) lifts the contact; find the zero crossing.
    if height(lo) > 0 or height(hi) < 0:
        return
    for _ in range(50):
        mid = (lo + hi) / 2
        if height(mid) > 0:
            hi = mid
        else:
            lo = mid
    branch["stance_deg"][0] = round((lo + hi) / 2, 3)


def _fit_leg_to_hip(branch: dict[str, Any], hip_height: float) -> None:
    """Scale the leg so its neutral contact meets the ground with the hip at ``hip_height``.

    The neutral shape (angles) is length-independent, so the drop scales linearly with length.
    """
    contact_y = _contact_height(branch, branch["stance_deg"], None, branch["stance_z_deg"])
    drop = branch["origin_m"][1] - contact_y
    if drop <= 1e-6:
        return
    _refresh_branch_dimensions(branch, (hip_height - _GROUND_CLEARANCE_M) / drop)


def _extension(branch: dict[str, Any], angles: list[float]) -> float:
    """Hip-to-contact distance over chain reach for a candidate stance."""
    fractions = _PROFILE_FRACTIONS[branch["binding_profile_id"]]
    contact = branch["contacts"][0]
    reach = branch["length_m"] * sum(fractions[:int(contact["bone_index"]) + 1])
    point = _contact_point_world(branch, angles, contact, branch["stance_z_deg"])
    return mu.length(mu.sub(point, branch["origin_m"])) / reach


_LIMIT_MARGIN_DEG = 3.0
_BONE_LIMIT_CACHE: dict[str, list[tuple[float, float]]] = {}


def _bone_limits(profile_id: str) -> list[tuple[float, float]]:
    """Per-joint swing limits in bone space (+X toward ``up``); profile flexion is bone -X."""
    if profile_id not in _BONE_LIMIT_CACHE:
        doc = json.loads((paths().data / "binding_profiles" / f"{profile_id}.binding.json").read_text(encoding="utf-8"))
        _BONE_LIMIT_CACHE[profile_id] = [
            (-float(j["limits_deg"]["swing_x"][1]) + _LIMIT_MARGIN_DEG,
             -float(j["limits_deg"]["swing_x"][0]) - _LIMIT_MARGIN_DEG)
            for j in doc["joints"]]
    return _BONE_LIMIT_CACHE[profile_id]


def _scaled_stance(branch: dict[str, Any], base: list[float], k: float) -> list[float]:
    limits = _bone_limits(branch["binding_profile_id"])
    return [round(min(hi, max(lo, a * k)), 3) for a, (lo, hi) in zip(base, limits)]


def _tune_extension(branch: dict[str, Any], target: float) -> None:
    """Scale the authored stance so the neutral contact sits at ``target`` of full reach.

    A nearly straight neutral leg (extension ~0.95) leaves no stroke for stepping;
    runtime foot placement needs compression and extension margin around home.
    """
    base = list(branch["stance_deg"])
    # Scaled angles are clamped to the profile's joint limits; never scale past 150 degrees, where
    # extension stops being monotonic.
    lo, hi = 0.0, min(4.0, 150.0 / max(1e-6, max(abs(a) for a in base)))
    if _extension(branch, _scaled_stance(branch, base, hi)) > target:
        branch["stance_deg"] = _scaled_stance(branch, base, hi)
        return
    for _ in range(40):
        mid = (lo + hi) / 2
        if _extension(branch, _scaled_stance(branch, base, mid)) > target:
            lo = mid
        else:
            hi = mid
    branch["stance_deg"] = _scaled_stance(branch, base, (lo + hi) / 2)


def _biped(a: dict[str, Any], h: float, length: float, width: float) -> tuple[list[dict[str, Any]], list[str], list[str], str]:
    torso_origin_y = h * .48
    torso_length = h * .46
    branches = [_branch("core", "spine3", None, origin=_v(0, torso_origin_y, 0), direction=[0, 1, 0],
                        up=[0, 0, 1], length=torso_length, role="core")]
    leg_len = h * (0.72 if a["variant"] == "plantigrade" else 0.78) * a["limb_scale"]
    leg_profile = "limb3_digitigrade" if a["variant"] == "digitigrade" else "limb3_plantigrade"
    for leg in _paired_legs(branches, prefix="leg", parent="core", positions=[(width * .38, h * .48, 0)],
                            length=leg_len, template="limb3", profile_id=leg_profile):
        _mammal_leg(leg)
    arm_len = h * .42 * a["limb_scale"]
    shoulder_x = min(width * .34, (torso_length * _PROFILE_GIRTH["spine3_axial"] +
                                   arm_len * _PROFILE_GIRTH["limb3_plantigrade"]) * .46)
    for side, sx in (("L", 1), ("R", -1)):
        branches.append(_branch(f"arm_{side}", "limb3", "core",
                                origin=_v(sx * shoulder_x, torso_origin_y + torso_length * .74, 0),
                                direction=[sx * .4, -.8, .1], up=[0, 0, 1], length=arm_len, side=side,
                                attach=2, mirror_of="arm_L" if side == "R" else "", role="manipulator",
                                phase=math.pi if side == "L" else 0, support=0.0, contact="hand", parent_joint="end"))
    branches.append(_branch("head", "head1", "core", origin=_v(0, torso_origin_y + torso_length, 0),
                            direction=[0, .5, 1], up=[0, 1, 0], length=h * .18,
                            role="head", attach=2, parent_joint="end"))
    return branches, ["leg_L", "leg_R"], ["leg_L", "leg_R", "arm_L", "arm_R"], "bilateral"


def _quadruped(a: dict[str, Any], h: float, length: float, width: float) -> tuple[list[dict[str, Any]], list[str], list[str], str]:
    branches = [_core(h, length * .68, width)]
    leg = h * (1.0 if a["variant"] == "stocky" else 1.12) * a["limb_scale"]
    leg_profile = "limb3_digitigrade" if a["variant"] == "lean" else "limb3_plantigrade"
    legs = _paired_legs(branches, prefix="leg", parent="core",
                        positions=[(width * .48, h, length * .25), (width * .48, h, -length * .25)],
                        length=leg, template="limb3", attach=1, profile_id=leg_profile)
    # Lateral-sequence walk (hind L, fore L, hind R, fore R a quarter cycle apart); diagonal trot run.
    walk = {"leg_L1": 0.0, "leg_L0": .25, "leg_R1": .5, "leg_R0": .75}
    trot = {"leg_L0": 0.0, "leg_R1": 0.0, "leg_R0": .5, "leg_L1": .5}
    for leg in legs:
        _mammal_leg(leg, fore=leg["branch_id"].endswith("0"))
        leg["gait"]["phase_rad"] = round(walk[leg["branch_id"]] * 2 * math.pi, 6)
        leg["gait"]["run_phase_rad"] = round(trot[leg["branch_id"]] * 2 * math.pi, 6)
    branches.append(_branch("head", "head1", "core", origin=_v(0, h, length * .38), direction=[0, .05, 1], up=[0, 1, 0], length=length * .18, role="head", attach=2, parent_joint="end"))
    return branches, ["leg_L0", "leg_R0", "leg_L1", "leg_R1"], ["leg_L0", "leg_R0", "leg_L1", "leg_R1"], "bilateral"


def _crawler(a: dict[str, Any], h: float, length: float, width: float) -> tuple[list[dict[str, Any]], list[str], list[str], str]:
    branches = [_branch("core", "core1", None, origin=_v(0, h, -length * .28), direction=[0, 0, 1], up=[0, 1, 0], length=length * .56, role="core")]
    supports: list[str] = []
    if a["variant"] == "tripod":
        angles = (90, 210, 330)
        for i, deg in enumerate(angles):
            rad = math.radians(deg); bid = f"leg_{i}"; supports.append(bid)
            leg = _branch(bid, "insect_leg4", "core", origin=_v(math.sin(rad) * width * .22, h, math.cos(rad) * length * .18), direction=[math.sin(rad), 0.0, math.cos(rad)], up=[0, 1, 0], length=width * .80 * a["limb_scale"], role="locomotor", phase=i * 2 * math.pi / 3, support=.62, contact="foot", parent_joint="upper")
            _insect_leg(leg)
            branches.append(leg)
        symmetry = "radial_3"
    else:
        for i, z in enumerate((-.22, -.07, .08, .23)):
            fan = (-.6, -.2, .2, .6)[i]
            for leg in _paired_legs(branches, prefix=f"leg{i}", parent="core", positions=[(width * .12, h, z * length)],
                                    length=width * .66 * a["limb_scale"], template="insect_leg4", attach=0,
                                    phases=(0 if i % 2 == 0 else math.pi, math.pi if i % 2 == 0 else 0),
                                    direction=lambda sx, _i, fan=fan: [sx * 1.0, 0.0, fan], up=[0, 1, 0]):
                _insect_leg(leg)
            supports.extend((f"leg{i}_L", f"leg{i}_R"))
        symmetry = "bilateral"
    return branches, supports, supports, symmetry


def _hexapod(a: dict[str, Any], h: float, length: float, width: float) -> tuple[list[dict[str, Any]], list[str], list[str], str]:
    branches = [_core(h, length * .72, width)]
    supports: list[str] = []
    for i, z in enumerate((-.22, 0, .22)):
        fan = (-.45, 0.0, .45)[i]
        legs = _paired_legs(branches, prefix=f"leg{i}", parent="core", positions=[(width * .14, h, z * length)],
                            length=width * .75 * a["limb_scale"], template="insect_leg4", attach=1,
                            phases=(0 if i % 2 == 0 else math.pi, math.pi if i % 2 == 0 else 0),
                            direction=lambda sx, _i, fan=fan: [sx * 1.0, 0.0, fan], up=[0, 1, 0])
        for leg in legs:
            # Sprawled insect leg bending in its vertical plane: the coxa rises,
            # femur and tibia flex downward (profile flexion is bone -X), so the
            # knee sits above the body and the tarsus meets the ground steeply.
            leg["stance_deg"] = [45.0, -60.0, -70.0, 10.0]
            leg["_extension_target"] = _INSECT_EXTENSION
        supports.extend((f"leg{i}_L", f"leg{i}_R"))
    return branches, supports, supports, "bilateral"


def _radial(a: dict[str, Any], h: float, length: float, width: float) -> tuple[list[dict[str, Any]], list[str], list[str], str]:
    template = "tentacle8" if a["variant"] == "low" else "insect_leg4"
    branches = [_branch("core", "core1", None, origin=_v(0, h, 0), direction=[0, 1, 0], up=[0, 0, 1], length=h * .5, role="core")]
    count = 8 if a["variant"] == "low" else 6; supports: list[str] = []
    for i in range(count):
        angle = 2 * math.pi * i / count; bid = f"arm_{i}"; supports.append(bid)
        vertical = 0.0 if template == "tentacle8" else -.72
        arm = _branch(bid, template, "core", origin=_v(math.sin(angle) * width * .16, h, math.cos(angle) * length * .16), direction=[math.sin(angle), vertical, math.cos(angle)], up=[0, 1, 0], length=width * .58 * a["limb_scale"], role="locomotor", phase=angle, support=.68, contact="sliding" if template == "tentacle8" else "foot", parent_joint="upper")
        if template == "insect_leg4":
            arm["direction"] = [math.sin(angle), 0.0, math.cos(angle)]
            _insect_leg(arm)
            arm["gait"]["run_phase_rad"] = round((i % 2) * math.pi, 6)
        branches.append(arm)
    return branches, supports, supports, f"radial_{count}"


def _serpentine(a: dict[str, Any], h: float, length: float, width: float) -> tuple[list[dict[str, Any]], list[str], list[str], str]:
    branches = [_branch("core", "head1", None, origin=_v(0, h, length * .43), direction=[0, .1, 1], up=[0, 1, 0], length=length * .16, role="head"),
                _branch("body", "tentacle8", "core", origin=_v(0, h * .28, length * .35), direction=[0, 0, -1], up=[0, 1, 0], length=length * .9, role="locomotor", support=.8, contact="sliding", parent_joint="upper")]
    if a["variant"] == "limbless":
        return branches, ["body"], ["body"], "bilateral"
    # Legged serpentines carry the body clear of the ground on their legs: the hips sit at body height.
    body_y = h * .55
    branches[1]["origin_m"][1] = round(body_y, 4)
    supports: list[str] = []
    for i, z in enumerate((.12, -.12)):
        for leg in _paired_legs(branches, prefix=f"leg{i}", parent="body", positions=[(width * .28, body_y, z * length)], length=h * .75 * a["limb_scale"], template="limb3", attach=3):
            _mammal_leg(leg)
        supports.extend((f"leg{i}_L", f"leg{i}_R"))
    branches[1].pop("contacts")
    return branches, supports, supports, "bilateral"


def _dragger(a: dict[str, Any], h: float, length: float, width: float) -> tuple[list[dict[str, Any]], list[str], list[str], str]:
    """Draggers crawl like a legless zombie: the chest lies on the ground, the head is raised, and the
    arms reach out nearly horizontally, slap down far ahead and haul the sliding body up to the hand
    (runtime "drag" gait). The dragged remains trail flat behind. The puller is long-armed and narrow;
    the belly hauler is broader and heavier with a longer trailing body."""
    pull = a["variant"] == "pull"
    body_length = length * (.55 if pull else .66)
    radius = body_length * _PROFILE_GIRTH["spine3_axial"] * .5
    rear_z = -body_length * .5
    core = _branch("core", "spine3", None, origin=_v(0, radius + _GROUND_CLEARANCE_M, rear_z),
                   direction=[0, 0, 1], up=[0, 1, 0], length=body_length, role="core")
    branches = [core]
    along = body_length * (_PROFILE_FRACTIONS["spine3_axial"][0] + _PROFILE_FRACTIONS["spine3_axial"][1])
    # Shoulders at chest height on the flanks: the arms start level with the torso, not above it.
    shoulder_y = core["origin_m"][1] + radius * .15
    shoulder_z = rear_z + along
    arm_length = length * (.66 if pull else .52) * a["limb_scale"]
    spread = .3 if pull else .55
    for side, sx, phase in (("L", 1, 0.0), ("R", -1, math.pi)):
        arm = _branch(
            f"arm_{side}", "limb3", "core",
            origin=_v(sx * radius * (.95 if pull else 1.1), shoulder_y, shoulder_z),
            direction=[sx * spread, -.12, 1.0], up=[0, 1, 0],
            length=arm_length, side=side, attach=2,
            mirror_of="arm_L" if side == "R" else "", role="locomotor",
            phase=phase, support=.6, contact="hand", parent_joint="end",
        )
        # Mid-pull: the upper arm lifts toward ``up``, the forearm folds down to the ground (profile
        # flexion is bone -X), the hand lies flat. At the far end of the stroke the arm is almost straight.
        arm["stance_deg"] = [30.0, -75.0, 30.0]
        arm["_extension_target"] = .72
        arm["_ground_contact"] = True
        branches.append(arm)
    # Raised head looking forward, carried off the ground by the neck.
    branches.append(_branch("head", "head1", "core", origin=_v(0, core["origin_m"][1] + radius * .5, rear_z + body_length),
                            direction=[0, .55, 1], up=[0, 1, 0], length=body_length * .3,
                            role="head", attach=2, parent_joint="end"))
    branches.append(_branch(
        "belly", "tentacle8", "core",
        origin=_v(0, core["origin_m"][1], rear_z), direction=[0, 0, -1], up=[0, 1, 0],
        length=length * (.45 if pull else .6), role="locomotor", support=.92, contact="body",
        parent_joint="pelvis",
    ))
    support = ["arm_L", "arm_R", "belly"]
    return branches, support, support, "bilateral"


_BUILDERS = {"biped": _biped, "quadruped": _quadruped, "crawler": _crawler, "hexapod": _hexapod, "radial": _radial, "serpentine": _serpentine, "dragger": _dragger}

# Parallel extras series: constants only, never a new _r draw. Not support/locomotor.
_FAMILY_EXTRAS: dict[str, tuple[str, ...]] = {
    "biped": ("extra_arm_pair", "back_tentacle"),
    "quadruped": ("tail", "dorsal"),
    "crawler": ("dorsal_tentacle",),
    "hexapod": ("mandibles",),
    "radial": ("crown",),
    "serpentine": ("second_tail", "fins"),
    "dragger": ("stump_pair",),
}
_NAMED_EXTRAS = {"armed": ("extra_arm_pair",), "finned": ("fins",)}


def _find_branch(branches: list[dict[str, Any]], branch_id: str) -> dict[str, Any]:
    return next(branch for branch in branches if branch["branch_id"] == branch_id)


def _extra(branch_id: str, template: str, parent: str | None, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("required", False)
    kwargs.setdefault("optional_fill_pct", 100)
    kwargs.setdefault("support", 0.0)
    return _branch(branch_id, template, parent, **kwargs)


def _extras_fit(branches: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> bool:
    next_parts = len(branches) + len(incoming)
    next_bones = 1 + sum(_FRACTIONS[branch["template"]] for branch in (*branches, *incoming))
    next_tris = 900 + next_bones * 155
    return next_parts <= 16 and next_bones <= 120 and next_tris <= 30_000


def _rule_extra_arm_pair(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    arm = _find_branch(branches, "arm_L")
    added: list[dict[str, Any]] = []
    for side, sx in (("L", 1), ("R", -1)):
        record = _extra(
            f"arm2_{side}", "limb3", "core",
            origin=_v(sx * abs(arm["origin_m"][0]) * 1.25, arm["origin_m"][1], arm["origin_m"][2] - 0.08),
            direction=[sx * 0.4, -0.8, -0.15], up=[0, 0, 1], length=arm["length_m"],
            side=side, attach=2, mirror_of="arm2_L" if side == "R" else "",
            role="manipulator", parent_joint="end", profile_id=arm["binding_profile_id"],
        )
        record["stance_deg"] = list(arm["stance_deg"])
        record["stance_z_deg"] = list(arm["stance_z_deg"])
        added.append(record)
    return added


def _rule_back_tentacle(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    core = _find_branch(branches, "core")
    return [_extra(
        "back", "tentacle8", "core",
        origin=_v(0, core["origin_m"][1] + core["length_m"] * 0.55, core["origin_m"][2] - 0.08),
        direction=[0, 0.4, -1], up=[0, 1, 0], length=1.10,
        attach=1, role="sway", parent_joint="lower",
    )]


def _rule_tail(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    core = _find_branch(branches, "core")
    return [_extra(
        "tail", "tentacle8", "core",
        origin=_v(0, core["origin_m"][1], core["origin_m"][2] - 0.02),
        direction=[0, 0.25, -1], up=[0, 1, 0], length=1.10,
        attach=0, role="sway", parent_joint="pelvis",
    )]


def _rule_dorsal(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    core = _find_branch(branches, "core")
    return [_extra(
        "dorsal_0", "appendage1", "core",
        origin=_v(0, core["origin_m"][1] + 0.12, core["origin_m"][2] + core["length_m"] * 0.5),
        direction=[0, 1, 0], up=[0, 0, 1], length=0.35,
        attach=1, role="sway", parent_joint="lower",
    )]


def _rule_dorsal_tentacle(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    core = _find_branch(branches, "core")
    return [_extra(
        "dorsal", "tentacle8", "core",
        origin=_v(0, core["origin_m"][1] + 0.08, core["origin_m"][2] + core["length_m"] * 0.5),
        direction=[0, 1, -0.3], up=[0, 0, 1], length=1.10,
        attach=0, role="sway", parent_joint="upper",
    )]


def _rule_mandibles(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    core = _find_branch(branches, "core")
    added: list[dict[str, Any]] = []
    for side, sx in (("L", 1), ("R", -1)):
        added.append(_extra(
            f"mandible_{side}", "appendage1", "core",
            origin=_v(sx * 0.12, core["origin_m"][1] - 0.04, core["origin_m"][2] + core["length_m"]),
            direction=[sx * 0.4, -0.2, 1], up=[0, 1, 0], length=0.35,
            side=side, attach=2, mirror_of="mandible_L" if side == "R" else "",
            role="manipulator", parent_joint="end",
        ))
    return added


def _rule_crown(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    core = _find_branch(branches, "core")
    return [_extra(
        "crown", "appendage1", "core",
        origin=_v(core["origin_m"][0], core["origin_m"][1] + core["length_m"], core["origin_m"][2]),
        direction=[0, 1, 0.2], up=[0, 0, 1], length=0.35,
        attach=-1, role="head", parent_joint="upper",
    )]


def _rule_second_tail(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    body = _find_branch(branches, "body")
    return [_extra(
        "tail_2", "tentacle8", "body",
        origin=_v(body["origin_m"][0] + 0.08, body["origin_m"][1] + 0.04, body["origin_m"][2]),
        direction=[0.4, 0.2, -1], up=[0, 1, 0], length=1.10,
        attach=3, role="sway", parent_joint="seg3",
    )]


def _rule_fins(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    body = _find_branch(branches, "body")
    added: list[dict[str, Any]] = []
    for side, sx in (("L", 1), ("R", -1)):
        added.append(_extra(
            f"fin_{side}", "appendage1", "body",
            origin=_v(sx * 0.16, body["origin_m"][1], body["origin_m"][2] + 0.1),
            direction=[sx, 0.3, -0.4], up=[0, 1, 0], length=0.35,
            side=side, attach=1, mirror_of="fin_L" if side == "R" else "",
            role="sway", parent_joint="seg1",
        ))
    return added


def _rule_stump_pair(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    core = _find_branch(branches, "core")
    radius = core["girth_m"] * 0.5
    added: list[dict[str, Any]] = []
    for side, sx in (("L", 1), ("R", -1)):
        record = _extra(
            f"stump_{side}", "limb3", "core",
            origin=_v(sx * radius * 0.9, core["origin_m"][1] + 0.05, core["origin_m"][2] + 0.04),
            direction=[sx * 0.25, 0.12, -1], up=[0, 1, 0], length=0.40,
            side=side, attach=0, mirror_of="stump_L" if side == "R" else "",
            role="sway", parent_joint="pelvis",
        )
        # Plantigrade lower swing_x is [-5, 135]; +8° stance inverts past that floor.
        record["stance_deg"] = _scaled_stance(record, [-5.0, 8.0, 0.0], 1.0)
        added.append(record)
    return added


_EXTRAS_RULES = {
    "extra_arm_pair": _rule_extra_arm_pair,
    "back_tentacle": _rule_back_tentacle,
    "tail": _rule_tail,
    "dorsal": _rule_dorsal,
    "dorsal_tentacle": _rule_dorsal_tentacle,
    "mandibles": _rule_mandibles,
    "crown": _rule_crown,
    "second_tail": _rule_second_tail,
    "fins": _rule_fins,
    "stump_pair": _rule_stump_pair,
}


def _extras_rule_ids(plan: str, extras: bool | str) -> tuple[str, ...]:
    family_rules = _FAMILY_EXTRAS[plan]
    if extras is True:
        return family_rules
    named = _NAMED_EXTRAS.get(extras if isinstance(extras, str) else "")
    if named is None:
        raise ValueError("extras must be False, True, 'armed', or 'finned'")
    return tuple(rule for rule in named if rule in family_rules)


def _apply_optional_branches(
    a: dict[str, Any],
    branches: list[dict[str, Any]],
    supports: list[str],
    contacts: list[str],
    extras: bool | str,
) -> None:
    """Append budget-gated optional branches. Does not mark them support/locomotor."""
    for rule_id in _extras_rule_ids(a["plan"], extras):
        incoming = _EXTRAS_RULES[rule_id](branches)
        if incoming and _extras_fit(branches, incoming):
            branches.extend(incoming)


def _qmul(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a; bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _socketize(branches: list[dict[str, Any]]) -> None:
    """Express authored attachment positions/orientations in the named parent-joint frame."""
    by_id = {branch["branch_id"]: branch for branch in branches}
    for branch in branches:
        parent_id = branch["parent_branch"]
        if not parent_id:
            bx, by, bz = mu.frame_from_dir_up(branch["direction"], branch["up"])
            branch["socket"] = {"parent_joint": "root", "position_m": branch["origin_m"],
                                "rotation_xyzw": [round(value, 8) for value in mu.quat_from_basis(bx, by, bz)]}
            continue
        parent = by_id[parent_id]
        attach = branch["attach_bone_index"]
        fractions = _PROFILE_FRACTIONS[parent["binding_profile_id"]]
        attach = attach if attach >= 0 else len(fractions) + attach
        joints = _JOINTS[parent["binding_profile_id"]]
        px, py, pz = mu.frame_from_dir_up(parent["direction"], parent["up"])
        joint_world = mu.add(parent["origin_m"], mu.scale(pz, parent["length_m"] * sum(fractions[:attach])))
        delta = mu.sub(branch["origin_m"], joint_world)
        local = [round(mu.dot(delta, axis), 4) for axis in (px, py, pz)]
        parent_q = mu.quat_from_basis(px, py, pz)
        cx, cy, cz = mu.frame_from_dir_up(branch["direction"], branch["up"])
        child_q = mu.quat_from_basis(cx, cy, cz)
        relative = _qmul((-parent_q[0], -parent_q[1], -parent_q[2], parent_q[3]), child_q)
        branch["socket"] = {"parent_joint": joints[attach], "position_m": local,
                            "rotation_xyzw": [round(value, 8) for value in relative]}


def _contact_height(branch: dict[str, Any], angles: list[float], contact: dict[str, Any] | None = None,
                    z_angles: list[float] | None = None) -> float:
    return _contact_point_world(branch, angles, contact, z_angles)[1]


def _contact_point_world(branch: dict[str, Any], angles: list[float], contact: dict[str, Any] | None = None,
                         z_angles: list[float] | None = None) -> tuple[float, float, float]:
    """FK one bone-local contact using Blender +Y as the bone length axis."""
    fractions = _PROFILE_FRACTIONS[branch["binding_profile_id"]]
    if len(angles) != len(fractions):
        raise ValueError("stance angle count must match the binding profile")
    z_angles = z_angles or [0.0] * len(fractions)
    if len(z_angles) != len(fractions):
        raise ValueError("stance Z angle count must match the binding profile")
    contact = contact or branch["contacts"][0]
    target = int(contact["bone_index"])
    if not 0 <= target < len(fractions):
        raise IndexError("contact bone index is outside the binding profile")

    forward = mu.normalize(branch["direction"])
    up = mu.normalize(mu.sub(branch["up"], mu.scale(forward, mu.dot(branch["up"], forward))))
    right = mu.cross(forward, up)
    world_q = mu.quat_from_basis(right, forward, up)
    point = tuple(branch["origin_m"])
    for index, (fraction, x_angle, z_angle) in enumerate(zip(fractions, angles, z_angles, strict=True)):
        x_half = math.radians(x_angle) / 2
        z_half = math.radians(z_angle) / 2
        local_q = _qmul((0.0, 0.0, math.sin(z_half), math.cos(z_half)),
                        (math.sin(x_half), 0.0, 0.0, math.cos(x_half)))
        world_q = _qmul(world_q, local_q)
        if index == target:
            return mu.add(point, mu.quat_rotate(world_q, contact["local_point_m"]))
        point = mu.add(point, mu.quat_rotate(world_q, (0.0, branch["length_m"] * fraction, 0.0)))
    raise AssertionError("unreachable contact")


def _neutral_pose(branches: list[dict[str, Any]], support_branches: list[str]) -> dict[str, Any]:
    rotations = [{"bone_name": "root", "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]}]
    support_heights: list[float] = []
    for branch in branches:
        count = _FRACTIONS[branch["template"]]
        angles = branch["stance_deg"]
        z_angles = branch["stance_z_deg"]
        if len(angles) != count or len(z_angles) != count:
            raise ValueError(f"invalid stance for {branch['branch_id']}")
        for i, (x_angle, z_angle) in enumerate(zip(angles, z_angles, strict=True)):
            x_half = math.radians(x_angle) / 2
            z_half = math.radians(z_angle) / 2
            quat = _qmul((0.0, 0.0, math.sin(z_half), math.cos(z_half)),
                         (math.sin(x_half), 0.0, 0.0, math.cos(x_half)))
            rotations.append({"bone_name": f"{branch['branch_id']}_b{i}",
                              "rotation_xyzw": [round(value, 8) for value in quat]})
        if branch["branch_id"] in support_branches and branch.get("contacts"):
            for contact in branch["contacts"]:
                target_height = _contact_clearance(contact["kind"])
                support_heights.append(target_height - _contact_height(branch, angles, contact, z_angles))
    root_lift = max(support_heights) if support_heights else 0.0
    return {"root_offset_m": [0.0, round(root_lift, 4), 0.0], "rotations": rotations}


def _scale_stance(branches: list[dict[str, Any]], factor: float) -> None:
    for branch in branches:
        branch["stance_deg"] = [round(angle * factor, 3) for angle in branch["stance_deg"]]
        branch["stance_z_deg"] = [round(angle * factor, 3) for angle in branch["stance_z_deg"]]


def _refresh_branch_dimensions(branch: dict[str, Any], factor: float) -> None:
    branch["length_m"] = round(branch["length_m"] * factor, 4)
    profile_id = branch["binding_profile_id"]
    branch["girth_m"] = round(branch["length_m"] * _PROFILE_GIRTH[profile_id], 4)
    branch["gait"]["stride_m"] = round(max(.12, branch["length_m"] * .42), 4)
    branch["gait"]["clearance_m"] = round(max(.03, branch["length_m"] * .12), 4)
    for contact in branch.get("contacts", []):
        index = contact["bone_index"]
        contact["local_point_m"] = _contact_point(contact["kind"], branch["length_m"],
                                                   _PROFILE_FRACTIONS[profile_id][index], branch["girth_m"])


def _apply_horror_modifier(branches: list[dict[str, Any]]) -> None:
    """Apply an explicit, deterministic elongation modifier without changing topology."""
    factors = {"core1": 1.10, "spine3": 1.0, "head1": 1.22, "tentacle8": 1.15,
               "limb3": 1.08, "insect_leg4": 1.08, "appendage1": 1.18}
    for branch in branches:
        _refresh_branch_dimensions(branch, factors[branch["template"]])
        branch["gait"]["bend_pole_m"][2] = round(branch["gait"]["bend_pole_m"][2] - branch["length_m"] * .04, 4)


def _align_distributed_supports(branches: list[dict[str, Any]], support_branches: list[str]) -> None:
    """Align a body surface to ground without sacrificing mixed limb tip clearance."""
    supports = [branch for branch in branches if branch["branch_id"] in support_branches]
    limb_offsets = [
        _contact_clearance(contact["kind"]) - _contact_height(branch, branch["stance_deg"], contact, branch["stance_z_deg"])
        for branch in supports for contact in branch.get("contacts", [])
        if contact["kind"] not in {"sliding", "body"}
    ]
    if not limb_offsets:
        return
    root_lift = max(limb_offsets)
    for branch in supports:
        if not branch.get("contacts") or branch["contacts"][0]["kind"] not in {"sliding", "body"}:
            continue
        residuals = [-(root_lift + _contact_height(branch, branch["stance_deg"], contact, branch["stance_z_deg"]))
                     for contact in branch["contacts"]]
        delta = sum(residuals) / len(residuals)
        branch["origin_m"][1] = round(branch["origin_m"][1] + delta, 4)
        branch["gait"]["bend_pole_m"][1] = round(branch["gait"]["bend_pole_m"][1] + delta, 4)


def build_candidate(archetype: str, preset: str, seed: int = 1, style: str = "anatomical",
                    extras: bool | str = False) -> dict[str, Any]:
    """Build one stable v3 draft candidate from a curated archetype and proportion preset."""
    if archetype not in ARCHETYPES:
        raise KeyError(f"unknown archetype: {archetype}")
    if preset not in PRESETS:
        raise KeyError(f"unknown preset: {preset}")
    if style not in {"anatomical", "horror"}:
        raise ValueError("style must be 'anatomical' or 'horror'")
    if extras not in {False, True, "armed", "finned"}:
        raise ValueError("extras must be False, True, 'armed', or 'finned'")
    if extras and style == "horror":
        raise ValueError("horror+extras is out of scope")
    a = dict(ARCHETYPES[archetype])
    shape = {
        "compact": {"height": .92, "length": .82, "width": 1.12, "limb": .88, "stance": 1.14, "radial": .90},
        "balanced": {"height": 1.0, "length": 1.0, "width": 1.0, "limb": 1.0, "stance": 1.0, "radial": 1.0},
        "elongated": {"height": 1.06, "length": 1.28, "width": .90, "limb": 1.12, "stance": .88, "radial": 1.16},
    }[preset]
    a["limb_scale"] = shape["limb"]
    h = a["height"] * shape["height"] * _r(seed, 1)
    if a["plan"] == "radial":
        diameter = a["length"] * shape["radial"] * _r(seed, 2)
        length = width = diameter
    else:
        length = a["length"] * shape["length"] * _r(seed, 2)
        width = a["width"] * shape["width"] * _r(seed, 3)
    branches, supports, contacts, symmetry = _BUILDERS[a["plan"]](a, h, length, width)
    if extras:
        _apply_optional_branches(a, branches, supports, contacts, extras)
    _scale_stance(branches, shape["stance"])
    if style == "horror":
        _apply_horror_modifier(branches)
    for branch in branches:
        target = branch.pop("_extension_target", None)
        hip = branch.pop("_hip_height", None)
        ground = branch.pop("_ground_contact", False)
        if target is not None:
            # Compact presets crouch lower, elongated ones stand taller.
            _tune_extension(branch, target - (shape["stance"] - 1.0) * .25)
        if ground:
            _ground_by_first_joint(branch)
        if hip is not None:
            # Presets keep their limb proportion: longer-limbed presets stand taller on the same torso.
            # Grounded torsos (draggers) never rise: their arms only reach further.
            grounded = a["plan"] == "dragger"
            _fit_leg_to_hip(branch, hip if grounded else hip * shape["limb"])
    _align_distributed_supports(branches, supports)
    _socketize(branches)
    bone_count = 1 + sum(_FRACTIONS[b["template"]] for b in branches)
    symmetry_data = {"kind": "radial", "ring_count": int(symmetry.removeprefix("radial_"))} if symmetry.startswith("radial_") else {"kind": symmetry}
    anatomy = {"archetype_id": archetype, "body_plan": a["plan"], "style": style, "support_branches": supports, "contact_branches": contacts,
               "symmetry": symmetry_data, "landmarks": {"pelvis": "core", "shoulder": "core", "neck": "head" if any(b["branch_id"] == "head" for b in branches) else "core"},
               "silhouette": {"height_m": round(h, 4), "length_m": round(length, 4), "width_m": round(width, 4)},
               "budgets": {"bones": bone_count, "parts": len(branches), "triangles": min(30_000, 900 + bone_count * 155)}}
    style_suffix = "" if style == "anatomical" else "_horror"
    extras_suffix = "" if extras is False else "_extras" if extras is True else f"_{extras}"
    locomotion = {"biped": "biped", "quadruped": "quadruped", "crawler": "crawl", "hexapod": "crawl",
                  "radial": "crawl", "serpentine": "slither", "dragger": "drag"}[a["plan"]]
    return {"schema_version": "3.0.0", "skeleton_id": f"{archetype}_{preset}{style_suffix}{extras_suffix}_v3", "family": a["family"], "locomotion_hint": locomotion, "status": "draft",
            "symmetry_pct": 100 if symmetry == "bilateral" else 92, "branches": branches,
            "neutral_pose": _neutral_pose(branches, supports), "anatomy": anatomy, "provenance": {"generator": "cc-gen-3", "seed": seed, "preset": preset}}


def generate_all(seed: int = 1, style: str = "anatomical") -> list[dict[str, Any]]:
    """Return all 13 curated archetypes in each compact/balanced/elongated preset."""
    return sorted((build_candidate(archetype, preset, seed=seed, style=style)
                   for archetype in ARCHETYPES for preset in PRESETS), key=lambda candidate: candidate["skeleton_id"])


def generate_extras(seed: int = 1) -> list[dict[str, Any]]:
    """Return 41 anatomical extras drafts: 13×3 *_extras_v3 plus armed and finned named variants."""
    extras = [build_candidate(archetype, preset, seed=seed, extras=True)
              for archetype in ARCHETYPES for preset in PRESETS]
    extras.append(build_candidate("biped_plantigrade_humanoid", "balanced", seed=seed, extras="armed"))
    extras.append(build_candidate("serpentine_limbless_articulated", "balanced", seed=seed, extras="finned"))
    return sorted(extras, key=lambda candidate: candidate["skeleton_id"])


def write_profiles(directory: Path) -> list[Path]:
    """Copy the immutable profile sources needed by curated candidates to *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    source_dir = paths().data / "binding_profiles"
    referenced = {branch["binding_profile_id"] for candidate in generate_all() for branch in candidate["branches"]}
    written: list[Path] = []
    for profile_id in sorted(referenced):
        source = source_dir / f"{profile_id}.binding.json"
        if not source.exists():
            raise FileNotFoundError(f"missing binding profile source: {source}")
        destination = directory / source.name
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        written.append(destination)
    return written


def write_candidates(directory: Path, seed: int = 1, style: str = "anatomical") -> list[Path]:
    """Write the complete deterministic draft candidate set for review."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for candidate in generate_all(seed=seed, style=style):
        path = directory / f"{candidate['skeleton_id']}.skeleton.json"
        path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8", newline="\n")
        written.append(path)
    return written
