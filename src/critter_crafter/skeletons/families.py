"""Procedural skeleton families (`critter skeleton vary`).

Each builder turns a seeded RNG plus the family's parameter ranges (data/skeleton_families/<family>.json)
into a skeleton source document (schemas/skeleton.v2.schema.json). Authoring-time randomness only:
the output files are what gets reviewed, approved and shipped. Runtime determinism is cc-gen-2's job.

Conventions (docs/frame.md + measured in Blender): a positive `stance_deg` / clip rotation about a
bone's local X swings the bone's tip toward the branch's `up` vector.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from ..recipes.rng import SplitMix64

PI = math.pi


class Rand:
    def __init__(self, seed: int):
        self.r = SplitMix64(seed)

    def uniform(self, lo: float, hi: float, digits: int = 3) -> float:
        return round(lo + (hi - lo) * self.r.below(1_000_001) / 1_000_000, digits)

    def rng(self, pair: list[float], digits: int = 3) -> float:
        return self.uniform(pair[0], pair[1], digits)

    def chance(self, pct: float) -> bool:
        return self.r.below(1000) < int(pct * 10)

    def choice(self, items: list[Any]) -> Any:
        return items[self.r.below(len(items))]

    def int_between(self, lo: int, hi: int) -> int:
        return lo + self.r.below(hi - lo + 1)


def _v(x: float, y: float, z: float) -> list[float]:
    return [round(x, 4), round(y, 4), round(z, 4)]


def branch(bid: str, template: str, parent: str | None, *, origin: list[float] | None = None, attach: int = 0,
           direction: list[float], up: list[float], length: float, size: str = "M", side: str = "C",
           required: bool = True, fill_pct: int | None = None, mirror_of: str | None = None,
           accepts: list[str], templates: list[str] | None = None, connector: str | None = "M",
           role: str = "none", phase: float = 0.0, stance: list[float] | None = None,
           stance_z: list[float] | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {
        "branch_id": bid, "template": template, "parent_branch": parent, "attach_bone_index": attach,
        "direction": direction, "up": up, "length_m": round(length, 3), "size_class": size, "side": side,
        "required": required, "accepts": {"categories": accepts}, "connector_size_class": connector,
        "gait": {"role": role, "phase_rad": round(phase, 6)},
    }
    if origin is not None:
        b["origin_m"] = origin
    if templates:
        b["accepts"]["templates"] = templates
    if not required:
        b["optional_fill_pct"] = int(fill_pct if fill_pct is not None else 50)
    if mirror_of:
        b["mirror_of"] = mirror_of
    if stance:
        b["stance_deg"] = [round(s, 1) for s in stance]
    if stance_z:
        b["stance_z_deg"] = [round(s, 1) for s in stance_z]
    return b


def _mirror(v: list[float]) -> list[float]:
    return [-v[0], v[1], v[2]]


# Stance presets (degrees about local X per bone; + swings toward the branch's up side).
HUMAN_LEG = [10.0, -24.0, 14.0]
HUMAN_ARM = [12.0, 22.0, 0.0]
SPIDER_LEG = [38.0, -72.0, -16.0, 12.0]
HIND_LEG = [-12.0, 26.0, -14.0]


def biped(p: dict[str, Any], r: Rand) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    hip = r.rng(p["hip_height_m"])
    torso = r.rng(p["torso_length_m"])
    hunch = r.rng(p["hunch"])
    shoulder = r.rng(p["shoulder_half_width_m"])
    head_len = r.rng(p["head_length_m"])
    arm_len = r.rng(p["arm_length_m"])
    top_y = hip + torso * math.cos(hunch)
    top_z = torso * math.sin(hunch)
    core_dir = [0.0, math.cos(hunch), math.sin(hunch)]
    br = [
        branch("core", "spine3", None, origin=_v(0, hip, 0), direction=core_dir, up=[0, 0, 1], length=torso,
               size="L", accepts=["core"], templates=["spine3"], connector=None, role="core"),
        branch("leg_L", "limb3", "core", origin=_v(0.17, hip, 0), direction=[0.05, -1, 0], up=[0, 0, 1],
               length=hip * 1.02, accepts=["limb"], role="locomotor", phase=0.0, stance=HUMAN_LEG),
        branch("leg_R", "limb3", "core", origin=_v(-0.17, hip, 0), direction=[-0.05, -1, 0], up=[0, 0, 1],
               length=hip * 1.02, side="R", mirror_of="leg_L", accepts=["limb"], role="locomotor", phase=PI,
               stance=HUMAN_LEG),
        branch("arm_L", "limb3", "core", attach=2, origin=_v(shoulder, top_y - 0.14, top_z - 0.02),
               direction=[0.35, -1, 0.1], up=[0, 0, 1], length=arm_len, side="L", required=False,
               fill_pct=p["arm_fill_pct"], accepts=["limb"], role="manipulator", phase=PI, stance=HUMAN_ARM),
        branch("arm_R", "limb3", "core", attach=2, origin=_v(-shoulder, top_y - 0.14, top_z - 0.02),
               direction=[-0.35, -1, 0.1], up=[0, 0, 1], length=arm_len, side="R", required=False,
               fill_pct=p["arm_fill_pct"], mirror_of="arm_L", accepts=["limb"], role="manipulator", phase=0.0,
               stance=HUMAN_ARM),
        branch("head", "head1", "core", attach=2, origin=_v(0, top_y + 0.02, top_z - 0.06),
               direction=[0, 0.35, 1], up=[0, 1, 0], length=head_len, accepts=["head"], role="head"),
    ]
    for side, sx in (("L", 1), ("R", -1)):
        br.append(branch(f"arm_{side}_tip", "appendage1", f"arm_{side}", attach=-1, direction=[0.35 * sx, -1, 0.1],
                         up=[0, 0, 1], length=0.35, size="S", side=side, required=False, fill_pct=p["tip_fill_pct"],
                         mirror_of="arm_L_tip" if side == "R" else None, accepts=["appendage"], connector="S",
                         role="manipulator", phase=PI if side == "L" else 0.0))
    if r.chance(p["extra_arms_pct"]):
        for side, sx in (("L", 1), ("R", -1)):
            br.append(branch(f"arm2_{side}", "limb3", "core", attach=1,
                             origin=_v(sx * shoulder * 0.8, hip + torso * 0.45, 0.05), direction=[sx * 0.8, -0.8, 0.4],
                             up=[0, 0, 1], length=r.rng(p["arm_length_m"]), side=side, required=False, fill_pct=70,
                             mirror_of="arm2_L" if side == "R" else None, accepts=["limb"], role="manipulator",
                             phase=0.0 if side == "L" else PI, stance=HUMAN_ARM))
    if r.chance(p["back_tentacle_pct"]):
        br.append(branch("back", "tentacle8", "core", attach=1, origin=_v(0, hip + torso * 0.55, -0.16),
                         direction=[0, 0.6, -1], up=[0, 1, 0], length=r.rng([1.0, 1.25]), required=False, fill_pct=80,
                         accepts=["limb", "tail"], templates=["tentacle8"], role="sway"))
    return {"locomotion_hint": "biped", "symmetry_pct": r.int_between(40, 80)}, br


def quadruped(p: dict[str, Any], r: Rand) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    hip = r.rng(p["hip_height_m"])
    spine = r.rng(p["spine_length_m"])
    splay = r.rng(p["leg_splay"])
    width = r.rng(p["half_width_m"])
    z0 = -spine / 2
    front_z, back_z = z0 + spine * 0.92, z0 + spine * 0.08
    leg_len = hip * r.rng([0.98, 1.08])
    br = [branch("core", "spine3", None, origin=_v(0, hip, z0), direction=[0, r.rng([-0.08, 0.12]), 1], up=[0, -1, 0],
                 length=spine, size="L", accepts=["core"], templates=["spine3"], connector=None, role="core")]
    legs = [("FL", width, front_z, 2, 0.0, HUMAN_LEG), ("FR", -width, front_z, 2, PI, HUMAN_LEG),
            ("BL", width, back_z, 0, PI, HIND_LEG), ("BR", -width, back_z, 0, 0.0, HIND_LEG)]
    for name, x, z, attach, phase, stance in legs:
        sx = 1 if x > 0 else -1
        br.append(branch(f"leg_{name}", "limb3", "core", attach=attach, origin=_v(x, hip, z),
                         direction=[sx * splay, -1, 0.05 if z > 0 else -0.05], up=[0, 0, 1], length=leg_len,
                         side="L" if sx > 0 else "R", mirror_of=f"leg_{name[0]}L" if sx < 0 else None,
                         accepts=["limb"], role="locomotor", phase=phase, stance=stance))
    br.append(branch("head", "head1", "core", attach=2, origin=_v(0, hip + 0.08, z0 + spine + 0.02),
                     direction=[0, r.rng([-0.1, 0.35]), 1], up=[0, 1, 0], length=r.rng(p["head_length_m"]),
                     accepts=["head"], role="head"))
    br.append(branch("tail", "tentacle8", "core", origin=_v(0, hip + 0.03, z0 - 0.02), direction=[0, 0.25, -1],
                     up=[0, 1, 0], length=r.rng([1.0, 1.25]), required=False, fill_pct=p["tail_fill_pct"],
                     accepts=["limb", "tail"], templates=["tentacle8"], role="sway"))
    for i in range(r.int_between(0, p["max_dorsal"])):
        br.append(branch(f"dorsal_{i}", "appendage1", "core", attach=1 + (i % 2),
                         origin=_v(r.uniform(-0.08, 0.08), hip + 0.2, z0 + spine * (0.35 + 0.25 * i)),
                         direction=[r.uniform(-0.4, 0.4), 1, r.uniform(-0.4, 0.2)], up=[0, 0, 1], length=0.35, size="S",
                         required=False, fill_pct=55, accepts=["appendage"], connector="S", role="sway", phase=i * 1.3))
    return {"locomotion_hint": "quadruped", "symmetry_pct": r.int_between(40, 70)}, br


def _ring_legs(n: int, radius: float, height: float, leg_len: float, drop: float, r: Rand, phase_mode: str,
               start_deg: float, template: str, stance: list[float], templates: list[str] | None = None,
               jitter_deg: float = 10.0) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        ang = math.radians(start_deg + 360.0 * i / n + r.uniform(-jitter_deg, jitter_deg))
        dx, dz = math.sin(ang), math.cos(ang)
        side = "C" if abs(dx) < 0.2 else ("L" if dx > 0 else "R")
        phase = (2 * PI * i / n) if phase_mode == "wave" else (0.0 if i % 2 == 0 else PI)
        out.append(branch(f"leg_{i}", template, "core", origin=_v(dx * radius, height, dz * radius),
                          direction=[dx, -drop, dz], up=[0, 1, 0], length=leg_len, side=side, accepts=["limb"],
                          templates=templates, role="locomotor", phase=phase, stance=stance))
    return out


def crawler(p: dict[str, Any], r: Rand) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    h = r.rng(p["body_height_m"])
    core_len = r.rng(p["core_length_m"])
    n = r.int_between(*p["leg_count"])
    br = [branch("core", "core1", None, origin=_v(0, h, -core_len / 2), direction=[0, r.rng([-0.1, 0.15]), 1],
                 up=[0, 1, 0], length=core_len, size="L", accepts=["core", "head"], connector=None, role="core")]
    br += _ring_legs(n, core_len * 0.35, h - 0.04, r.rng(p["leg_length_m"]), r.rng([0.45, 0.8]), r, "wave",
                     r.uniform(0, 360), "insect_leg4", SPIDER_LEG)
    br.append(branch("mouth", "appendage1", "core", origin=_v(0, h - 0.02, core_len / 2), direction=[0, -0.1, 1],
                     up=[0, 1, 0], length=0.35, size="S", required=False, fill_pct=70, accepts=["appendage"],
                     connector="S", role="manipulator"))
    if r.chance(p["dorsal_tentacle_pct"]):
        br.append(branch("dorsal", "tentacle8", "core", origin=_v(0, h + core_len * 0.4, 0),
                         direction=[r.uniform(-0.3, 0.3), 1, -0.35], up=[0, 0, 1], length=r.rng([1.0, 1.25]),
                         required=False, fill_pct=65, accepts=["limb", "tail"], templates=["tentacle8"], role="sway"))
    return {"locomotion_hint": "crawl", "symmetry_pct": r.int_between(25, 60)}, br


def hexapod(p: dict[str, Any], r: Rand) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    h = r.rng(p["body_height_m"])
    spine = r.rng(p["spine_length_m"])
    z0 = -spine / 2
    leg_len = r.rng(p["leg_length_m"])
    drop = r.rng([0.4, 0.7])
    br = [branch("core", "spine3", None, origin=_v(0, h, z0), direction=[0, 0, 1], up=[0, 1, 0], length=spine,
                 size="L", accepts=["core"], templates=["spine3"], connector=None, role="core")]
    # tripod gait: L0 R1 L2 together, R0 L1 R2 together
    for i, t in enumerate((0.85, 0.5, 0.15)):
        for side, sx in (("L", 1), ("R", -1)):
            fwd = (0.35, 0.0, -0.35)[i]
            phase = 0.0 if (i % 2 == 0) == (sx > 0) else PI
            br.append(branch(f"leg_{side}{i}", "insect_leg4", "core", attach=min(2, int(t * 3)),
                             origin=_v(sx * 0.14, h, z0 + spine * t), direction=[sx * 1.0, -drop, fwd],
                             up=[0, 1, 0], length=leg_len, side=side, mirror_of=f"leg_L{i}" if sx < 0 else None,
                             accepts=["limb"], role="locomotor", phase=phase, stance=SPIDER_LEG))
    br.append(branch("head", "head1", "core", attach=2, origin=_v(0, h + 0.04, z0 + spine + 0.02),
                     direction=[0, -0.1, 1], up=[0, 1, 0], length=r.rng([0.5, 0.62]), required=False, fill_pct=75,
                     accepts=["head"], role="head"))
    for side, sx in (("L", 1), ("R", -1)):
        br.append(branch(f"mandible_{side}", "appendage1", "core", attach=2, origin=_v(sx * 0.12, h - 0.05, z0 + spine),
                         direction=[sx * 0.4, -0.2, 1], up=[0, 1, 0], length=0.35, size="S", side=side, required=False,
                         fill_pct=50, mirror_of="mandible_L" if sx < 0 else None, accepts=["appendage"], connector="S",
                         role="manipulator"))
    return {"locomotion_hint": "crawl", "symmetry_pct": r.int_between(50, 85)}, br


def radial(p: dict[str, Any], r: Rand) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    h = r.rng(p["body_height_m"])
    core_len = r.rng(p["core_length_m"])
    n = r.int_between(*p["arm_count"])
    br = [branch("core", "core1", None, origin=_v(0, h - core_len / 2, 0), direction=[0, 1, 0], up=[0, 0, 1],
                 length=core_len, size="L", accepts=["core", "head"], connector=None, role="core")]
    br += _ring_legs(n, core_len * 0.3, h - core_len * 0.3, r.rng(p["arm_length_m"]), r.rng([0.2, 0.55]), r, "wave",
                     r.uniform(0, 360), "tentacle8", [8.0, -4.0, -4.0, -4.0, -4.0, 2.0, 4.0, 4.0], jitter_deg=6.0)
    if r.chance(p["crown_pct"]):
        br.append(branch("crown", "appendage1", "core", attach=-1, direction=[0, 1, 0.2], up=[0, 0, 1], length=0.35,
                         size="S", required=False, fill_pct=80, accepts=["appendage", "head"], connector="S",
                         role="head"))
    return {"locomotion_hint": "crawl", "symmetry_pct": r.int_between(50, 90)}, br


def serpentine(p: dict[str, Any], r: Rand) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    h = r.rng(p["head_height_m"])
    core_len = r.rng(p["core_length_m"])
    body_len = r.rng(p["body_length_m"])
    # lateral S-curve rest pose: a random sine over the 8 body bones
    amp, freq, ph = r.rng([6.0, 16.0]), r.rng([0.6, 1.4]), r.uniform(0, 2 * PI)
    s_curve = [amp * math.sin(ph + freq * k) for k in range(8)]
    br = [branch("core", "core1", None, origin=_v(0, h, 0), direction=[0, r.rng([0.0, 0.3]), 1], up=[0, 1, 0],
                 length=core_len, size="L", accepts=["core", "head"], connector=None, role="core"),
          branch("body", "tentacle8", "core", origin=_v(0, h - 0.04, 0.02), direction=[0, -(h - 0.12), -body_len],
                 up=[0, 1, 0], length=body_len, accepts=["limb", "tail"], templates=["tentacle8"], role="locomotor",
                 stance=[-4.0, 2.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0], stance_z=s_curve)]
    br.append(branch("jaw", "appendage1", "core", attach=-1, direction=[0, -0.3, 1], up=[0, 1, 0], length=0.35,
                     size="S", required=False, fill_pct=70, accepts=["appendage"], connector="S", role="manipulator"))
    if r.chance(p["second_tail_pct"]):
        br.append(branch("tail_2", "tentacle8", "core", origin=_v(r.uniform(-0.1, 0.1), h + 0.05, -0.02),
                         direction=[r.uniform(-0.5, 0.5), 0.4, -1], up=[0, 1, 0], length=r.rng([1.0, 1.25]),
                         required=False, fill_pct=70, accepts=["limb", "tail"], templates=["tentacle8"], role="sway"))
    for side, sx in (("L", 1), ("R", -1)):
        br.append(branch(f"fin_{side}", "appendage1", "core", origin=_v(sx * 0.16, h, core_len * 0.2),
                         direction=[sx, 0.3, -0.4], up=[0, 1, 0], length=0.35, size="S", side=side, required=False,
                         fill_pct=p["fin_fill_pct"], mirror_of="fin_L" if sx < 0 else None, accepts=["appendage"],
                         connector="S", role="sway", phase=0.0 if sx > 0 else PI))
    if r.chance(p["centipede_pct"]):
        for k in sorted({r.int_between(1, 2), r.int_between(3, 4), r.int_between(5, 6)}):
            for side, sx in (("L", 1), ("R", -1)):
                br.append(branch(f"crawl_{side}{k}", "appendage1", "body", attach=k, origin=None,
                                 direction=[sx, -0.6, 0.2], up=[0, 1, 0], length=0.35, size="S", side=side,
                                 required=False, fill_pct=80, mirror_of=f"crawl_L{k}" if sx < 0 else None,
                                 accepts=["appendage"], connector="S", role="sway",
                                 phase=k * 0.9 + (0.0 if sx > 0 else PI)))
    return {"locomotion_hint": "slither", "symmetry_pct": r.int_between(60, 95)}, br


def dragger(p: dict[str, Any], r: Rand) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    h = r.rng(p["chest_height_m"])
    torso = r.rng(p["torso_length_m"])
    tilt = r.rng(p["torso_tilt"])
    arm_len = r.rng(p["arm_length_m"])
    top = [0.0, h + torso * math.sin(tilt), torso * math.cos(tilt) - torso * 0.5]
    br = [branch("core", "spine3", None, origin=_v(0, h, -torso * 0.5), direction=[0, math.sin(tilt), math.cos(tilt)],
                 up=[0, -1, 0], length=torso, size="L", accepts=["core"], templates=["spine3"], connector=None,
                 role="core")]
    for side, sx in (("L", 1), ("R", -1)):
        br.append(branch(f"arm_{side}", "limb3", "core", attach=2, origin=_v(sx * 0.26, top[1] - 0.05, top[2] - 0.08),
                         direction=[sx * 0.35, -0.7, 0.8], up=[0, 1, 0], length=arm_len, side=side,
                         mirror_of="arm_L" if sx < 0 else None, accepts=["limb"], role="locomotor",
                         phase=0.0 if sx > 0 else PI * r.uniform(0.0, 1.0), stance=[-10.0, 25.0, 10.0]))
    br.append(branch("head", "head1", "core", attach=2, origin=_v(0, top[1] + 0.02, top[2] - 0.02),
                     direction=[0, 0.1, 1], up=[0, 1, 0], length=r.rng([0.5, 0.62]), accepts=["head"], role="head"))
    br.append(branch("trail", "tentacle8", "core", origin=_v(0, h - 0.05, -torso * 0.5 - 0.02),
                     direction=[r.uniform(-0.2, 0.2), -0.35, -1], up=[0, 1, 0], length=r.rng([1.0, 1.3]),
                     required=False, fill_pct=p["trail_fill_pct"], accepts=["limb", "tail"], templates=["tentacle8"],
                     role="sway", stance=[-8.0, -6.0, 3.0, 3.0, 3.0, 2.0, 2.0, 0.0]))
    if r.chance(p["stump_legs_pct"]):
        for side, sx in (("L", 1), ("R", -1)):
            br.append(branch(f"stump_{side}", "limb3", "core", origin=_v(sx * 0.18, h - 0.02, -torso * 0.45),
                             direction=[sx * 0.4, -0.35, -1], up=[0, 1, 0], length=r.rng([0.85, 1.0]), side=side,
                             required=False, fill_pct=60, mirror_of="stump_L" if sx < 0 else None, accepts=["limb"],
                             role="sway", phase=0.0 if sx > 0 else PI, stance=[-5.0, 8.0, 0.0]))
    return {"locomotion_hint": "drag", "symmetry_pct": r.int_between(30, 70)}, br


BUILDERS: dict[str, Callable[[dict[str, Any], Rand], tuple[dict[str, Any], list[dict[str, Any]]]]] = {
    "biped": biped, "quadruped": quadruped, "crawler": crawler, "hexapod": hexapod,
    "radial": radial, "serpentine": serpentine, "dragger": dragger,
}


def build_skeleton(family: str, index: int, params: dict[str, Any], seed: int) -> dict[str, Any]:
    r = Rand(seed * 1000 + index)
    head, branches = BUILDERS[family](params, r)
    return {
        "schema_version": "2.0.0",
        "skeleton_id": f"{family}_v{index:02d}",
        "family": family,
        "locomotion_hint": head["locomotion_hint"],
        "status": "draft",
        "symmetry_pct": head["symmetry_pct"],
        "provenance": {"generator": "skeleton-vary-1", "family_params": f"{family}.json", "seed": seed, "index": index},
        "branches": branches,
    }
