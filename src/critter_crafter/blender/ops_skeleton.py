"""Build a skeleton armature from a compiled catalog skeleton and bake its procedural clip set.

Clips (all authored with zero horizontal root motion; the root bone only bobs/drops on Y):
  idle, walk, run, stun  (looping)
  telegraph, attack, hit, death (one-shot; death holds its last pose)
"""

from __future__ import annotations

import math
from typing import Any

import bpy
from mathutils import Euler, Vector

from . import rigkit

ARMATURE_NAME = "Skeleton"

CLIPS = [
    # name, loop
    ("idle", True),
    ("walk", True),
    ("run", True),
    ("stun", True),
    ("telegraph", False),
    ("attack", False),
    ("hit", False),
    ("death", False),
]


def _smooth(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def _pulse(u: float, peak: float) -> float:
    """0 at u=0, 1 at u=peak, back to 0 at u=1 (smooth)."""
    if u <= peak:
        return _smooth(u / peak) if peak > 0 else 1.0
    return 1.0 - _smooth((u - peak) / (1.0 - peak))


def _clip_frames(clip: str, freq: float) -> int:
    walk = max(8, round(rigkit.FPS / freq))
    return {
        "idle": 60,
        "walk": walk,
        "run": max(6, round(walk / 1.6)),
        "stun": 24,
        "telegraph": 18,
        "attack": 15,
        "hit": 8,
        "death": 30,
    }[clip]


def _pose(clip: str, frame: int, n: int, branch: dict[str, Any], k: int, g: dict[str, Any]) -> tuple[float, float]:
    """(rx, rz) in radians for bone k of a branch."""
    role = branch["gait_role"]
    phase = branch["gait_phase_rad"]
    amp = math.radians(g["amplitude_deg"])
    lag = g["chain_lag_rad"]
    u = frame / n
    th = 2 * math.pi * u
    first = 1.0 if k == 0 else 0.5
    if clip in ("walk", "run"):
        a = amp * (1.5 if clip == "run" else 1.0)
        if role == "locomotor":
            return a * first * math.sin(th + phase - k * lag), 0.0
        if role == "manipulator":
            return 0.4 * a * first * math.sin(th + phase - k * lag), 0.0
        if role == "sway":
            return 0.5 * a * math.sin(th - k * lag), 0.3 * a * math.cos(th - k * lag)
        if role == "head":
            return 0.15 * a * math.sin(2 * th), 0.0
        if role == "core":
            return 0.08 * a * math.sin(2 * th + k * lag), 0.05 * a * math.sin(th)
        return 0.0, 0.0
    if clip == "idle":
        if role == "sway":
            return 0.3 * amp * math.sin(th - k * lag), 0.2 * amp * math.cos(th - k * lag)
        if role == "core":
            return math.radians(2.0) * math.sin(th + k * 0.4), 0.0
        if role == "head":
            return math.radians(4.0) * math.sin(th + 1.0), math.radians(6.0) * math.sin(0.5 * th)
        if role == "manipulator":
            return math.radians(3.0) * math.sin(th + phase), 0.0
        if role == "locomotor":
            return math.radians(1.5) * math.sin(th + phase), 0.0
        return 0.0, 0.0
    if clip == "stun":
        j = math.radians(5.0)
        return j * math.sin(7 * th + 1.7 * k + phase), j * math.sin(5 * th + 2.3 * k)
    if clip == "telegraph":
        s = _smooth(u / 0.8)
        return {
            "core": (math.radians(-8.0) * s, 0.0),
            "manipulator": (math.radians(-55.0 if k == 0 else -15.0) * s, 0.0),
            "head": (math.radians(-18.0) * s, 0.0),
            "sway": (0.6 * amp * s * math.sin(th * 2 - k * lag), 0.0),
            "locomotor": (math.radians(4.0) * s, 0.0),
        }.get(role, (0.0, 0.0))
    if clip == "attack":
        p = _pulse(u, 0.4)
        return {
            "core": (math.radians(10.0) * p, 0.0),
            "manipulator": (math.radians(70.0 if k == 0 else 25.0) * p, 0.0),
            "head": (math.radians(25.0) * p, 0.0),
            "sway": (math.radians(35.0) * p * math.cos(k * lag), 0.0),
            "locomotor": (math.radians(-6.0) * p, 0.0),
        }.get(role, (0.0, 0.0))
    if clip == "hit":
        p = math.sin(math.pi * u)
        return {
            "core": (math.radians(-12.0) * p, 0.0),
            "head": (math.radians(-20.0) * p, math.radians(10.0) * p),
            "manipulator": (math.radians(-15.0) * p, 0.0),
            "sway": (math.radians(-15.0) * p, 0.0),
        }.get(role, (0.0, 0.0))
    if clip == "death":
        s = _smooth(u)
        return {
            "locomotor": (math.radians(50.0 if k == 0 else -30.0) * s, 0.0),
            "manipulator": (math.radians(35.0) * s, 0.0),
            "sway": (math.radians(40.0) * s, math.radians(10.0) * s),
            "head": (math.radians(30.0) * s, math.radians(15.0) * s),
            "core": (math.radians(5.0) * s, 0.0),
        }.get(role, (0.0, 0.0))
    return 0.0, 0.0


def _root_y(clip: str, frame: int, n: int, g: dict[str, Any], drop: float) -> float:
    u = frame / n
    th = 2 * math.pi * u
    if clip == "walk":
        return g["bob_m"] * (0.5 - 0.5 * math.cos(2 * th))
    if clip == "run":
        return 2 * g["bob_m"] * (0.5 - 0.5 * math.cos(2 * th))
    if clip == "idle":
        return 0.005 * math.sin(th)
    if clip == "death":
        return -drop * _smooth(u)
    if clip == "hit":
        return -0.02 * math.sin(math.pi * u)
    return 0.0


def _bake_clips(obj: bpy.types.Object, skeleton: dict[str, Any], gait: dict[str, Any]) -> list[dict[str, Any]]:
    obj.animation_data_create()
    core = next(b for b in skeleton["branches"] if not b["parent_branch"])
    core_head_y = next(bn for bn in skeleton["bones"] if bn["name"] == core["bone_names"][0])["head_m"][1]
    drop = 0.55 * core_head_y
    out = []
    for clip, loop in CLIPS:
        n = _clip_frames(clip, gait["frequency_hz"])
        action = bpy.data.actions.new(clip)
        action.use_fake_user = True
        obj.animation_data.action = action
        root = obj.pose.bones["root"]
        for f in range(0, n + 1):
            for br in skeleton["branches"]:
                for k, name in enumerate(br["bone_names"]):
                    rx, rz = _pose(clip, f, n, br, k, gait)
                    pb = obj.pose.bones[name]
                    pb.rotation_quaternion = Euler((rx, 0.0, rz), "XYZ").to_quaternion()
                    pb.keyframe_insert("rotation_quaternion", frame=f, group=name)
            # root bone local Y points world-up (catalog root tail is +Y glTF), so bob on local Y only
            root.location = Vector((0.0, _root_y(clip, f, n, gait, drop), 0.0))
            root.keyframe_insert("location", frame=f, group="root")
            root.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            root.keyframe_insert("rotation_quaternion", frame=f, group="root")
        action.use_frame_range = True
        action.frame_start = 0
        action.frame_end = n
        action.use_cyclic = loop
        out.append({"name": clip, "loop": loop, "frames": n, "fps": rigkit.FPS})
    clear_pose(obj)
    return out


def clear_pose(obj: bpy.types.Object) -> None:
    """Leave the armature in its rest pose with no active action, so exported node transforms
    (which Unity uses as the bind pose) are the rest pose, never a sampled clip frame."""
    obj.animation_data.action = None
    for pb in obj.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()


def run(args: dict[str, Any]) -> dict[str, Any]:
    skeleton = args["skeleton"]
    gait = args["gait_profile"]
    rigkit.reset_scene()
    obj = rigkit.build_armature(ARMATURE_NAME, skeleton["bones"])
    clips = _bake_clips(obj, skeleton, gait)
    bpy.context.scene.frame_start = 0
    bpy.context.scene.frame_end = max(c["frames"] for c in clips)
    rigkit.export_fbx(args["out_fbx"], [obj], animated=True)
    if args.get("out_glb"):
        rigkit.export_glb(args["out_glb"], [obj], animated=True)
    if args.get("out_blend"):
        rigkit.save_blend(args["out_blend"])
    return {"skeleton_id": skeleton["skeleton_id"], "bones": len(obj.data.bones), "clips": clips}
