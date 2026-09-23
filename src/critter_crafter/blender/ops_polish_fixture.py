"""Create authored and deliberately incompatible polish masters for integration tests."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import bpy
from mathutils import Quaternion

from .ops_skeleton import ARMATURE_NAME


def _open(path: Path) -> bpy.types.Object:
    bpy.ops.wm.open_mainfile(filepath=str(path.resolve()))
    arm = bpy.data.objects.get(ARMATURE_NAME)
    if arm is None or arm.type != "ARMATURE":
        raise ValueError(f"fixture input lacks armature {ARMATURE_NAME!r}")
    return arm


def _save(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path.resolve()), check_existing=False)


def _authored_keyframe(
    source: Path, output: Path, action_name: str, bone_name: str, frame: int, angle_deg: float
) -> dict[str, Any]:
    arm = _open(source)
    action = bpy.data.actions.get(action_name)
    if action is None:
        raise ValueError(f"fixture input lacks action {action_name!r}")
    bone = arm.pose.bones.get(bone_name)
    if bone is None:
        raise ValueError(f"fixture input lacks pose bone {bone_name!r}")
    arm.animation_data_create()
    arm.animation_data.action = action
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    before = bone.rotation_quaternion.copy()
    bone.rotation_mode = "QUATERNION"
    bone.rotation_quaternion = (before @ Quaternion((0.0, 0.0, 1.0), math.radians(angle_deg))).normalized()
    after = bone.rotation_quaternion.copy()
    bone.keyframe_insert("rotation_quaternion", frame=frame, group=bone_name)
    bpy.context.view_layer.update()
    _save(output)
    return {
        "before_wxyz": [float(value) for value in before],
        "after_wxyz": [float(value) for value in after],
    }


def _incompatible_rest(source: Path, output: Path, bone_name: str) -> None:
    arm = _open(source)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bone = arm.data.edit_bones.get(bone_name)
    if bone is None:
        raise ValueError(f"fixture input lacks edit bone {bone_name!r}")
    bone.roll += math.radians(7.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    _save(output)


def _incompatible_source(source: Path, output: Path) -> None:
    arm = _open(source)
    arm["cc_source_fingerprint"] = "0" * 64
    _save(output)


def run(args: dict[str, Any]) -> dict[str, Any]:
    source = Path(args["input_blend"])
    authored = Path(args["authored_blend"])
    bad_rest = Path(args["bad_rest_blend"])
    bad_source = Path(args["bad_source_blend"])
    edit = _authored_keyframe(
        source,
        authored,
        str(args["action_name"]),
        str(args["bone_name"]),
        int(args["frame"]),
        float(args.get("angle_deg", 3.0)),
    )
    _incompatible_rest(source, bad_rest, str(args["bone_name"]))
    _incompatible_source(source, bad_source)
    return {
        "authored_blend": str(authored),
        "bad_rest_blend": str(bad_rest),
        "bad_source_blend": str(bad_source),
        "frame": int(args["frame"]),
        "bone_name": str(args["bone_name"]),
        **edit,
    }
