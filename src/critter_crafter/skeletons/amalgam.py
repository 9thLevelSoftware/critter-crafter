"""Seeded amalgam skeletons: fused bodies with limbs sprouting anywhere, a few of them useful.

Each seed grows one creature. A fat blob body gets one to three heads set off-axis, a handful of *working*
limbs of mixed kinds that carry or haul it, one striker, and a scatter of vestigial limbs that only wave.
The movement mode is drawn from the seed:

* ``hauled``  - the body lies on the ground and 1-3 arms/legs pull and push it (the dragger's haul);
* ``walker``  - the body is held up on 2-4 mismatched legs (mammal, insect, or an arm used as a leg);
* ``slither`` - the body slides on one trailing tentacle.

Every decision has its own random stream, so changing how limbs are scattered never reshuffles the mode.
Working limbs are grounded one by one (a leg that hovers would make the whole creature hover), and each
skeleton is checked end to end by :func:`validate_amalgam` before its seed may be committed.
"""

from __future__ import annotations

import math
from typing import Any

from .. import mathutil as mu
from ..recipes.rng import SplitMix64
from . import archetypes as arch
from . import motion as motion_planner

MODES = ("hauled", "walker", "slither")
_MODE_WEIGHTS = (40, 35, 25)
# Seeds whose skeletons are committed (`critter skeleton vary`). Chosen from `critter skeleton amalgam-sweep`.
AMALGAM_SEEDS: tuple[int, ...] = (
    4, 5, 9, 13, 14,        # hauled
    8, 24, 29, 37, 39,      # walker
    6, 17, 18, 28, 34,      # slither
    40, 43, 47, 54, 58, 59,   # heads on necks, in clusters and on limb tips (HEADY_SEEDS)
)

# Seeds that grow heads on necks, in clusters, or on the tips of limbs (see `_necked_heads`). They are separate from
# the committed seeds above on purpose: a seed absent from this table grows exactly what it always did, so the
# approved amalgams regenerate byte for byte.
HEAD_GROWTH_NECKED, HEAD_GROWTH_CLUSTER, HEAD_GROWTH_LIMB_TIP = "necked", "cluster", "limb_tip"
HEAD_GROWTHS = (HEAD_GROWTH_NECKED, HEAD_GROWTH_CLUSTER, HEAD_GROWTH_LIMB_TIP)
HEADY_SEEDS: dict[int, str] = {
    43: HEAD_GROWTH_NECKED, 47: HEAD_GROWTH_NECKED,
    54: HEAD_GROWTH_CLUSTER, 59: HEAD_GROWTH_CLUSTER,
    40: HEAD_GROWTH_LIMB_TIP, 58: HEAD_GROWTH_LIMB_TIP,
}

# A hauling arm ends in a rigid hand branch on its own binding profile, so only a part fitted to it (a giant splayed
# hand) fills it and no other branch accepts that part. The arm keeps its own contact at the wrist; the hand is set
# HAND_LIFT_M above it so the palm's underside rests at the contact's height (the bake's IK aims the bone tail, so
# the contact itself can't move).
HAND_PROFILE = "hand1_rigid"
HAND_LENGTH_M = .7                 # one nominal size: a hand part fits within 80-125% of its length
HAND_LIFT_M = .12                  # how far the hand's axis rides above the wrist at the neutral pose (half its thickness)

MAX_BRANCHES = 14
# A walker's legs are tall and slim next to its body: this scales the profile's girth ratio.
WALKER_LEG_GIRTH = .6
_GROUND_TOLERANCE_M = .002


class AmalgamError(ValueError):
    """The seed grew something that can't stand, haul or be checked; try another seed."""


def _stream(seed: int, index: int) -> SplitMix64:
    return SplitMix64(seed * 7919 + index)


def _unit(rng: SplitMix64) -> float:
    return rng.below(10_000) / 10_000


def _between(rng: SplitMix64, low: float, high: float) -> float:
    return low + (high - low) * _unit(rng)


def _yaw(vector: list[float], angle: float) -> list[float]:
    """Turn a vector about +Y so that +Z points toward (sin angle, cos angle)."""
    c, s = math.cos(angle), math.sin(angle)
    return [vector[0] * c + vector[2] * s, vector[1], -vector[0] * s + vector[2] * c]


def mode_for_seed(seed: int) -> str:
    roll = _stream(seed, 1).below(100)
    return MODES[0] if roll < _MODE_WEIGHTS[0] else MODES[1] if roll < _MODE_WEIGHTS[0] + _MODE_WEIGHTS[1] else MODES[2]


def amalgam_id(seed: int, mode: str | None = None) -> str:
    return f"amalgam_{mode or mode_for_seed(seed)}_s{seed:04d}_v3"


class _Body:
    """The blob: a fat capsule lying along +Z. ``point`` samples its surface (t along, theta from up toward +X)."""

    def __init__(self, length: float, radius: float, center_y: float, on_ground: bool):
        self.length, self.radius, self.y, self.on_ground = length, radius, center_y, on_ground
        self.rear_z = -length / 2

    def z(self, t: float) -> float:
        return self.rear_z + t * self.length

    def point(self, t: float, theta: float, inset: float = .96) -> tuple[list[float], list[float]]:
        normal = [math.sin(theta), math.cos(theta), 0.0]
        return ([round(self.radius * inset * normal[0], 4), round(self.y + self.radius * inset * normal[1], 4),
                 round(self.z(t), 4)], normal)


def _side(x: float) -> str:
    return "C" if abs(x) < .05 else "L" if x > 0 else "R"


def _up_for(direction: list[float]) -> list[float]:
    """A bend reference that isn't nearly parallel to the direction."""
    d = mu.normalize(direction)
    return [0.0, 1.0, 0.0] if abs(d[1]) < .7 else [0.0, 0.0, 1.0]


def _private_keys_dropped(branches: list[dict[str, Any]]) -> None:
    for branch in branches:
        for key in [k for k in branch if k.startswith("_")]:
            branch.pop(key)


def _height_error(branch: dict[str, Any]) -> float:
    """How far the neutral contact is from its resting clearance (metres, positive = hovering)."""
    contact = branch["contacts"][0]
    return arch._contact_height(branch, branch["stance_deg"], contact, branch["stance_z_deg"]) - arch._contact_clearance(contact["kind"])


def _ground_arm(branch: dict[str, Any], base_length: float, max_length: float, girth_scale: float = 1.0) -> None:
    """Grow an arm/leg used as a foot until swinging its shoulder can reach the ground, then swing it.

    A limb longer than *max_length* is refused: its girth grows with it, and a very thick limb pushes its
    volume through the floor when the body sinks."""
    base = (list(branch["stance_deg"]), list(branch["stance_z_deg"]))
    target = branch.pop("_extension_target", .72)
    for mult in (1.0, 1.2, 1.45, 1.7, 2.0, 2.4):
        if base_length * mult > max_length:
            break
        arch._refresh_branch_dimensions(branch, base_length * mult / branch["length_m"], girth_scale)
        branch["stance_deg"], branch["stance_z_deg"] = list(base[0]), list(base[1])
        arch._tune_extension(branch, target)
        arch._ground_by_first_joint(branch)
        if abs(_height_error(branch)) < _GROUND_TOLERANCE_M:
            return
    raise AmalgamError(f"{branch['branch_id']} cannot reach the ground")


def _fit_leg(branch: dict[str, Any], hip_y: float, girth_scale: float = 1.0) -> None:
    """Crouch a leg to its stepping extension and size it so its neutral contact meets the ground.

    Slimmer legs move their contact point a little, so the fit is repeated until it settles."""
    arch._tune_extension(branch, branch.pop("_extension_target"))
    for _ in range(4):
        arch._fit_leg_to_hip(branch, hip_y, girth_scale)
        if abs(_height_error(branch)) < _GROUND_TOLERANCE_M:
            return
    raise AmalgamError(f"{branch['branch_id']} does not meet the ground ({_height_error(branch):.4f} m)")


def _set_gait(branch: dict[str, Any], **flags: Any) -> None:
    branch["gait"].update(flags)


# ---- working limbs -------------------------------------------------------------------------------------

def _pull_arm(body: _Body, bid: str, sx: int, t: float, length: float, phase: float, rng: SplitMix64) -> dict[str, Any]:
    """The dragger's reaching arm, set on the flank: elbow up, out and back, hand slapped down ahead."""
    spread = _between(rng, .3, .55)
    yaw = math.radians(_between(rng, -18, 18)) * sx
    origin = [round(sx * body.radius * .98, 4), round(body.y + body.radius * _between(rng, -.25, .2), 4), round(body.z(t), 4)]
    arm = arch._branch(bid, "limb3", "core", origin=origin, direction=_yaw([sx * spread, -.12, 1.0], yaw),
                       up=_yaw([sx * .55, .75, -.35], yaw), length=length, side=_side(origin[0]), attach=0,
                       role="locomotor", phase=phase, support=.6, contact="hand", profile_id="limb3_brachial")
    arm["stance_deg"] = [30.0, -75.0, 30.0]
    arm["stance_z_deg"] = [0.0, 15.0 * sx, 20.0 * sx]
    arm["_extension_target"] = .72
    _set_gait(arm, pole_ik=True)
    return arm


def _arm_pose(arm: dict[str, Any]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The rest basis of a hauling arm and the rotation its stance puts on its last bone (bone basis: Y along the bone,
    Z toward up, X = Y x Z; the same convention as ``kinematics.pose_world``)."""
    forward = mu.normalize(arm["direction"])
    up = mu.normalize(mu.sub(arm["up"], mu.scale(forward, mu.dot(arm["up"], forward))))
    rest = mu.quat_from_basis(mu.cross(forward, up), forward, up)
    stance = (0.0, 0.0, 0.0, 1.0)
    for x_angle, z_angle in zip(arm["stance_deg"], arm["stance_z_deg"], strict=True):
        x_half, z_half = math.radians(x_angle) / 2, math.radians(z_angle) / 2
        local = arch._qmul((0.0, 0.0, math.sin(z_half), math.cos(z_half)), (math.sin(x_half), 0.0, 0.0, math.cos(x_half)))
        stance = arch._qmul(stance, local)
    return rest, stance


def _inverse(q: tuple[float, ...]) -> tuple[float, ...]:
    return (-q[0], -q[1], -q[2], q[3])


def _hand_frame(arm: dict[str, Any], lift: float) -> tuple[list[float], list[float], list[float]]:
    """Where a hand sits and how it points so that, at the neutral pose, it lies flat on the ground ahead of the wrist.

    A child of the arm's last bone rides that bone's posed rotation P = R Q (R the rest basis, Q the stance), and ends
    up at the bone's posed tail. Both the position and the rest rotation are chosen so the posed result is wanted:
    *lift* metres straight above the wrist, its axis horizontal along the arm's reach and its dorsal side up.
    Returns (offset from the wrist, direction, up) in the rest frame."""
    rest, stance = _arm_pose(arm)
    to_rest = lambda v: mu.quat_rotate(rest, mu.quat_rotate(_inverse(stance), mu.quat_rotate(_inverse(rest), v)))  # noqa: E731
    posed_axis = mu.quat_rotate(arch._qmul(rest, stance), (0.0, 1.0, 0.0))
    reach = [posed_axis[0], 0.0, posed_axis[2]]
    if mu.length(reach) < .2:
        reach = [arm["direction"][0], 0.0, arm["direction"][2]]
    axis = mu.normalize(reach)
    wanted = mu.quat_from_basis(mu.cross(axis, [0.0, 1.0, 0.0]), axis, [0.0, 1.0, 0.0])
    hand_rest = arch._qmul(arch._qmul(rest, _inverse(stance)), arch._qmul(_inverse(rest), wanted))
    return (list(to_rest((0.0, lift, 0.0))), list(mu.quat_rotate(hand_rest, (0.0, 1.0, 0.0))),
            list(mu.quat_rotate(hand_rest, (0.0, 0.0, 1.0))))


def _hand(arm: dict[str, Any]) -> dict[str, Any]:
    """A rigid one-bone hand on the end of a hauling arm: it never gestures, and lies flat ahead of the wrist."""
    px, py, pz = mu.frame_from_dir_up(arm["direction"], arm["up"])
    lift, direction, up = _hand_frame(arm, HAND_LIFT_M)
    tip = [round(v, 4) for v in mu.add(mu.add(arm["origin_m"], mu.scale(pz, arm["length_m"])), lift)]
    hand = arch._branch(f"hand{arm['branch_id'].removeprefix('arm')}", "appendage1", arm["branch_id"], origin=tip,
                        direction=direction, up=up, length=HAND_LENGTH_M, side=arm["side"], attach=-1, role="hand",
                        profile_id=HAND_PROFILE)
    return hand


def _push_leg(body: _Body, bid: str, sx: int, t: float, length: float, phase: float, rng: SplitMix64) -> dict[str, Any]:
    """The dragger's driving leg: from the hip, out and back; the knee flares up, the shin folds down."""
    origin = [round(sx * body.radius * .98, 4), round(body.y + body.radius * _between(rng, -.25, .2), 4), round(body.z(t), 4)]
    leg = arch._branch(bid, "limb3", "core", origin=origin, direction=[sx * .55, -.2, -1.0], up=[sx * .55, .75, .35],
                       length=length, side=_side(origin[0]), attach=0, role="locomotor", phase=phase, support=.6,
                       contact="hand", profile_id="limb3_digitigrade")
    leg["stance_deg"] = [30.0, -75.0, -44.0]
    leg["stance_z_deg"] = [0.0, 15.0 * sx, 20.0 * sx]
    leg["_extension_target"] = .72
    _set_gait(leg, pole_ik=True)
    return leg


def _walking_leg(body: _Body, bid: str, kind: str, sx: int, z: float, rng: SplitMix64) -> tuple[dict[str, Any], float]:
    """One leg of a walker on the lower flank. Returns the leg and its hip height."""
    alpha = _between(rng, .15, .6)
    origin = [round(sx * body.radius * math.cos(alpha) * .98, 4), round(body.y - body.radius * math.sin(alpha), 4), round(z, 4)]
    hip_y = origin[1]
    if hip_y < .25:
        raise AmalgamError("hips too low for a leg")
    if kind == "insect":
        fan = _between(rng, -.5, .5)
        leg = arch._branch(bid, "insect_leg4", "core", origin=origin, direction=[sx * 1.0, 0.0, fan], up=[0, 1, 0],
                           length=hip_y * _between(rng, 1.5, 2.0), side=_side(origin[0]), attach=0, role="locomotor",
                           phase=0.0, support=.62, contact="foot", profile_id="insect_leg4_articulated")
        arch._insect_leg(leg)
        _set_gait(leg, pole_ik=False, centre_stance=True)
    else:
        profile = "limb3_digitigrade" if kind == "digitigrade" else "limb3_plantigrade"
        fore = z > body.z(.5)
        leg = arch._branch(bid, "limb3", "core", origin=origin, direction=[sx * _between(rng, .15, .5), -1.0, _between(rng, -.1, .1)],
                           up=[0, 0, -1] if fore else [0, 0, 1], length=hip_y * _between(rng, 1.2, 1.6), side=_side(origin[0]),
                           attach=0, role="locomotor", phase=0.0, support=.58, contact="foot", profile_id=profile)
        leg["_extension_target"] = arch._LIMB_EXTENSION
        _set_gait(leg, pole_ik=True, centre_stance=True)
    _fit_leg(leg, hip_y, WALKER_LEG_GIRTH)
    return leg, hip_y


# ---- the pieces every amalgam has ---------------------------------------------------------------------

def _heads(body: _Body, rng: SplitMix64) -> list[dict[str, Any]]:
    heads = []
    for index in range(1 + (rng.below(100) < 35)):
        theta = math.radians(_between(rng, -55, 55))
        origin, normal = body.point(_between(rng, .6, .95), theta)
        direction = mu.normalize([normal[0] * .55, normal[1] * .3, .85])
        heads.append(arch._branch("head" if index == 0 else f"head{index + 1}", "head1", "core", origin=origin,
                                  direction=list(direction), up=[0, 1, 0], length=body.radius * _between(rng, .8, 1.2),
                                  side=_side(origin[0]), attach=0, role="head"))
    return heads


def _necked_heads(body: _Body, rng: SplitMix64, count: int) -> list[dict[str, Any]]:
    """*count* heads, each on its own two-bone neck (``neck``/``head``, ``neck2``/``head2``, ...), fanned across the
    front-top of the body. The first neck and head are required; later ones are optional, and so is a head on an
    optional neck (a required branch can't hang from an optional one). The first head keeps the id ``head``."""
    out: list[dict[str, Any]] = []
    for index in range(count):
        spread = 0.0 if count == 1 else (index / (count - 1) - .5) * 2         # -1 .. 1 across the fan
        theta = math.radians(_between(rng, -15, 15) + spread * 45)
        origin, normal = body.point(_between(rng, .7, .95), theta)
        yaw = spread * math.radians(_between(rng, 18, 38))
        direction = _yaw(list(mu.normalize([normal[0] * .35, normal[1] * .55 + .2, .8])), yaw)
        nid, hid = ("neck", "head") if index == 0 else (f"neck{index + 1}", f"head{index + 1}")
        neck = arch._branch(nid, "neck2", "core", origin=origin, direction=_round_direction(direction), up=[0, 1, 0],
                            length=body.radius * _between(rng, 1.0, 1.7), side=_side(origin[0]), attach=0, role="neck")
        # A gentle forward curve, within the profile's limits, so the neck isn't a rod.
        neck["stance_deg"] = [round(_between(rng, 4, 14), 3), round(_between(rng, -14, -4), 3)]
        fill = None if index == 0 else 70 + 5 * rng.below(5)
        head_dir = _round_direction(_yaw([direction[0], direction[1] * .8 - .1, direction[2]], 0.0))
        tip = [round(v, 4) for v in mu.add(origin, mu.scale(mu.normalize(direction), neck["length_m"]))]
        head = arch._branch(hid, "head1", nid, origin=tip, direction=head_dir, up=[0, 1, 0],
                            length=body.radius * _between(rng, .8, 1.2), side=_side(tip[0]), attach=-1, role="head")
        for branch in (neck, head):
            if fill is not None:
                branch["required"] = False
                branch["optional_fill_pct"] = fill
        out += [neck, head]
    return out


def _round_direction(vector: list[float]) -> list[float]:
    return [round(v, 6) for v in mu.normalize(vector)]


def _limb_tip_head(body: _Body, flails: list[dict[str, Any]], rng: SplitMix64, floor: float) -> list[dict[str, Any]]:
    """A head grown on the free end of a vestigial limb: a mouth on the end of an arm. Optional, like its limb."""
    # Arms only: a head on a metre of tentacle swings so far in the loops that the stun clip's seam velocity fails QA.
    length = body.radius * _between(rng, .45, .8)
    tips = {}
    for b in flails:
        if b["template"] == "limb3":
            pz = mu.frame_from_dir_up(b["direction"], b["up"])[2]
            tip = [round(v, 4) for v in mu.add(b["origin_m"], mu.scale(pz, b["length_m"]))]
            # The head's own volume must clear the ground, and so must the tip when the telegraph pitches the limb
            # and the head down (a held pose at the start of every attack clip).
            if tip[1] - length * .6 - b["length_m"] * .35 >= floor:
                tips[b["branch_id"]] = (b, pz, tip)
    if not tips:
        return []
    parent, pz, tip = tips[sorted(tips)[rng.below(len(tips))]]
    head = arch._branch("tiphead", "head1", parent["branch_id"],
                        origin=tip, direction=_round_direction(list(pz)), up=_up_for(list(pz)),
                        length=length, side=_side(tip[0]), attach=-1, role="head")
    head["required"] = False
    head["optional_fill_pct"] = 60 + 5 * rng.below(5)
    return [head]


def _striker(body: _Body, rng: SplitMix64) -> dict[str, Any]:
    """The one limb that hits: a contactless arm on the upper front flank."""
    sx = 1 if rng.below(2) else -1
    origin, normal = body.point(_between(rng, .55, .85), sx * math.radians(_between(rng, 50, 80)))
    direction = mu.normalize([normal[0] * .8, normal[1] * .2 + .1, .7])
    arm = arch._branch("striker", "limb3", "core", origin=origin, direction=list(direction), up=[0, 1, 0],
                       length=body.length * _between(rng, .45, .7),
                       side=_side(origin[0]), attach=0, role="manipulator", profile_id="limb3_brachial", support=0.0)
    arm["stance_deg"] = [10.0, -70.0, 8.0]
    return arm


def _flails(body: _Body, existing: list[dict[str, Any]], count: int, rng: SplitMix64,
            floor: float) -> list[dict[str, Any]]:
    """Vestigial and flailing limbs anywhere on the top and sides, sometimes growing from each other.

    *floor* is the height the lowest point of a limb must stay above: a body that sinks when it dies (a walker)
    would otherwise push a low-hanging limb through the ground."""
    out: list[dict[str, Any]] = []
    kinds = ("arm",) * 30 + ("insect",) * 20 + ("tentacle",) * 35 + ("claw",) * 15
    for index in range(count):
        kind = kinds[rng.below(len(kinds))]
        parent = None
        if out and rng.below(100) < 30:
            parent = out[rng.below(len(out))]
        if parent is None:
            # A body lying on the ground has no underside for limbs to wave from: keep them on the upper half.
            reach = 70 if body.on_ground else 100
            theta = math.radians(_between(rng, -reach, reach))
            origin, normal = body.point(_between(rng, .06, .94), theta, inset=.99)
            heading = mu.normalize([normal[0], normal[1] + (.45 if body.on_ground else .15), _between(rng, -.4, .4)])
            parent_id, attach = "core", 0
        else:
            attach = min(1, arch._FRACTIONS[parent["template"]] - 1)
            fractions = arch._PROFILE_FRACTIONS[parent["binding_profile_id"]]
            px, py, pz = mu.frame_from_dir_up(parent["direction"], parent["up"])
            origin = [round(v, 4) for v in mu.add(parent["origin_m"], mu.scale(pz, parent["length_m"] * sum(fractions[:attach])))]
            tilt = [_between(rng, -.6, .6), _between(rng, -.2, .6), _between(rng, -.6, .6)]
            heading = mu.normalize(mu.add(mu.scale(mu.normalize(parent["direction"]), 1.0), tilt))
            parent_id = parent["branch_id"]
        template, profile, length, role = {
            "arm": ("limb3", "limb3_brachial", _between(rng, .2, .6), "flail"),
            "insect": ("insect_leg4", "insect_leg4_articulated", _between(rng, .25, .6), "flail"),
            "tentacle": ("tentacle8", "tentacle8_flexible", _between(rng, .5, 1.2), "flail"),
            "claw": ("appendage1", "appendage1_terminal", _between(rng, .15, .35), "none"),
        }[kind]
        girth = length * arch._PROFILE_GIRTH[profile]
        for _ in range(8):
            lowest = origin[1] + min(0.0, heading[1]) * length - girth * .5
            if lowest >= floor:
                break
            # Too low: lift the limb toward the top of the body and point it up a little more.
            theta = math.radians(_between(rng, -50, 50))
            if parent is None:
                origin, normal = body.point(_between(rng, .06, .94), theta, inset=.99)
                heading = mu.normalize([normal[0], abs(normal[1]) + .3, _between(rng, -.4, .4)])
            else:
                heading = mu.normalize([heading[0], abs(heading[1]) + .4, heading[2]])
        branch = arch._branch(f"flail{index + 1}", template, parent_id, origin=origin, direction=list(heading),
                              up=_up_for(list(heading)), length=length, side=_side(origin[0]), attach=attach, role=role,
                              profile_id=profile)
        branch["required"] = False
        branch["optional_fill_pct"] = 60 + 5 * rng.below(5)
        out.append(branch)
    return out


def _belly(body: _Body, bid: str, length: float, kind: str) -> dict[str, Any]:
    return arch._branch(bid, "tentacle8", "core", origin=[0.0, round(body.y, 4), round(body.rear_z, 4)], direction=[0, 0, -1],
                        up=[0, 1, 0], length=length, role="locomotor", support=.92 if kind == "body" else .8, contact=kind, attach=0)


# ---- modes ---------------------------------------------------------------------------------------------

def _hauled(seed: int, body: _Body, rng: SplitMix64) -> tuple[list[dict[str, Any]], list[str], str]:
    count = 1 + rng.below(3)
    kinds = ["pull"] + [("pull" if rng.below(100) < 55 else "push") for _ in range(count - 1)]
    first_side = 1 if rng.below(2) else -1
    limbs, hands = [], []
    for index, kind in enumerate(kinds):
        sx = first_side * (1 if index % 2 == 0 else -1)
        phase = index * math.tau / count
        if kind == "pull":
            limb = _pull_arm(body, f"arm{index + 1}", sx, _between(rng, .65, .92), body.length * _between(rng, .8, 1.1), phase, rng)
        else:
            limb = _push_leg(body, f"leg{index + 1}", sx, _between(rng, .03, .2), body.length * _between(rng, .6, .85), phase, rng)
        _ground_arm(limb, limb["length_m"], max_length=2.0)
        limbs.append(limb)
        if kind == "pull":
            hands.append(_hand(limb))
    belly = _belly(body, "belly", body.length * _between(rng, .45, .65), "body")
    return limbs + hands + [belly], [b["branch_id"] for b in limbs] + ["belly"], "drag"


def _walker(seed: int, body: _Body, rng: SplitMix64) -> tuple[list[dict[str, Any]], list[str], str]:
    count = 2 + rng.below(3)
    kind_pool = ("mammal", "digitigrade", "insect", "arm")
    kinds = [kind_pool[rng.below(4)] for _ in range(count)]
    if len(set(kinds)) < 2:
        kinds[-1] = kind_pool[(kind_pool.index(kinds[0]) + 1 + rng.below(3)) % 4]
    legs = []
    for index, kind in enumerate(kinds):
        sx = 1 if index % 2 == 0 else -1
        z = body.z(_between(rng, .15, .85))
        bid = f"leg{index + 1}"
        if kind == "arm":
            hip_y = body.y - body.radius * .3
            arm = _pull_arm(body, bid, sx, (z - body.rear_z) / body.length, hip_y * _between(rng, 1.4, 2.0), 0.0, rng)
            _set_gait(arm, centre_stance=True)
            try:
                _ground_arm(arm, arm["length_m"], max_length=body.y * 2.2, girth_scale=WALKER_LEG_GIRTH)
                legs.append(arm)
                continue
            except AmalgamError:
                kind = "mammal"                 # too big an arm to stand on: grow an ordinary leg here instead
        leg, _ = _walking_leg(body, bid, kind, sx, z, rng)
        legs.append(leg)
    # Walk phases follow the order of the legs round the body but are spaced unevenly, so the gait lurches rather
    # than ticking like a metronome; alternate at a run when there is an even count.
    order = sorted(legs, key=lambda b: math.atan2(b["origin_m"][0], b["origin_m"][2] - body.z(.5)))
    gaps = [_between(rng, .55, 1.45) for _ in order]
    elapsed = 0.0
    for k, leg in enumerate(order):
        leg["gait"]["phase_rad"] = round(elapsed / sum(gaps) * math.tau, 6)
        elapsed += gaps[k]
        if len(order) % 2 == 0:
            leg["gait"]["run_phase_rad"] = round((k % 2) * math.pi, 6)
    # One leg limps: the body drops toward it whenever it carries weight.
    limper = order[rng.below(len(order))]
    _set_gait(limper, limp=round(limper["origin_m"][1] * _between(rng, .06, .12), 4))
    return legs, [b["branch_id"] for b in legs], "crawl"


def _slither(seed: int, body: _Body, rng: SplitMix64) -> tuple[list[dict[str, Any]], list[str], str]:
    tail = _belly(body, "body", body.length * _between(rng, 1.0, 1.4), "sliding")
    residuals = [-arch._contact_height(tail, tail["stance_deg"], c, tail["stance_z_deg"]) for c in tail["contacts"]]
    delta = sum(residuals) / len(residuals)
    tail["origin_m"][1] = round(tail["origin_m"][1] + delta, 4)
    tail["gait"]["bend_pole_m"][1] = round(tail["gait"]["bend_pole_m"][1] + delta, 4)
    return [tail], ["body"], "slither"


_MODE_BUILDERS = {"hauled": _hauled, "walker": _walker, "slither": _slither}


def build_amalgam(seed: int) -> dict[str, Any]:
    """Grow one amalgam skeleton from *seed* (raises :class:`AmalgamError` if it can't stand)."""
    mode = mode_for_seed(seed)
    body_rng = _stream(seed, 2)
    length = _between(body_rng, .9, 1.7) * (.78 if mode == "walker" else 1.0)     # walkers are carried high, so smaller
    fat = _between(body_rng, .26, .40)
    # A walker's body is a smaller pod carried high on long legs; the others are fat blobs lying on the ground.
    radius = length * (.18 + (fat - .26) / .14 * .10 if mode == "walker" else fat)
    center_y = radius + length * _between(body_rng, .45, .85) if mode == "walker" else radius + .02
    body = _Body(length, radius, center_y, on_ground=mode != "walker")
    core = arch._branch("core", "core1", None, origin=[0.0, round(center_y, 4), round(body.rear_z, 4)], direction=[0, 0, 1],
                        up=[0, 1, 0], length=length, role="core")
    core["girth_m"] = round(2 * radius, 4)
    working, supports, hint = _MODE_BUILDERS[mode](seed, body, _stream(seed, 3))
    growth = HEADY_SEEDS.get(seed)
    if growth in (HEAD_GROWTH_NECKED, HEAD_GROWTH_CLUSTER):
        heads = _necked_heads(body, _stream(seed, 8), 1 if growth == HEAD_GROWTH_NECKED else 2 + _stream(seed, 9).below(2))
    else:
        heads = _heads(body, _stream(seed, 4))
    striker = _striker(body, _stream(seed, 5))
    branches = [core] + heads + working + [striker]
    target = 8 + _stream(seed, 6).below(7)
    # A walker sinks by DEATH_DROP of its hip height when it dies; a body already on the ground doesn't.
    hips = [b["origin_m"][1] for b in working if b["gait"]["role"] == "locomotor"]
    sink = motion_planner.DEATH_DROP * (sum(hips) / len(hips)) if mode == "walker" and hips else 0.0
    flails = _flails(body, branches, max(2, min(MAX_BRANCHES - len(branches), target - len(branches))), _stream(seed, 7),
                     floor=.03 + sink)
    branches += flails
    if growth == HEAD_GROWTH_LIMB_TIP:
        branches += _limb_tip_head(body, flails, _stream(seed, 8), floor=.03 + sink)
    _private_keys_dropped(branches)
    legs = [b for b in working if b["gait"]["role"] == "locomotor" and b.get("contacts") and b["contacts"][0]["kind"] in ("foot", "hand")]
    traits = {"min_support": 1 if len(legs) <= 3 else 2, "body_on_ground": mode == "hauled", "ring": False,
              "pole_ik": False, "compact_clip": len(legs) >= 4}
    return arch._finalise(
        branches, supports, list(supports), "asymmetric", archetype=f"amalgam_{mode}", body_plan="amalgam",
        family="amalgam", style="anatomical", hint=hint, skeleton_id=amalgam_id(seed, mode),
        dims=(center_y + radius, length, 2 * radius),
        provenance={"generator": "cc-gen-3", "seed": seed, "preset": "amalgam", "mode": mode},
        landmarks={"pelvis": "core", "shoulder": "core", "neck": "head"}, traits=traits)


def committed_amalgams() -> list[dict[str, Any]]:
    return [build_amalgam(seed) for seed in AMALGAM_SEEDS]


def validate_amalgam(source: dict[str, Any]) -> list[str]:
    """Compile one amalgam and check it end to end without Blender. Returns problems (empty when it is sound)."""
    from ..config import paths
    from ..library.catalog import CatalogError, compile_catalog, load_sources
    from ..locomotion.block import locomotion_block
    from ..locomotion.qa import evaluate_locomotion
    from .actions import AttackPlanError, resolve_attack
    from .kinematics import contact_world, neutral_pose_world
    from .motion import build_motion

    problems: list[str] = []
    if len(source["branches"]) > MAX_BRANCHES:
        problems.append(f"{len(source['branches'])} branches")
    sources = load_sources(paths().data)
    sources["skeletons"] = [source]
    sources["parts"] = []
    try:
        skeleton = compile_catalog(sources)["skeletons"][0]
    except CatalogError as exc:
        return problems + [f"compile: {exc}"]
    if len(skeleton["bones"]) > 120:
        problems.append(f"{len(skeleton['bones'])} bones")
    try:
        skeleton["locomotion"] = locomotion_block(skeleton)
    except Exception as exc:  # noqa: BLE001 - report whatever the block builder chokes on
        return problems + [f"locomotion block: {exc}"]
    problems += [f"locomotion: {d}" for d in evaluate_locomotion(skeleton)]
    try:
        resolve_attack(skeleton)
    except AttackPlanError as exc:
        problems.append(f"attack: {exc}")
    pose = neutral_pose_world(skeleton)
    for branch in skeleton["branches"]:
        role = branch["gait_role"]
        if role == "locomotor" and branch.get("contacts") and branch["contacts"][0]["kind"] in ("foot", "hand"):
            clearance = arch._contact_clearance(branch["contacts"][0]["kind"])
            height = contact_world(pose, branch, branch["contacts"][0])[1]
            if abs(height - clearance) > 3 * _GROUND_TOLERANCE_M:
                problems.append(f"{branch['branch_id']} contact {height:.4f} m off the ground")
        grown = role == "neck" or (role == "head" and branch["parent_branch"] != "core")
        if grown or (role in ("flail", "none") and branch["branch_id"].startswith("flail")):
            lowest = min(min(pose[n]["head"][1], pose[n]["tail"][1]) for n in branch["bone_names"]) - branch["girth_m"] * .5
            if lowest < .005:
                problems.append(f"{branch['branch_id']} dips to {lowest:.3f} m")
    try:
        build_motion(skeleton, {"hint": skeleton["locomotion_hint"], "frequency_hz": 1.2, "amplitude_deg": 16, "bob_m": .005})
    except Exception as exc:  # noqa: BLE001
        problems.append(f"motion plan: {exc}")
    return problems
