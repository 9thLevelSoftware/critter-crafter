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
from typing import Any, Iterable, Sequence

from .actions import action_global_phase, attack_target_at, resolve_attack

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
    "radial_low_tentacle_crawler":      {"family": "radial",     "stride": .15, "support": .70, "clearance": .07, "cadence": .72},
    "serpentine_limbless_articulated":  {"family": "serpentine", "stride": .31, "support": .82, "clearance": .025,"cadence": .92},
    "serpentine_segmented_paired_legs": {"family": "serpentine", "stride": .23, "support": .86, "clearance": .018,"cadence": .74},
    "dragger_forelimb_puller":           {"family": "dragger",    "stride": .43, "support": .73, "clearance": .075,"cadence": .82},
    "dragger_belly_hauler":              {"family": "dragger",    "stride": .25, "support": .84, "clearance": .035,"cadence": .65},
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
        "idle": 60, "walk": locomotion,
        "run": max(40 if complex_gait else 36, round(locomotion / 1.55)),
        "stun": 30, "telegraph": 18, "attack": 18, "hit": 10, "death": 36,
    }[name]


def _root_vertical(name: str, u: float, bob: float, drop: float) -> float:
    if name == "idle":
        return 0.0
    if name in ("walk", "run"):
        scale = .8 if name == "walk" else 1.1
        return scale * bob * (.5 - .5 * math.cos(TAU * u))
    if name == "hit":
        return 0.0
    if name == "death":
        # Collapse is articulated while the body remains supported; sinking
        # the armature root through the floor would create invalid geometry.
        return 0.0
    return 0.0


def _gesture(name: str, role: str, k: int, u: float, phase: float,
             amplitude: float, chain_count: int) -> tuple[float, float]:
    if role == "stabilizer":
        return 0.0, 0.0
    if role == "locomotor":
        return 0.0, 0.0
    th = TAU * u
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
        if role == "manipulator":
            return (.3 if k == 0 else .1)*amp*math.sin(th + phase - k*.25), 0.0
        if role == "core":
            return .06*amp*math.sin(2*th+k*.2), .04*amp*math.sin(th)
        if role == "head":
            return .08*amp*math.sin(2*th), 0.0
        return 0.0, 0.0  # locomotors are driven by contact IK
    if name == "idle":
        if role == "sliding":
            return 0.0, .035*amp*math.sin(th + phase - k*.35)
        return ((.10*amp*math.sin(th + phase + k*.3), .05*amp*math.cos(th))
                if role != "locomotor" else (0.0, 0.0))
    if name == "stun":
        if role == "sliding":
            return 0.0, math.radians(1.0)*math.sin(2*th+2.3*k)
        return math.radians(1.2)*math.sin(2*th+1.7*k+phase), math.radians(1.0)*math.sin(2*th+2.3*k)
    if name == "telegraph":
        s = smoothstep(u / .8)
        return ({"core": (-.14*s, 0), "manipulator": (-.75*s if k == 0 else -.2*s, 0),
                 "head": (-.3*s, 0), "sway": (.3*s*math.sin(2*th-k*.3), 0),
                 "sliding": (0, .18*s*math.sin(k*.55))}.get(role, (0, 0)))
    if name == "attack":
        # Begin exactly at the held telegraph pose with zero endpoint velocity,
        # strike, then recover to neutral.  This makes telegraph -> attack C1.
        wind = {"core": -.14, "manipulator": (-.75 if k == 0 else -.2),
                "head": -.3, "sway": .3*math.sin(4*math.pi-k*.3)}.get(role, 0.0)
        if role == "sliding":
            wind = .18 * math.sin(k*.55)
        strike = {"core": .18, "manipulator": (.95 if k == 0 else .07),
                  "head": .38, "sway": .45*math.cos(k*.4)}.get(role, 0.0)
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
        p = math.sin(math.pi*u)
        return ({"core": (-.20*p, 0), "head": (-.32*p, .16*p),
                 "manipulator": (-.24*p, 0), "sway": (-.2*p, 0)}.get(role, (0, 0)))
    if name == "death":
        s = smoothstep(u)
        return ({"locomotor": (.7*s if k == 0 else -.35*s, 0), "manipulator": ((.5 if k == 0 else .07)*s, 0),
                 "sway": (.55*s, .15*s), "head": (.45*s, .2*s), "core": (.08*s, 0)}.get(role, (0, 0)))
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
    if skeleton.get("locomotion", {}).get("mode") != "legs":
        return False
    return not any(branch_role(b) == "locomotor" and c.get("kind") in ("body", "sliding")
                   for b in skeleton["branches"] for c in b.get("contacts") or [])


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
    stabilized: set[str] = set()
    grounded_body = False
    locomotor_count = sum(branch_role(branch) == "locomotor" and bool(branch.get("contacts"))
                           for branch in skeleton["branches"])
    stable_minimum = 2 if skeleton.get("family") in ("quadruped", "hexapod", "crawler", "radial") else 1
    for branch in skeleton["branches"]:
        if branch_role(branch) == "locomotor":
            if _contact_kind(branch, skeleton) in ("body", "sliding"):
                grounded_body = True
            parent_id = branch.get("parent_branch")
            while parent_id:
                stabilized.add(parent_id)
                parent_id = by_id[parent_id].get("parent_branch")
    root_offset = tuple(skeleton.get("neutral_pose", {}).get("root_offset_m", (0.0, 0.0, 0.0)))
    core_height = max((float(b["head_m"][1]) for b in skeleton["bones"]), default=1.0)
    clips = []
    for name in CLIP_ORDER:
        phase_driven = overlay and name in ("walk", "run")
        frames = (FPS if phase_driven
                  else _clip_frames(name, cadence, skeleton.get("family") in ("crawler", "radial")))
        cycle_hz = (FPS / frames) if name in ("walk", "run") else 0.0
        speed = (float(profile["stride"]) * cycle_hz if name in ("walk", "run") and not phase_driven else 0.0)
        samples = []
        for frame in range(frames + 1):
            u = frame / frames
            rotations = dict(neutral)
            contacts = []
            for br in skeleton["branches"]:
                role = branch_role(br)
                phase_rad = branch_phase(br)
                kind = _contact_kind(br, skeleton)
                motion_role = "sliding" if kind in ("body", "sliding") else role
                if (attack_plan and name in ("telegraph", "attack")
                        and br["branch_id"] == attack_plan["effector"]["branch_id"]):
                    # The semantic action solver owns this chain.  Layering a
                    # second procedural gesture changes its declared neutral
                    # anchor and can push the IK solution outside joint limits.
                    motion_role = "stabilizer"
                if br["branch_id"] in stabilized:
                    motion_role = "stabilizer"
                for k, bone_name in enumerate(br["bone_names"]):
                    rx, rz = _gesture(name, motion_role, k, u, phase_rad, amplitude, len(br["bone_names"]))
                    if rx or rz:
                        rotations[bone_name] = quat_mul(neutral[bone_name], quat_xz(rx, rz))
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
                        if skeleton.get("family") == "radial":
                            support = min(support, .53)
                    support = max(support, min(.92, stable_minimum / max(1, locomotor_count) + .02))
                    seam_offset = (.265 if skeleton.get("family") == "radial" else .25)
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
                    contacts.append({
                        "branch_id": br["branch_id"], "kind": kind,
                        "bone_name": br["bone_names"][-1], "phase": phase,
                        "drive_ik": name in ("walk", "run") and not phase_driven,
                        "support_fraction": support, "stride_m": stride, "clearance_m": clearance, **path,
                    })
                elif br.get("contacts"):
                    # Manipulation and body annotations are sampled even when
                    # they are not ground supports.  Their target follows the
                    # evaluated point unless an action explicitly plants it.
                    contacts.append({"branch_id":br["branch_id"], "kind":kind,
                                     "bone_name":br["bone_names"][-1], "phase":u,
                                     "support_fraction":0.0, "stride_m":0.0, "clearance_m":0.0,
                                     "drive_ik":False,
                                     "forward_m":0.0, "height_m":0.0, "forward_dphase":0.0,
                                     "height_dphase":0.0, "support":False})
            samples.append({
                "frame": frame, "phase": u, "root_position_m": [root_offset[0],
                    root_offset[1] + (0.0 if grounded_body or phase_driven else _root_vertical(
                        name, u, bob, .55*max(.1, core_height))), root_offset[2]],
                "simulated_forward_m": speed * (frame / FPS),
                "rotations_xyzw": {k: [round(v, 8) for v in q] for k, q in rotations.items()},
                "contacts": contacts,
                **({"action_target": attack_target_at(
                        attack_plan, action_global_phase(attack_plan, name, u))}
                   if name in ("telegraph", "attack") and attack_plan else {}),
            })
        seam_offset = .265 if skeleton.get("family") == "radial" else .25
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
                    if skeleton.get("family") == "radial":
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
