"""Neutral-pose forward kinematics over a compiled skeleton (catalog frame, metres).

Bone basis: +Y along the bone (head -> tail), +Z toward ``up_m``, +X = Y x Z.  Neutral
rotations are local deltas in that basis, applied on top of the straight rest hierarchy;
this matches ``SkeletonPose.ApplyNeutralPose`` in the Unity package.
"""

from __future__ import annotations

from typing import Any, Sequence

from .. import mathutil as mu

Quat = tuple[float, float, float, float]


def qmul(a: Sequence[float], b: Sequence[float]) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qinv(q: Sequence[float]) -> Quat:
    return (-q[0], -q[1], -q[2], q[3])


def rest_basis(bone: dict[str, Any]) -> Quat:
    y = mu.normalize(mu.sub(bone["tail_m"], bone["head_m"]))
    z = mu.normalize(mu.sub(bone["up_m"], mu.scale(y, mu.dot(bone["up_m"], y))))
    return mu.quat_from_basis(mu.cross(y, z), y, z)


def neutral_pose_world(skeleton: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """World head/tail/rotation of every bone after the neutral pose and root offset."""
    deltas = {r["bone_name"]: tuple(r["rotation_xyzw"])
              for r in skeleton.get("neutral_pose", {}).get("rotations", [])}
    offset = skeleton.get("neutral_pose", {}).get("root_offset_m", [0.0, 0.0, 0.0])
    rest = {b["name"]: rest_basis(b) for b in skeleton["bones"]}
    by_name = {b["name"]: b for b in skeleton["bones"]}
    out: dict[str, dict[str, Any]] = {}
    for bone in skeleton["bones"]:  # compiled order is parent-first
        name, parent = bone["name"], bone.get("parent") or ""
        delta = deltas.get(name, (0.0, 0.0, 0.0, 1.0))
        length = mu.length(mu.sub(bone["tail_m"], bone["head_m"]))
        if not parent:
            rotation = qmul(rest[name], delta)
            head = mu.add(bone["head_m"], offset)
        else:
            p = out[parent]
            local_rest = qmul(qinv(rest[parent]), rest[name])
            rotation = qmul(qmul(p["rotation"], local_rest), delta)
            local_head = mu.quat_rotate(qinv(rest[parent]), mu.sub(bone["head_m"], by_name[parent]["head_m"]))
            head = mu.add(p["head"], mu.quat_rotate(p["rotation"], local_head))
        tail = mu.add(head, mu.quat_rotate(rotation, (0.0, length, 0.0)))
        out[name] = {"head": head, "tail": tail, "rotation": rotation, "length": length}
    return out


def contact_world(pose: dict[str, dict[str, Any]], branch: dict[str, Any], contact: dict[str, Any]) -> mu.Vec:
    bone = pose[branch["bone_names"][int(contact["bone_index"])]]
    return mu.add(bone["head"], mu.quat_rotate(bone["rotation"], contact["local_point_m"]))
