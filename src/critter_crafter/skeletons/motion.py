"""Deterministic contact-driven motion planning (no Blender dependency).

The planner describes *where* support contacts must be.  Blender's IK pass turns
those targets into deformation-bone rotations and then bakes the evaluated pose.
All vectors here use the catalog frame (metres, +Y up, +Z forward).

Neutral rotations are local deltas from the straight bind skeleton.  Their
right-handed bone-local basis is +Y along the bone, +Z toward ``up_m`` and
+X = +Y cross +Z.  This is also Blender's edit-bone basis after align_roll.
Quaternions are stored [x, y, z, w].
"""

from __future__ import annotations

import math
import zlib
from typing import Any, Iterable, Sequence

from .actions import action_global_phase, attack_target_at, resolve_attack
from .. import mathutil as mu
from .kinematics import contact_world, neutral_pose_world, pose_world
from .traits import traits_of

FPS = 30
TAU = 2.0 * math.pi

CLIP_ORDER = ("idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death")
LOOP_CLIPS = frozenset(("idle", "walk", "run", "stun"))

# Two deliberately different motion characters per family.  A skeleton can
# select one explicitly with motion_profile_id; otherwise its stable id chooses.
ARCHETYPE_PROFILES: dict[str, dict[str, float | str]] = {
    "biped_plantigrade_humanoid":       {"family": "biped",      "stride": .46, "support": .62, "clearance": .10, "cadence": .90},
    "biped_digitigrade_creature":       {"family": "biped",      "stride": .62, "support": .56, "clearance": .15, "cadence": 1.08},
    "quadruped_lean_digitigrade":       {"family": "quadruped",  "stride": .58, "support": .61, "clearance": .09, "cadence": 1.05},
    "quadruped_stocky_plantigrade":     {"family": "quadruped",  "stride": .42, "support": .69, "clearance": .07, "cadence": .82},
    "hexapod_elongated_insect":         {"family": "hexapod",    "stride": .34, "support": .64, "clearance": .08, "cadence": 1.10},
    "hexapod_compact_insect":           {"family": "hexapod",    "stride": .27, "support": .58, "clearance": .11, "cadence": 1.32},
    "crawler_alien_tripod":             {"family": "crawler",    "stride": .25, "support": .72, "clearance": .06, "cadence": 1.05},
    "crawler_bilateral_eight_legged":   {"family": "crawler",    "stride": .36, "support": .66, "clearance": .10, "cadence": 1.18},
    "radial_raised_articulated_walker": {"family": "radial",     "stride": .19, "support": .76, "clearance": .05, "cadence": .88},
    "serpentine_limbless_articulated":  {"family": "serpentine", "stride": .31, "support": .82, "clearance": .025,"cadence": .92},
    "serpentine_segmented_paired_legs": {"family": "serpentine", "stride": .23, "support": .86, "clearance": .018,"cadence": .74},
    "dragger_forelimb_puller":           {"family": "dragger",    "stride": .43, "support": .73, "clearance": .075,"cadence": .82},
    "dragger_belly_hauler":              {"family": "dragger",    "stride": .25, "support": .84, "clearance": .035,"cadence": .65},
    "dragger_arm_leg_crawler":           {"family": "dragger",    "stride": .36, "support": .78, "clearance": .06, "cadence": .74},
    "amalgam_hauled":                    {"family": "amalgam",    "stride": .40, "support": .76, "clearance": .07, "cadence": .80},
    "amalgam_walker":                    {"family": "amalgam",    "stride": .30, "support": .68, "clearance": .08, "cadence": 1.00},
    "amalgam_slither":                   {"family": "amalgam",    "stride": .31, "support": .82, "clearance": .025,"cadence": .92},
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, x))


def smoothstep(x: float) -> float:
    x = _clamp(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def pulse(x: float, peak: float = .4) -> float:
    return smoothstep(x / peak) if x <= peak else 1.0 - smoothstep((x - peak) / (1.0 - peak))


def quat_mul(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_inverse(q: Sequence[float]) -> tuple[float, float, float, float]:
    return (-q[0], -q[1], -q[2], q[3])


def _unit_quat(q: Sequence[float]) -> tuple[float, float, float, float]:
    length = math.sqrt(sum(v*v for v in q))
    out = tuple(v / length for v in q)
    return tuple(-v for v in out) if out[3] < 0 else out


def enforce_discrete_loop_seams(plan: dict[str, Any]) -> None:
    """Make sampled endpoint position *and finite-difference velocity* repeat.

    Runtime clips are sampled at 30 FPS, so matching the adjacent quaternion
    increment is more useful than relying only on an analytic derivative.
    """
    for clip in plan["clips"]:
        samples = clip["samples"]
        if not clip["loop"] or len(samples) < 3:
            continue
        first, last = samples[0], samples[-1]
        last["rotations_xyzw"] = {name: list(q) for name, q in first["rotations_xyzw"].items()}
        for offset in (1, 2):
            before, after = samples[-1-offset], samples[offset]
            for name, q0_raw in first["rotations_xyzw"].items():
                q0, qk = tuple(q0_raw), tuple(after["rotations_xyzw"][name])
                delta = quat_mul(quat_inverse(q0), qk)
                before["rotations_xyzw"][name] = list(_unit_quat(quat_mul(q0, quat_inverse(delta))))
            before["root_position_m"] = [2*a-b for a,b in zip(first["root_position_m"], after["root_position_m"])]
        last["root_position_m"] = list(first["root_position_m"])
        last["contacts"] = [dict(c) for c in first["contacts"]]


def quat_axis(axis: int, angle: float) -> tuple[float, float, float, float]:
    q = [0.0, 0.0, 0.0, math.cos(angle * .5)]
    q[axis] = math.sin(angle * .5)
    return tuple(q)  # type: ignore[return-value]


def quat_xz(rx: float, rz: float) -> tuple[float, float, float, float]:
    """Local X swing followed by local Z twist, canonical hemisphere."""
    q = quat_mul(quat_axis(2, rz), quat_axis(0, rx))
    if q[3] < 0:
        q = tuple(-v for v in q)
    return q


def select_profile(skeleton: dict[str, Any]) -> tuple[str, dict[str, float | str]]:
    requested = (skeleton.get("motion_profile_id") or skeleton.get("anatomy", {}).get("motion_profile_id")
                 or skeleton.get("anatomy", {}).get("archetype_id"))
    if requested in ARCHETYPE_PROFILES:
        return requested, ARCHETYPE_PROFILES[requested]
    family = skeleton.get("family", "crawler")
    candidates = sorted(k for k, p in ARCHETYPE_PROFILES.items() if p["family"] == family)
    if not candidates:
        candidates = ["crawler_alien_tripod"]
    # Legacy v2 skeletons have no anatomy id; choose the conservative family
    # profile.  V3 never relies on a hash/random choice.
    key = candidates[0]
    return key, ARCHETYPE_PROFILES[key]


def branch_role(branch: dict[str, Any]) -> str:
    return str(branch.get("gait_role", branch.get("gait", {}).get("role", "none")))


def branch_phase(branch: dict[str, Any]) -> float:
    return float(branch.get("gait_phase_rad", branch.get("gait", {}).get("phase_rad", 0.0)))


def support_fraction(branch: dict[str, Any], default: float) -> float:
    raw = branch.get("support_phase", branch.get("gait", {}).get("support_phase", default))
    return _clamp(float(raw), .51, .92)


def neutral_quaternions(skeleton: dict[str, Any]) -> dict[str, tuple[float, float, float, float]]:
    """Return local delta quaternions; the straight bind armature remains untouched."""
    out = {b["name"]: (0.0, 0.0, 0.0, 1.0) for b in skeleton["bones"]}
    explicit = skeleton.get("neutral_pose", {}).get("rotations", [])
    if explicit:
        out.update({r["bone_name"]: tuple(r["rotation_xyzw"]) for r in explicit})
        return out
    for br in skeleton["branches"]:
        sx = br.get("stance_deg", [])
        sz = br.get("stance_z_deg", [])
        for i, name in enumerate(br["bone_names"]):
            rx = math.radians(float(sx[i])) if i < len(sx) else 0.0
            rz = math.radians(float(sz[i])) if i < len(sz) else 0.0
            out[name] = quat_xz(rx, rz)
    return out


def _hermite(p0: float, p1: float, m0: float, m1: float, u: float) -> float:
    u2, u3 = u * u, u * u * u
    return ((2*u3 - 3*u2 + 1)*p0 + (u3 - 2*u2 + u)*m0
            + (-2*u3 + 3*u2)*p1 + (u3 - u2)*m1)


def contact_trajectory(phase: float, stride_m: float, clearance_m: float,
                       support: float) -> dict[str, float | bool]:
    """Periodic C1 stance/swing target relative to its neutral contact.

    During stance the world contact is fixed while the simulated body advances,
    hence its local forward coordinate moves backwards.  The swing is a cubic
    Hermite return whose endpoint velocity exactly matches stance.
    """
    p = phase % 1.0
    half = stride_m * .5
    slope = -stride_m / support
    if p <= support:
        return {"forward_m": half + slope * p, "height_m": 0.0,
                "forward_dphase": slope, "height_dphase": 0.0, "support": True}
    u = (p - support) / (1.0 - support)
    scale = 1.0 - support
    z = _hermite(-half, half, slope * scale, slope * scale, u)
    # derivative of Hermite with respect to u, then phase
    dzdu = ((6*u*u - 6*u)*(-half) + (3*u*u - 4*u + 1)*(slope*scale)
            + (-6*u*u + 6*u)*half + (3*u*u - 2*u)*(slope*scale))
    h = clearance_m * math.sin(math.pi * u) ** 2
    dh = clearance_m * math.pi * math.sin(2.0 * math.pi * u) / scale
    return {"forward_m": z, "height_m": h, "forward_dphase": dzdu / scale,
            "height_dphase": dh, "support": False}


def _clip_frames(name: str, cadence_hz: float, complex_gait: bool = False) -> int:
    # Dense enough for the baked IK solution's second-order endpoint velocity
    # estimate, while run remains the shorter/faster cycle.
    locomotion = max(60 if complex_gait else 48, round(FPS / cadence_hz))
    return {
        "idle": 120, "walk": locomotion,
        "run": max(40 if complex_gait else 36, round(locomotion / 1.55)),
        "stun": 60, "telegraph": 18, "attack": 18, "hit": 18, "death": 54,
    }[name]


def _root_vertical(name: str, u: float, bob: float, drop: float, hip_height: float = 0.0) -> float:
    """Root offset from the neutral height (never above it: raising the hips costs a planted foot its reach)."""
    if name == "idle":
        return -.004 * hip_height * (.5 - .5 * math.cos(2.0 * TAU * u))     # breath: two dips per loop
    if name in ("walk", "run"):
        scale = .8 if name == "walk" else 1.1
        return scale * bob * (.5 - .5 * math.cos(TAU * u))
    if name == "stun":
        return -.01 * hip_height * (.5 - .5 * math.cos(TAU * u))
    if name == "hit":
        return -.012 * hip_height * math.sin(math.pi * u) ** 2
    if name == "death":
        # Collapse over the planted feet: stagger, buckle, one small bounce, settle.
        return -_death_drop(u) * hip_height
    return 0.0


DEATH_DROP = .2       # of the hip height: the body sinks this far while the feet hold


# How far a whole neck chain bends compared with a bare head's gesture.
NECK_SHARE = .6


def _death_drop(u: float) -> float:
    if u < .2:
        return .02 * math.sin(math.pi * u / .2) ** 2                       # stagger
    if u < .78:
        return DEATH_DROP * smoothstep((u - .2) / .58)
    if u < .88:
        return DEATH_DROP - .03 * math.sin(math.pi * (u - .78) / .10) ** 2   # bounce
    return DEATH_DROP


def _spring(t: float) -> float:
    """Damped recoil, peak about 1, exactly zero at t=1."""
    t = _clamp(t, 0.0, 1.0)
    return 2.0 * math.exp(-3.5 * t) * math.sin(TAU * 1.25 * t) * (1.0 - smoothstep((t - .8) / .2))


def _gesture(name: str, role: str, k: int, u: float, phase: float,
             amplitude: float, chain_count: int, *, depth: int = 0, tag: float = 0.0,
             death_style: str = "collapse") -> tuple[float, float]:
    if role == "stabilizer":
        return 0.0, 0.0
    if role == "locomotor":
        return 0.0, 0.0
    if role == "neck":
        # A neck plays the head's gestures spread down its chain, so the whole neck bends about as far as a head
        # does (a head on top adds its own curve): each bone takes NECK_SHARE / chain_count of the head's angle.
        rx, rz = _gesture(name, "head", k, u, phase, amplitude, chain_count, depth=depth, tag=tag, death_style=death_style)
        share = NECK_SHARE / max(1, chain_count)
        return rx * share, rz * share
    th = TAU * u
    ph = phase + tag
    frac = k / max(1, chain_count - 1)
    amp = amplitude * (2.5 / chain_count if chain_count > 4 else 1.0)
    if name in ("walk", "run"):
        amp *= 1.0 if name == "walk" else .72
        if role == "sliding":
            # Local Z is dorsal, so this produces a readable horizontal
            # travelling wave without pitching the ventral surface into the
            # floor.  The former .02 factor was an obsolete workaround for a
            # socket-bridge skinning defect fixed in the reference geometry.
            return 0.0, .24*amp*math.sin(th + phase - k*.62)
        if role == "sway":
            return .42*amp*math.sin(th - k*.42), .3*amp*math.cos(th-k*.42)
        if role == "flail":
            # One cycle per 30-frame walk overlay: more harmonics break the loop seam at this sample rate.
            return .42*amp*math.sin(th + ph - k*.5), .3*amp*math.cos(th + ph - k*.5)
        if role == "manipulator":
            return (.3 if k == 0 else .1)*amp*math.sin(th + phase - k*.25), 0.0
        if role == "core":
            return .06*amp*math.sin(2*th+k*.2), .04*amp*math.sin(th)
        if role == "head":
            return .08*amp*math.sin(2*th), 0.0
        return 0.0, 0.0  # locomotors are driven by contact IK
    if name == "idle":
        # Layers on whole cycles per loop (2, 3, 5, 7, 4) so the loop seam stays exact. The angles are small and
        # fixed: pinned feet must stay inside their reach (build_motion also budgets them per skeleton).
        if role == "sliding":
            return 0.0, .2*amp*math.sin(th + phase - k*.35)
        if role == "core":
            # A torso lying on the ground can't pitch without sinking one end into it.
            g = .3 if death_style == "sprawl" else 1.0
            return (g*math.radians(.8 + .7*frac)*math.sin(2*th + ph + .35*k),
                    g*math.radians(.5)*math.sin(3*th + ph*1.3))
        if role == "head":
            return (math.radians(2.2)*math.sin(5*th + ph) - math.radians(.5)*math.sin(2*th + ph),
                    math.radians(4.0)*math.sin(7*th + ph*.7))
        if role in ("sway", "flail"):
            g = 1.0 + .6*k
            return (math.radians(2.6)*g*math.sin(4*th + ph - .55*k),
                    math.radians(1.8)*g*math.sin(4*th + ph - .55*k + 1.3))
        if role == "manipulator":
            return (math.radians(1.6)*math.sin(3*th + ph - .3*k),
                    math.radians(1.0)*math.sin(5*th + ph*1.1 + k))
        return math.radians(1.2)*math.sin(3*th + ph + .3*k), math.radians(.7)*math.sin(5*th + ph)
    if name == "stun":
        if role == "sliding":
            return 0.0, math.radians(1.0)*math.sin(2*th+2.3*k)
        if role == "core":
            g = .3 if death_style == "sprawl" else 1.0
            return (g*math.radians(2.2)*math.sin(2*th + .9*k + ph), g*math.radians(1.0)*math.sin(3*th + ph))
        if role == "head":
            return (math.radians(9.0)*(.5 - .5*math.cos(th)) + math.radians(2.5)*math.sin(3*th + ph),
                    math.radians(6.0)*math.sin(2*th + ph))
        if role in ("sway", "flail"):
            g = 1.0 + .5*k
            return (math.radians(5.0)*g*math.sin(2*th - .5*k + ph),
                    math.radians(3.5)*g*math.sin(2*th - .5*k + ph + 1.0))
        if role == "manipulator":
            return math.radians(3.5)*math.sin(2*th + k + ph), math.radians(2.0)*math.sin(3*th + ph)
        return math.radians(2.0)*math.sin(2*th + ph + .3*k), math.radians(1.0)*math.sin(3*th + ph)
    if name == "telegraph":
        s = smoothstep(u / .8)
        return ({"core": (-.14*s, 0), "manipulator": (-.75*s if k == 0 else -.2*s, 0),
                 "head": (-.3*s, 0), "sway": (.3*s*math.sin(2*th-k*.3), 0),
                 "flail": (.3*s*math.sin(2*th-k*.3), 0),
                 "sliding": (0, .18*s*math.sin(k*.55))}.get(role, (0, 0)))
    if name == "attack":
        # Begin exactly at the held telegraph pose with zero endpoint velocity,
        # strike, then recover to neutral.  This makes telegraph -> attack C1.
        wind = {"core": -.14, "manipulator": (-.75 if k == 0 else -.2),
                "head": -.3, "sway": .3*math.sin(4*math.pi-k*.3),
                "flail": .3*math.sin(4*math.pi-k*.3)}.get(role, 0.0)
        if role == "sliding":
            wind = .18 * math.sin(k*.55)
        strike = {"core": .18, "manipulator": (.95 if k == 0 else .07),
                  "head": .38, "sway": .45*math.cos(k*.4), "flail": .45*math.cos(k*.4)}.get(role, 0.0)
        if role == "sliding":
            strike_rz = .16 * math.sin(k*.55)
            if u <= .42:
                a = smoothstep(u/.42)
                return 0.0, wind + (strike_rz-wind)*a
            return 0.0, strike_rz * (1.0-smoothstep((u-.42)/.58))
        if u <= .42:
            a = smoothstep(u / .42)
            return wind + (strike - wind) * a, 0.0
        a = smoothstep((u - .42) / .58)
        return strike * (1.0 - a), 0.0
    if name == "hit":
        # Recoil along -Z that travels: each bone, and each branch further from the core, starts a little later.
        delay = .06 * (depth + k)
        sp = _spring((u - delay) / max(.2, 1.0 - delay))
        per = 2.0 / max(1, chain_count) ** .5
        if death_style == "sprawl":
            per *= .25              # a torso lying on the ground can't pitch without sinking one end into it
        return ({"core": (-.11*sp*per, .02*sp), "head": (-.32*sp, .16*sp),
                 "manipulator": (-.24*sp, 0), "sway": (-.28*sp, .08*sp), "flail": (-.28*sp, .08*sp),
                 "sliding": (0, .08*sp), "none": (-.08*sp, 0)}.get(role, (0, 0)))
    if name == "death":
        s = smoothstep((u - .15) / .85)
        if role == "core":
            slump = .10 if death_style == "kneel_slump" else .06
            return (slump*s*(1.0 + .3*frac), 0.0)
        if role == "sliding":
            return 0.0, .07*s                                       # the body curls where it lies
        return ({"manipulator": ((.5 if k == 0 else .07)*s, .05*s), "head": (.45*s, .2*s),
                 "sway": (.35*s, .1*s), "flail": (.35*s, .1*s), "none": (.15*s, 0)}.get(role, (0, 0)))
    return 0.0, 0.0


def _contact_kind(branch: dict[str, Any], skeleton: dict[str, Any]) -> str:
    contacts = branch.get("contacts") or []
    if contacts:
        return str(contacts[0].get("kind", "foot"))
    hint = skeleton.get("locomotion_hint", "crawl")
    if hint == "slither":
        return "sliding"
    if hint == "drag":
        return "hand"
    return "foot"


def runtime_legs(skeleton: dict[str, Any]) -> bool:
    """True when legged locomotion is placed at runtime (walk/run are overlays).

    Skeletons whose locomotion includes sliding or body contacts still bake their
    travel until the runtime slide model lands.
    """
    return skeleton.get("locomotion", {}).get("mode") in ("legs", "slide")


def is_pinned_locomotor(branch: dict[str, Any], skeleton: dict[str, Any]) -> bool:
    """A weight-bearing foot or hand the bake can hold at its neutral world point with IK.

    Sliding and body contacts, and chains with several contacts, can't be pinned; they keep the
    authored axial pose instead (see ``_frozen_bones``). Mirrors ``ops_skeleton._make_controls``.
    """
    if branch_role(branch) != "locomotor":
        return False
    support = set(skeleton.get("anatomy", {}).get("support_branches", []))
    if support and branch["branch_id"] not in support:
        return False
    if "anatomy" in skeleton and not branch.get("contacts"):
        return False
    contacts = branch.get("contacts") or []
    if len(contacts) > 1:
        return False
    return _contact_kind(branch, skeleton) not in ("body", "sliding")


def _ancestor_bones(skeleton: dict[str, Any], branch: dict[str, Any]) -> set[str]:
    """Bones of every ancestor branch from the root down to the bone *branch* is attached to."""
    by_id = {b["branch_id"]: b for b in skeleton["branches"]}
    bones: set[str] = set()
    child = branch
    while child.get("parent_branch"):
        parent = by_id[child["parent_branch"]]
        names = parent["bone_names"]
        attach = child.get("attach_bone")
        end = names.index(attach) + 1 if attach in names else len(names)
        bones.update(names[:end])
        child = parent
    return bones


def _frozen_bones(skeleton: dict[str, Any]) -> set[str]:
    """Bones that must not move because an unpinnable contact rides on them.

    A pinned foot lets the torso move (the IK holds the foot), but a sliding or body contact is held by
    the authored pose, so its ancestors are frozen from the root down to the bone it is attached to.
    """
    frozen: set[str] = set()
    for branch in skeleton["branches"]:
        if (branch_role(branch) == "locomotor" and branch.get("contacts")
                and not is_pinned_locomotor(branch, skeleton)):
            frozen |= _ancestor_bones(skeleton, branch)
    return frozen


def _death_style(skeleton: dict[str, Any], traits: dict[str, Any]) -> str:
    """How the body dies: sprawl (dragged), coil (slides), kneel_slump (upright torso) or collapse."""
    if traits["body_on_ground"]:
        return "sprawl"
    if skeleton.get("locomotion", {}).get("mode") == "slide":
        return "coil"
    core = next((b for b in skeleton["branches"] if branch_role(b) == "core"), None)
    if core:
        first = next(b for b in skeleton["bones"] if b["name"] == core["bone_names"][0])
        if first["tail_m"][1] - first["head_m"][1] > .7 * mu.length(mu.sub(first["tail_m"], first["head_m"])):
            return "kneel_slump"
    return "collapse"


REACH_BUDGET = .2    # a pinned leg's hip may move toward or away from its foot by this share of the leg's spare reach


def build_motion(skeleton: dict[str, Any], gait: dict[str, Any]) -> dict[str, Any]:
    """Create all eight clip plans in a JSON-serializable form.

    With runtime legs, walk/run are one-cycle overlays: locomotor chains hold the
    neutral stance (the runtime planner and IK own them), the body carries no bob
    (the runtime adds it), and non-locomotor chains keep their phased gestures.
    """
    overlay = runtime_legs(skeleton)
    profile_id, profile = select_profile(skeleton)
    attack_plan = resolve_attack(skeleton) if skeleton.get("anatomy", {}).get("archetype_id") else None
    annotated = [float(b.get("gait", {}).get("cadence_hz", 0.0)) for b in skeleton["branches"]]
    annotated = [x for x in annotated if x > 0.0 and branch_role(next(b for b in skeleton["branches"] if float(b.get("gait", {}).get("cadence_hz", 0.0)) == x)) == "locomotor"]
    base_cadence = sum(annotated) / len(annotated) if annotated else float(gait.get("cadence_hz", gait.get("frequency_hz", 1.8)))
    cadence = base_cadence * float(profile["cadence"])
    amplitude = math.radians(float(gait.get("amplitude_deg", 24.0)))
    bob = float(gait.get("bob_m", .02))
    neutral = neutral_quaternions(skeleton)
    by_id = {branch["branch_id"]: branch for branch in skeleton["branches"]}
    frozen = _frozen_bones(skeleton)
    pinned_branches = {b["branch_id"]: is_pinned_locomotor(b, skeleton) for b in skeleton["branches"]}
    by_id = {b["branch_id"]: b for b in skeleton["branches"]}
    depths: dict[str, int] = {}
    for b in skeleton["branches"]:
        depth, cursor = 0, b
        while cursor.get("parent_branch"):
            depth += 1
            cursor = by_id[cursor["parent_branch"]]
        depths[b["branch_id"]] = depth
    # A stable per-branch phase (not the authored one, which is 0 for many branches) so layers don't march in step.
    tags = {b["branch_id"]: (zlib.crc32(b["branch_id"].encode()) % 3600) / 3600.0 * TAU for b in skeleton["branches"]}
    grounded_body = False
    locomotor_count = sum(branch_role(branch) == "locomotor" and bool(branch.get("contacts"))
                           for branch in skeleton["branches"])
    traits = traits_of(skeleton)
    death_style = _death_style(skeleton, traits)
    stable_minimum = int(traits["min_support"])
    for branch in skeleton["branches"]:
        if branch_role(branch) == "locomotor" and (branch.get("contacts") or "anatomy" not in skeleton):
            if _contact_kind(branch, skeleton) in ("body", "sliding"):
                grounded_body = True
    root_offset = tuple(skeleton.get("neutral_pose", {}).get("root_offset_m", (0.0, 0.0, 0.0)))
    core_height = max((float(b["head_m"][1]) for b in skeleton["bones"]), default=1.0)
    # The pinned legs: where each foot is held, how far its hip is, and how much reach it has to spare.
    neutral_world = neutral_pose_world(skeleton)
    pins = []
    driver_bones: set[str] = set()
    for br in skeleton["branches"]:
        if not pinned_branches[br["branch_id"]] or not br.get("contacts"):
            continue
        contact = br["contacts"][0]
        target = contact_world(neutral_world, br, contact)
        hip = neutral_world[br["bone_names"][0]]["head"]
        reach = sum(neutral_world[n]["length"] for n in br["bone_names"][:int(contact["bone_index"]) + 1])
        d0 = mu.length(mu.sub(hip, target))
        pins.append({"branch_id": br["branch_id"], "hip_bone": br["bone_names"][0], "target": target, "d0": d0,
                     "allowed": REACH_BUDGET * max(0.0, .95 * reach - d0), "hip_y": hip[1] - target[1]})
        driver_bones |= _ancestor_bones(skeleton, br)
    hip_height = (sum(pin["hip_y"] for pin in pins) / len(pins)) if pins else .5 * core_height
    effector_id = attack_plan["effector"]["branch_id"] if attack_plan else None
    clips = []
    for name in CLIP_ORDER:
        phase_driven = overlay and name in ("walk", "run")
        frames = (FPS if phase_driven
                  else _clip_frames(name, cadence, bool(traits["compact_clip"])))
        cycle_hz = (FPS / frames) if name in ("walk", "run") else 0.0
        speed = (float(profile["stride"]) * cycle_hz if name in ("walk", "run") and not phase_driven else 0.0)
        # The semantic attack solver owns the effector chain, and its target is fixed relative to the body:
        # the chain's ancestors hold still while it works.
        frozen_clip = set(frozen)
        released = {effector_id} if effector_id and name in ("telegraph", "attack") else set()
        if released:
            frozen_clip |= _ancestor_bones(skeleton, by_id[effector_id])
        clip_pins = [pin for pin in pins if pin["branch_id"] not in released]

        def pose_for(u: float, scale: float) -> tuple[dict[str, Any], float]:
            rotations = dict(neutral)
            for br in skeleton["branches"]:
                role = branch_role(br)
                kind = _contact_kind(br, skeleton)
                motion_role = "sliding" if kind in ("body", "sliding") else role
                if role == "locomotor" and not br.get("contacts") and "anatomy" in skeleton:
                    motion_role = "core"          # a body chain that carries legs but is not itself a foot
                if br["branch_id"] in released:
                    # Layering a second procedural gesture changes the declared neutral anchor and can push
                    # the IK solution outside joint limits.
                    motion_role = "stabilizer"
                for k, bone_name in enumerate(br["bone_names"]):
                    if bone_name in frozen_clip:
                        continue
                    rx, rz = _gesture(name, motion_role, k, u, branch_phase(br), amplitude, len(br["bone_names"]),
                                      depth=depths[br["branch_id"]], tag=tags[br["branch_id"]], death_style=death_style)
                    if bone_name in driver_bones:
                        rx, rz = rx * scale, rz * scale
                    if rx or rz:
                        rotations[bone_name] = quat_mul(neutral[bone_name], quat_xz(rx, rz))
            dy = 0.0
            if not (grounded_body or phase_driven):
                dy = _root_vertical(name, u, bob, .55 * max(.1, core_height), hip_height)
                if name != "death":
                    dy *= scale
            return rotations, dy

        def worst_stretch(scale: float) -> float:
            worst = 0.0
            for frame in range(frames + 1):
                rotations, dy = pose_for(frame / frames, scale)
                pose = pose_world(skeleton, rotations, (root_offset[0], root_offset[1] + dy, root_offset[2]))
                for pin in clip_pins:
                    stretch = abs(mu.length(mu.sub(pose[pin["hip_bone"]]["head"], pin["target"])) - pin["d0"])
                    if stretch > 1e-5:
                        worst = max(worst, stretch / pin["allowed"] if pin["allowed"] > 1e-5 else math.inf)
            return worst

        scale = 1.0
        if clip_pins and not (grounded_body and not driver_bones):
            for _ in range(6):
                worst = worst_stretch(scale)
                if worst <= 1.0:
                    break
                scale *= .5 if math.isinf(worst) else max(0.0, .92 / worst)
        samples = []
        for frame in range(frames + 1):
            u = frame / frames
            rotations, dy = pose_for(u, scale)
            contacts = []
            for br in skeleton["branches"]:
                role = branch_role(br)
                phase_rad = branch_phase(br)
                kind = _contact_kind(br, skeleton)
                if role == "locomotor" and (br.get("contacts") or "anatomy" not in skeleton):
                    support = support_fraction(br, float(profile["support"]))
                    authored_stride = float(br.get("stride_m", br.get("gait", {}).get("stride_m", profile["stride"])))
                    # Reserve reach for knee/pole bending and socket motion.  IK
                    # never stretches a chain to satisfy an oversized stride.
                    stride = min(authored_stride, float(br.get("length_m", 1.0)) * .32)
                    clearance = float(br.get("clearance_m", br.get("gait", {}).get("clearance_m", profile["clearance"])))
                    if name == "run":
                        stride *= 1.25
                        clearance *= 1.35
                        support = max(.51, support - .10)
                        if traits["ring"]:
                            support = min(support, .53)
                    support = max(support, min(.92, stable_minimum / max(1, locomotor_count) + .02))
                    seam_offset = (.265 if traits["ring"] else .25)
                    phase = ((u + phase_rad / TAU + (seam_offset if name in ("walk", "run") else 0.0)) % 1.0
                             if not phase_driven else u)
                    if name in ("walk", "run") and not phase_driven:
                        if kind in ("body", "sliding"):
                            path = {"forward_m":0.0, "height_m":0.0, "forward_dphase":0.0,
                                    "height_dphase":0.0, "support":True}
                        else:
                            path = contact_trajectory(phase, stride, clearance, support)
                    else:
                        planted = name != "death" or u < .25
                        path = {"forward_m": 0.0, "height_m": 0.0, "forward_dphase": 0.0,
                                "height_dphase": 0.0, "support": planted}
                    driven = name in ("walk", "run") and not phase_driven
                    # pin: hold the foot at its neutral world point so the body can move over it. Death keeps the
                    # feet held after the support is released, so the legs buckle instead of sinking through the floor.
                    ik = ("drive" if driven else
                          "pin" if (pinned_branches.get(br["branch_id"]) and (path["support"] or name == "death")
                                    and br["branch_id"] not in released)
                          else "off")
                    contacts.append({
                        "branch_id": br["branch_id"], "kind": kind,
                        "bone_name": br["bone_names"][-1], "phase": phase,
                        "drive_ik": driven, "ik": ik,
                        "support_fraction": support, "stride_m": stride, "clearance_m": clearance, **path,
                    })
                elif br.get("contacts"):
                    # Manipulation and body annotations are sampled even when
                    # they are not ground supports.  Their target follows the
                    # evaluated point unless an action explicitly plants it.
                    contacts.append({"branch_id":br["branch_id"], "kind":kind,
                                     "bone_name":br["bone_names"][-1], "phase":u,
                                     "support_fraction":0.0, "stride_m":0.0, "clearance_m":0.0,
                                     "drive_ik":False, "ik":"off",
                                     "forward_m":0.0, "height_m":0.0, "forward_dphase":0.0,
                                     "height_dphase":0.0, "support":False})
            samples.append({
                "frame": frame, "phase": u, "root_position_m": [root_offset[0], root_offset[1] + dy, root_offset[2]],
                "simulated_forward_m": speed * (frame / FPS),
                "rotations_xyzw": {k: [round(v, 8) for v in q] for k, q in rotations.items()},
                "contacts": contacts,
                **({"action_target": attack_target_at(
                        attack_plan, action_global_phase(attack_plan, name, u))}
                   if name in ("telegraph", "attack") and attack_plan else {}),
            })
        seam_offset = .265 if traits["ring"] else .25
        contact_schedule = []
        for br in skeleton["branches"]:
            if not br.get("contacts"):
                continue
            kind = _contact_kind(br, skeleton)
            phase_offset = ((branch_phase(br) / TAU + seam_offset) % 1.0
                            if name in ("walk", "run") else 0.0)
            if branch_role(br) == "locomotor" and name in ("walk", "run") and not phase_driven:
                stance = support_fraction(br, float(profile["support"]))
                if name == "run":
                    stance = max(.51, stance - .10)
                    if traits["ring"]:
                        stance = min(stance, .53)
                stance = max(stance, min(.92, stable_minimum / max(1, locomotor_count) + .02))
            elif branch_role(br) == "locomotor":
                stance = .25 if name == "death" else 1.0
                if phase_driven:
                    phase_offset = 0.0
            else:
                stance = 0.0
            for contact_index, contact in enumerate(br.get("contacts", [])):
                contact_schedule.append({"contact_id":f"{br['branch_id']}:{contact_index}",
                    "branch_id":br["branch_id"], "contact_index":contact_index,
                    "kind":contact.get("kind", kind), "phase_offset":round(phase_offset, 7),
                    "stance_fraction":round(stance, 7),
                    "support":br["branch_id"] in set(skeleton.get("anatomy", {}).get("support_branches", []))})
        clips.append({
            "name": name, "loop": name in LOOP_CLIPS, "phase_driven": phase_driven, "frames": frames, "fps": FPS,
            "driver_scale": round(scale, 4),
            "duration_s": frames / FPS, "cadence_hz": cycle_hz if name in ("walk", "run") else 0.0,
            "speed_mps": speed, "stride_m": float(profile["stride"]) * (1.25 if name == "run" else 1.0),
            "playback": {"wrap": "loop" if name in LOOP_CLIPS else "once", "phase_range": [0.0, 1.0],
                         "rate_range": [0.5, 1.5],
                         "continuation": "attack" if name == "telegraph" else "locomotion"},
            "support_policy": {"mode":"authored_schedule", "death_release_phase":.25 if name == "death" else None,
                               "airborne_phases":[]},
            "contact_schedule": contact_schedule,
            "samples": samples,
        })
    result = {"schema_version": "motion-samples-1", "skeleton_id": skeleton["skeleton_id"],
            "motion_profile_id": profile_id, "root_motion": "fixed_horizontal",
            "neutral_basis": "local +Y=bone, +Z=up, +X=Y_cross_Z", "clips": clips}
    if attack_plan:
        result["attack_plan"] = attack_plan
    return result


def iter_contact_samples(motion: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for clip in motion["clips"]:
        for sample in clip["samples"]:
            yield from sample["contacts"]
