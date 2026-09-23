"""Versioned semantic attack plans for the curated v3 archetypes.

An attack profile names its effector and trajectory before Blender evaluates a
pose.  :func:`resolve_attack` only resolves structural facts (bone name, bind
length, and authored contacts); it never inspects neutral or animated poses.
Targets are offsets from the neutral effector anchor in catalog coordinates
(metres, +Y up, +Z forward).  This keeps the authored intent reviewable while
letting Blender choose temporary IK or analytic chain rotations.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from critter_crafter import mathutil as mu


ACTION_PLAN_VERSION = "1.0.0"


class AttackPlanError(ValueError):
    """A semantic action cannot be resolved against the supplied skeleton."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AttackProfile:
    """Immutable authored action values; normalized vectors scale by branch length."""

    attack_id: str
    solver: str
    branch_id: str
    bone_index: int
    local_point_n: tuple[float, float, float]
    windup_offset_n: tuple[float, float, float]
    impact_offset_n: tuple[float, float, float]
    timing: tuple[float, float, float, float]
    max_reach_n: float
    minimum_displacement_n: float
    release_contact_indices: tuple[int, ...]
    minimum_preserved: int
    solver_bone_count: int


def _profile(
    attack_id: str,
    solver: str,
    branch_id: str,
    bone_index: int,
    windup: tuple[float, float, float],
    impact: tuple[float, float, float],
    timing: tuple[float, float, float, float],
    *,
    release: tuple[int, ...] = (),
    minimum_preserved: int,
    solver_bone_count: int | None = None,
    max_reach_n: float = .30,
    minimum_displacement_n: float = .07,
) -> AttackProfile:
    return AttackProfile(
        attack_id=attack_id,
        solver=solver,
        branch_id=branch_id,
        bone_index=bone_index,
        local_point_n=(0.0, 1.0, 0.0),
        windup_offset_n=windup,
        impact_offset_n=impact,
        timing=timing,
        max_reach_n=max_reach_n,
        minimum_displacement_n=minimum_displacement_n,
        release_contact_indices=release,
        minimum_preserved=minimum_preserved,
        solver_bone_count=solver_bone_count or bone_index + 1,
    )


# Each entry names a deliberate anatomical effector.  Multi-legged creatures
# lift only one forward branch; the limbless tail releases only its terminal
# body contact.  The remaining declared contacts stay planted throughout.
ATTACK_PROFILES: Mapping[str, AttackProfile] = MappingProxyType({
    "biped_plantigrade_humanoid": _profile(
        "straight_punch", "temporary_ik", "arm_R", 2,
        (.04, .03, -.09), (-.03, .04, .20), (.28, .50, .62, 1.0),
        minimum_preserved=2, max_reach_n=.26, minimum_displacement_n=.10,
    ),
    "biped_digitigrade_creature": _profile(
        "raking_claw", "temporary_ik", "arm_R", 2,
        (.07, .05, -.08), (.12, -.03, .17), (.22, .43, .55, 1.0),
        minimum_preserved=2, max_reach_n=.25, minimum_displacement_n=.11,
    ),
    "quadruped_stocky_plantigrade": _profile(
        "driving_bite", "analytic_chain", "head", 0,
        (0.0, .10, -.012), (0.0, -.22, -.03), (.34, .58, .70, 1.0),
        minimum_preserved=4, solver_bone_count=1, max_reach_n=.25, minimum_displacement_n=.12,
    ),
    "quadruped_lean_digitigrade": _profile(
        "snap_bite", "analytic_chain", "head", 0,
        (.03, .09, -.01), (-.04, -.20, -.025), (.24, .45, .56, 1.0),
        minimum_preserved=4, solver_bone_count=1, max_reach_n=.24, minimum_displacement_n=.12,
    ),
    "crawler_bilateral_eight_legged": _profile(
        "foreleg_spear", "temporary_ik", "leg3_L", 3,
        (-.02, .04, -.08), (.03, .09, .17), (.25, .47, .59, 1.0),
        release=(0,), minimum_preserved=7, max_reach_n=.24, minimum_displacement_n=.11,
    ),
    "crawler_alien_tripod": _profile(
        "tripod_hook", "temporary_ik", "leg_2", 3,
        (.06, .04, -.07), (-.08, .10, .15), (.32, .55, .67, 1.0),
        release=(0,), minimum_preserved=2, max_reach_n=.25, minimum_displacement_n=.10,
    ),
    "hexapod_compact_insect": _profile(
        "foreleg_jab", "temporary_ik", "leg2_L", 3,
        (-.02, .05, -.07), (.02, .10, .16), (.20, .39, .50, 1.0),
        release=(0,), minimum_preserved=5, max_reach_n=.23, minimum_displacement_n=.11,
    ),
    "hexapod_elongated_insect": _profile(
        "foreleg_lance", "temporary_ik", "leg2_L", 3,
        (-.03, .04, -.10), (.025, .07, .19), (.29, .52, .64, 1.0),
        release=(0,), minimum_preserved=5, max_reach_n=.24, minimum_displacement_n=.12,
    ),
    "radial_raised_articulated_walker": _profile(
        "radial_stab", "temporary_ik", "arm_0", 3,
        (.04, .03, -.08), (-.04, .08, .18), (.27, .49, .60, 1.0),
        release=(0,), minimum_preserved=5, max_reach_n=.23, minimum_displacement_n=.11,
    ),
    "serpentine_limbless_articulated": _profile(
        "tail_whip", "analytic_chain", "body", 7,
        # Leave a small displacement margin for the joint-limited baked solve.
        (-.06, .20, .063), (.0601, .19, .06), (.38, .61, .72, 1.0),
        release=(2,), minimum_preserved=2, solver_bone_count=3,
        max_reach_n=.22, minimum_displacement_n=.12,
    ),
    "serpentine_segmented_paired_legs": _profile(
        "segmented_tail_sweep", "analytic_chain", "body", 7,
        (.06, .19, .06), (-.06, .18, .06), (.35, .58, .70, 1.0),
        minimum_preserved=4, solver_bone_count=3, max_reach_n=.21, minimum_displacement_n=.11,
    ),
    "dragger_forelimb_puller": _profile(
        "puller_hammer", "temporary_ik", "arm_L", 2,
        (.04, .08, -.09), (-.04, .12, .18), (.31, .54, .67, 1.0),
        release=(0,), minimum_preserved=4, max_reach_n=.24, minimum_displacement_n=.12,
    ),
    "dragger_belly_hauler": _profile(
        "hauler_shove", "temporary_ik", "arm_L", 2,
        # The hauler's low, compact arm drives upward and forward without
        # asking the three-bone chain to extend past its physical length.
        (-.03, .05, -.07), (.04, .09, .11), (.36, .60, .73, 1.0),
        release=(0,), minimum_preserved=4, max_reach_n=.22, minimum_displacement_n=.10,
    ),
})


def _r6(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if rounded == -0.0 else rounded


def _scaled(vector: Sequence[float], length: float) -> list[float]:
    return [_r6(float(value) * length) for value in vector]


def _length(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(value) ** 2 for value in vector))


def _bone_length(bone: Mapping[str, Any]) -> float:
    head = bone.get("head_m", [])
    tail = bone.get("tail_m", [])
    if len(head) != 3 or len(tail) != 3:
        raise AttackPlanError("CC_ACTION_EFFECTOR", f"bone {bone.get('name', '<unknown>')} has no bind endpoints")
    length = math.dist(tuple(float(value) for value in head), tuple(float(value) for value in tail))
    if not math.isfinite(length) or length <= 0:
        raise AttackPlanError("CC_ACTION_EFFECTOR", f"bone {bone.get('name', '<unknown>')} has invalid bind length")
    return length


def _qmul(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float, float]:
    ax, ay, az, aw = left
    bx, by, bz, bw = right
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _qinverse(value: Sequence[float]) -> tuple[float, float, float, float]:
    norm2 = sum(float(component) ** 2 for component in value)
    if norm2 <= 1e-12:
        raise AttackPlanError("CC_ACTION_NEUTRAL", "zero-length neutral quaternion")
    return (-value[0] / norm2, -value[1] / norm2, -value[2] / norm2, value[3] / norm2)


def _rest_rotation(bone: Mapping[str, Any]) -> tuple[float, float, float, float]:
    head = tuple(float(value) for value in bone["head_m"])
    tail = tuple(float(value) for value in bone["tail_m"])
    up = tuple(float(value) for value in bone["up_m"])
    y_axis = mu.normalize(mu.sub(tail, head))
    z_axis = mu.normalize(mu.sub(up, mu.scale(y_axis, mu.dot(up, y_axis))))
    x_axis = mu.cross(y_axis, z_axis)
    return mu.quat_from_basis(x_axis, y_axis, z_axis)


def _neutral_geometry(skeleton: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Evaluate authored neutral FK from source transforms, without Blender samples."""
    rotations = {
        str(item["bone_name"]): tuple(float(value) for value in item["rotation_xyzw"])
        for item in skeleton.get("neutral_pose", {}).get("rotations", [])
    }
    root_offset = tuple(float(value) for value in skeleton.get("neutral_pose", {}).get("root_offset_m", [0, 0, 0]))
    bones = {str(bone["name"]): bone for bone in skeleton.get("bones", [])}
    result: dict[str, dict[str, Any]] = {}
    for bone in skeleton.get("bones", []):
        name = str(bone["name"])
        rest_rotation = _rest_rotation(bone)
        delta = rotations.get(name, (0.0, 0.0, 0.0, 1.0))
        parent_name = str(bone.get("parent", ""))
        rest_head = tuple(float(value) for value in bone["head_m"])
        if parent_name:
            parent = bones.get(parent_name)
            parent_pose = result.get(parent_name)
            if parent is None or parent_pose is None:
                raise AttackPlanError("CC_ACTION_NEUTRAL", f"{name}: parent {parent_name} must precede child")
            parent_rest_head = tuple(float(value) for value in parent["head_m"])
            parent_rest_rotation = _rest_rotation(parent)
            relative_head = mu.quat_rotate(
                _qinverse(parent_rest_rotation),
                mu.sub(rest_head, parent_rest_head),
            )
            posed_head = mu.add(parent_pose["head_m"], mu.quat_rotate(parent_pose["rotation_xyzw"], relative_head))
            posed_rotation = _qmul(
                _qmul(parent_pose["rotation_xyzw"], _qinverse(parent_rest_rotation)),
                _qmul(rest_rotation, delta),
            )
        else:
            posed_head = mu.add(rest_head, root_offset)
            posed_rotation = _qmul(rest_rotation, delta)
        posed_tail = mu.add(posed_head, mu.quat_rotate(posed_rotation, (0.0, _bone_length(bone), 0.0)))
        result[name] = {
            "head_m": posed_head,
            "tail_m": posed_tail,
            "rotation_xyzw": posed_rotation,
        }
    return result


def _support_contacts(skeleton: Mapping[str, Any]) -> list[str]:
    support = set(skeleton.get("anatomy", {}).get("support_branches", []))
    return [
        f"{branch.get('branch_id')}:{index}"
        for branch in skeleton.get("branches", [])
        if branch.get("branch_id") in support
        for index, _contact in enumerate(branch.get("contacts", []))
    ]


def _sphere_project(anchor: Sequence[float], target: Sequence[float], radius: float) -> tuple[float, float, float]:
    direction = mu.sub(target, anchor)
    distance = mu.length(direction)
    if not math.isfinite(distance) or distance <= 1e-12:
        raise AttackPlanError("CC_ACTION_REACH", "one-bone target cannot coincide with its solver anchor")
    return mu.add(anchor, mu.scale(direction, radius / distance))


def resolve_attack(skeleton: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve one authored profile to concrete metres and skeleton bone names."""
    archetype_id = str(skeleton.get("anatomy", {}).get("archetype_id", ""))
    profile = ATTACK_PROFILES.get(archetype_id)
    if profile is None:
        raise AttackPlanError("CC_ACTION_ARCHETYPE", f"no attack profile for {archetype_id or '<missing>'}")

    branch = next(
        (item for item in skeleton.get("branches", []) if item.get("branch_id") == profile.branch_id),
        None,
    )
    if branch is None:
        raise AttackPlanError("CC_ACTION_EFFECTOR", f"{archetype_id}: missing branch {profile.branch_id}")
    names = list(branch.get("bone_names", []))
    if not 0 <= profile.bone_index < len(names):
        raise AttackPlanError(
            "CC_ACTION_EFFECTOR",
            f"{archetype_id}.{profile.branch_id}: missing bone index {profile.bone_index}",
        )
    bone_name = str(names[profile.bone_index])
    bone = next((item for item in skeleton.get("bones", []) if item.get("name") == bone_name), None)
    if bone is None:
        raise AttackPlanError("CC_ACTION_EFFECTOR", f"{archetype_id}: missing bone {bone_name}")

    branch_length = float(branch.get("length_m", 0.0))
    if not math.isfinite(branch_length) or branch_length <= 0:
        raise AttackPlanError("CC_ACTION_EFFECTOR", f"{archetype_id}.{profile.branch_id}: invalid branch length")
    local_point = _scaled(profile.local_point_n, _bone_length(bone))
    authored_windup = _scaled(profile.windup_offset_n, branch_length)
    authored_impact = _scaled(profile.impact_offset_n, branch_length)
    max_reach = _r6(profile.max_reach_n * branch_length)
    if max(_length(authored_windup), _length(authored_impact)) > max_reach + 1e-6:
        raise AttackPlanError("CC_ACTION_REACH", f"{archetype_id}: declared target exceeds reach budget")

    contacts = list(branch.get("contacts", []))
    released: list[str] = []
    for index in profile.release_contact_indices:
        if not 0 <= index < len(contacts):
            raise AttackPlanError(
                "CC_ACTION_SUPPORT",
                f"{archetype_id}.{profile.branch_id}: missing release contact {index}",
            )
        released.append(f"{profile.branch_id}:{index}")
    support_contacts = _support_contacts(skeleton)
    unknown_release = set(released) - set(support_contacts)
    if unknown_release:
        raise AttackPlanError("CC_ACTION_SUPPORT", f"{archetype_id}: release is not a support contact")
    preserved = [contact_id for contact_id in support_contacts if contact_id not in set(released)]
    if len(preserved) < profile.minimum_preserved:
        raise AttackPlanError(
            "CC_ACTION_SUPPORT",
            f"{archetype_id}: preserves {len(preserved)}, requires {profile.minimum_preserved}",
        )
    preserved_branches = list(dict.fromkeys(contact_id.rsplit(":", 1)[0] for contact_id in preserved))
    windup_end, impact_phase, recovery_start, recovery_end = profile.timing

    source_contact_ids = [f"{profile.branch_id}:{index}" for index, _ in enumerate(contacts)]
    tolerance = _r6(max(.008, min(.02, branch_length * .02)))
    chain_start = profile.bone_index + 1 - profile.solver_bone_count
    if chain_start < 0:
        raise AttackPlanError("CC_ACTION_REACH", f"{archetype_id}: solver chain exceeds effector branch")
    solver_names = names[chain_start:profile.bone_index + 1]
    neutral = _neutral_geometry(skeleton)
    solver_anchor = neutral[solver_names[0]]["head_m"]
    effector_pose = neutral[bone_name]
    neutral_effector = mu.add(
        effector_pose["head_m"],
        mu.quat_rotate(effector_pose["rotation_xyzw"], local_point),
    )
    chain_length = sum(_bone_length(next(item for item in skeleton["bones"] if item["name"] == name)) for name in solver_names)
    authored_targets = {
        "windup_target_m": mu.add(neutral_effector, authored_windup),
        "impact_target_m": mu.add(neutral_effector, authored_impact),
        "recovery_target_m": neutral_effector,
    }
    targets = dict(authored_targets)
    projection = "none"
    if len(solver_names) == 1:
        authored_errors = {
            target_name: abs(math.dist(solver_anchor, target) - chain_length)
            for target_name, target in authored_targets.items()
        }
        if any(error > tolerance for error in authored_errors.values()):
            worst = max(authored_errors.values())
            raise AttackPlanError(
                "CC_ACTION_REACH",
                f"{archetype_id}: authored one-bone target misses its reach sphere by {worst:.6f}m",
            )
        targets = {
            target_name: _sphere_project(solver_anchor, target, chain_length)
            for target_name, target in authored_targets.items()
        }
        projection = "one_bone_sphere"
    windup = mu.r6v(mu.sub(targets["windup_target_m"], neutral_effector))
    impact = mu.r6v(mu.sub(targets["impact_target_m"], neutral_effector))
    if max(_length(windup), _length(impact)) > max_reach + 1e-6:
        raise AttackPlanError("CC_ACTION_REACH", f"{archetype_id}: resolved target exceeds reach budget")
    reach_errors: dict[str, float] = {}
    for target_name, target in targets.items():
        radius = math.dist(solver_anchor, target)
        excess = max(0.0, radius - chain_length)
        if excess > 1e-6:
            raise AttackPlanError(
                "CC_ACTION_REACH",
                f"{archetype_id}.{target_name}: target stretches chain by {excess:.6f}m",
            )
        reach_errors[target_name] = abs(radius - chain_length) if len(solver_names) == 1 else excess
    return {
        "version": ACTION_PLAN_VERSION,
        "archetype_id": archetype_id,
        "attack_id": profile.attack_id,
        "solver": profile.solver,
        "effector": {
            "branch_id": profile.branch_id,
            "bone_index": profile.bone_index,
            "bone_name": bone_name,
            "local_point_m": local_point,
            "space": "bone_local",
            "source_contact_ids": source_contact_ids,
        },
        "timing": {
            "windup_end": windup_end,
            "impact": impact_phase,
            "recovery_start": recovery_start,
            "recovery_end": recovery_end,
        },
        "trajectory": {
            "space": "catalog",
            "projection": projection,
            "neutral_effector_m": mu.r6v(neutral_effector),
            "authored_windup_offset_m": authored_windup,
            "authored_impact_offset_m": authored_impact,
            "authored_windup_target_m": mu.r6v(authored_targets["windup_target_m"]),
            "authored_impact_target_m": mu.r6v(authored_targets["impact_target_m"]),
            "windup_offset_m": windup,
            "impact_offset_m": impact,
            "recovery_offset_m": [0.0, 0.0, 0.0],
            "windup_target_m": mu.r6v(targets["windup_target_m"]),
            "impact_target_m": mu.r6v(targets["impact_target_m"]),
            "recovery_target_m": mu.r6v(targets["recovery_target_m"]),
            "max_reach_m": max_reach,
        },
        "solver_chain": {
            "bone_names": solver_names,
            "anchor_neutral_m": mu.r6v(solver_anchor),
            "length_m": _r6(chain_length),
            "nonstretch": True,
        },
        "support_release": {
            "policy": "contact_granular",
            "contact_ids": released,
            "start": 0.0,
            "end": 1.0,
            "active_condition": "0 < global_phase < 1",
            "preserved_contact_ids": preserved,
            "preserved_branch_ids": preserved_branches,
            "minimum_preserved": profile.minimum_preserved,
        },
        "qa": {
            "minimum_displacement_m": _r6(profile.minimum_displacement_n * branch_length),
            "target_tolerance_m": tolerance,
            "displacement_reference": "attack_start_effector_world",
            "target_reference": "declared_neutral_effector_anchor",
        },
    }


def _smoothstep(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


def _mix(left: Sequence[float], right: Sequence[float], amount: float) -> list[float]:
    amount = _smoothstep(amount)
    return [_r6(float(a) + (float(b) - float(a)) * amount) for a, b in zip(left, right, strict=True)]


def _project_sample_offset(plan: Mapping[str, Any], offset: Sequence[float]) -> list[float]:
    if plan["trajectory"].get("projection") != "one_bone_sphere":
        return list(offset)
    anchor = plan["solver_chain"]["anchor_neutral_m"]
    neutral = plan["trajectory"]["neutral_effector_m"]
    target = mu.add(neutral, offset)
    projected = _sphere_project(anchor, target, float(plan["solver_chain"]["length_m"]))
    return mu.r6v(mu.sub(projected, neutral))


def action_global_phase(plan: Mapping[str, Any], clip_name: str, clip_phase: float) -> float:
    """Map telegraph/attack clip-local time onto one continuous authored action."""
    clip_phase = float(clip_phase)
    if not math.isfinite(clip_phase) or not 0.0 <= clip_phase <= 1.0:
        raise AttackPlanError("CC_ACTION_PHASE", f"clip phase must be within [0,1], got {clip_phase}")
    windup_end = float(plan["timing"]["windup_end"])
    if clip_name == "telegraph":
        return _r6(clip_phase * windup_end)
    if clip_name == "attack":
        return _r6(windup_end + clip_phase * (1.0 - windup_end))
    raise AttackPlanError("CC_ACTION_CLIP", f"semantic attack has no {clip_name!r} clip")


def attack_target_at(plan: Mapping[str, Any], phase: float) -> dict[str, Any]:
    """Sample the declared attack trajectory without consulting an evaluated pose."""
    phase = float(phase)
    if not math.isfinite(phase) or not 0.0 <= phase <= 1.0:
        raise AttackPlanError("CC_ACTION_PHASE", f"phase must be within [0,1], got {phase}")
    timing = plan["timing"]
    trajectory = plan["trajectory"]
    zero = [0.0, 0.0, 0.0]
    windup = trajectory["windup_offset_m"]
    impact = trajectory["impact_offset_m"]
    if phase <= timing["windup_end"]:
        denominator = timing["windup_end"] or 1.0
        target = _mix(zero, windup, phase / denominator)
        stage = "windup"
    elif phase <= timing["impact"]:
        span = timing["impact"] - timing["windup_end"]
        target = _mix(windup, impact, (phase - timing["windup_end"]) / span)
        stage = "strike"
    elif phase <= timing["recovery_start"]:
        target = list(impact)
        stage = "impact"
    else:
        span = timing["recovery_end"] - timing["recovery_start"]
        target = _mix(impact, zero, (phase - timing["recovery_start"]) / span)
        stage = "recovery" if phase < timing["recovery_end"] else "settled"
    target = _project_sample_offset(plan, target)
    release = plan["support_release"]
    released = list(release["contact_ids"]) if release["start"] < phase < release["end"] else []
    return {
        "stage": stage,
        "target_offset_m": target,
        "release_contact_ids": released,
    }


__all__ = [
    "ACTION_PLAN_VERSION",
    "ATTACK_PROFILES",
    "AttackPlanError",
    "AttackProfile",
    "action_global_phase",
    "attack_target_at",
    "resolve_attack",
]
