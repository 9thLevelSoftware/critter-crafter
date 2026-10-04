"""Rights-cleared, bone-only cc-motion-world-1 interchange (no numerical runtime)."""
from __future__ import annotations

import hashlib
import math
from typing import Any

from .jobs import canonical_bytes

CAPABILITIES = ("support", "strike", "bite", "whip", "grasp", "slide")
ROLES = ("root", "core", "locomotor", "manipulator", "puller", "slither", "detail")
CATEGORIES = ("idle", "stun", "telegraph_attack", "hit", "death")
TRAINING = {"seed": 1, "latent_dim": 128, "layers": 4, "diffusion_steps": 100,
            "noise_schedule": "cosine", "lambda_geo": 0.0, "lambda_fs": 0.0,
            "learning_rate": 0.0001, "effective_batch": 16, "microbatch": 2,
            "accumulation": 8, "optimizer_updates": 100000, "validation_every": 1000,
            "representation": "cc-motion-world-1", "fps": 30}


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def transpose(a):
    return [list(row) for row in zip(*a)]


def rotation6(matrix):
    return [matrix[i][j] for j in range(2) for i in range(3)]


def from_rotation6(values):
    def unit(v):
        length = math.sqrt(sum(x * x for x in v))
        if length < 1e-10 or not math.isfinite(length):
            raise ValueError("CC_MOTION_ROTATION: degenerate rotation6")
        return [x / length for x in v]
    x = unit(values[:3]); raw = values[3:6]
    dot = sum(a * b for a, b in zip(x, raw))
    y = unit([b - dot * a for a, b in zip(x, raw)])
    z = [x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0]]
    return [[x[i], y[i], z[i]] for i in range(3)]


def quaternion(matrix):
    trace = sum(matrix[i][i] for i in range(3))
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        q = [(matrix[2][1]-matrix[1][2])/s, (matrix[0][2]-matrix[2][0])/s,
             (matrix[1][0]-matrix[0][1])/s, .25*s]
    else:
        i = max(range(3), key=lambda k: matrix[k][k]); j = (i+1)%3; k = (i+2)%3
        s = math.sqrt(max(0., 1+matrix[i][i]-matrix[j][j]-matrix[k][k])) * 2
        if s < 1e-10:
            raise ValueError("CC_MOTION_ROTATION: invalid matrix")
        q = [0., 0., 0., (matrix[k][j]-matrix[j][k])/s]
        q[i] = .25*s; q[j] = (matrix[j][i]+matrix[i][j])/s; q[k] = (matrix[k][i]+matrix[i][k])/s
    norm = math.sqrt(sum(x*x for x in q))
    return [x/norm * (-1 if q[3] < 0 else 1) for x in q]


def quaternion_matrix(q):
    x,y,z,w = q
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]


def recovered_heads(sample, record):
    positions = []; rotations = []
    bind = record["bind_rotations"]; heads = record["bind_heads_m"]
    for j,name in enumerate(record["bone_names"]):
        parent = record["parents"][j]
        relative = bind[j] if parent < 0 else multiply(transpose(bind[parent]),bind[j])
        local = multiply(relative,quaternion_matrix(sample["rotations_xyzw"][name]))
        rotations.append(local if parent < 0 else multiply(rotations[parent],local))
        if parent < 0:
            positions.append([a+b for a,b in zip(heads[j],sample["root_position_m"])])
        else:
            offset = [heads[j][i]-heads[parent][i] for i in range(3)]
            local_offset = [sum(transpose(bind[parent])[i][k]*offset[k] for k in range(3)) for i in range(3)]
            positions.append([positions[parent][i]+sum(rotations[parent][i][k]*local_offset[k] for k in range(3)) for i in range(3)])
    return dict(zip(record["bone_names"],positions))


def parent_order(skeleton):
    bones = {b["name"]: b for b in skeleton["bones"]}
    ordered = []
    def visit(name, stack):
        if name in ordered:
            return
        if name in stack:
            raise ValueError("CC_MOTION_TOPOLOGY: cyclic parents")
        parent = bones[name].get("parent")
        if parent:
            if parent not in bones:
                raise ValueError("CC_MOTION_TOPOLOGY: absent parent")
            visit(parent, stack + [name])
        ordered.append(name)
    for bone in skeleton["bones"]:
        visit(bone["name"], [])
    if not ordered or ordered[0] != "root":
        raise ValueError("CC_MOTION_TOPOLOGY: root must be first")
    return ordered


def role_vectors(skeleton, names):
    by_name = {}
    for branch in skeleton["branches"]:
        role = branch.get("gait_role", branch.get("gait", {}).get("role", "detail"))
        for name in branch["bone_names"]:
            vector = [0.] * 768
            for capability in branch.get("capabilities", []):
                vector[CAPABILITIES.index(capability)] = 1.
            vector[6 + ROLES.index(role if role in ROLES else "detail")] = 1.
            by_name[name] = vector
    root = [0.] * 768; root[6] = 1.; by_name["root"] = root
    return [by_name.get(name, [0.] * 768) for name in names]


def lineage_split(skeleton):
    anatomy = skeleton.get("anatomy", {})
    archetype = anatomy.get("archetype_id", skeleton.get("family", ""))
    seed = skeleton.get("provenance", {}).get("seed", anatomy.get("seed", skeleton.get("seed")))
    if anatomy.get("origin") == "amalgam" or str(archetype).startswith("amalgam") or skeleton.get("family") == "amalgam":
        if seed is None:
            import re
            match = re.search(r"_s(\d+)_", skeleton["skeleton_id"])
            if not match:
                raise ValueError("CC_MOTION_LINEAGE: amalgam seed missing")
            seed = int(match.group(1))
        return "held_out" if int(seed) % 5 == 0 else "train", "amalgam:" + str(seed)
    held = "radial" in str(archetype) or "serpent" in str(archetype)
    return "held_out" if held else "train", str(archetype)


def canonical_world_samples(samples, names, parents, bind):
    result = []
    for sample in samples:
        source = {bone["name"]:bone for bone in sample["bones"]}
        world = []
        for joint,name in enumerate(names):
            q = source[name]["rotation_xyzw"]
            norm = math.sqrt(sum(value*value for value in q))
            if len(q) != 4 or not math.isfinite(norm) or norm < 1e-12:
                raise ValueError("CC_MOTION_ROTATION: invalid evaluated local quaternion")
            delta = quaternion_matrix([value/norm for value in q])
            parent = parents[joint]
            relative = bind[joint] if parent < 0 else multiply(transpose(bind[parent]),bind[joint])
            local = multiply(relative,delta)
            world.append(local if parent < 0 else multiply(world[parent],local))
        result.append(dict(sample,bones=[dict(source[name],world_rotation=world[joint])
                                        for joint,name in enumerate(names)]))
    return result


def encode_frames(samples, names, height, contact_bones):
    frames = []; previous = None
    for sample in samples:
        bones = {b["name"]: b for b in sample["bones"]}
        root = sample["root_position_m"]
        positions = [[(bones[n]["head_m"][i] - (root[i] if i != 1 else 0)) / height
                      for i in range(3)] for n in names]
        active = {contact_bones[c["contact_id"]] for c in sample["contacts"] if c["planted"]}
        rows = []
        for j, name in enumerate(names):
            velocity = [0., 0., 0.] if previous is None else [(positions[j][i]-previous[j][i])*30 for i in range(3)]
            rows.append(positions[j] + rotation6(bones[name]["world_rotation"]) + velocity + [float(name in active)])
        if not all(math.isfinite(v) for row in rows for v in row):
            raise ValueError("CC_MOTION_NONFINITE")
        frames.append(rows); previous = positions
    return frames


def decode_frames(features, record):
    names = record["bone_names"]; parents = record["parents"]
    height = record["body_height_m"]; bind = record["bind_rotations"]
    result = []
    for frame, rows in enumerate(features):
        if len(rows) != len(names) or any(len(row) != 13 or not all(math.isfinite(x) for x in row) for row in rows):
            raise ValueError("CC_MOTION_SHAPE: expected [T,J,13]")
        worlds = [from_rotation6(row[3:9]) for row in rows]
        rotations = {}
        for j, name in enumerate(names):
            parent = parents[j]
            rest_relative = bind[j] if parent < 0 else multiply(transpose(bind[parent]), bind[j])
            local = worlds[j] if parent < 0 else multiply(transpose(worlds[parent]), worlds[j])
            rotations[name] = quaternion(multiply(transpose(rest_relative), local))
        baseline = record["root_positions_m"][frame]
        result.append({"frame": frame, "rotations_xyzw": rotations,
                       "root_position_m": [baseline[0]-record["root_bind_head_m"][0],
                                           rows[0][1]*height-record["root_bind_head_m"][1],
                                           baseline[2]-record["root_bind_head_m"][2]]})
    return result


def make_record(artifact, category):
    if artifact.get("sample_source") != "evaluated_blender" or artifact.get("fps") != 30:
        raise ValueError("CC_MOTION_DATASET: evaluated Blender 30Hz data required")
    skeleton = artifact["skeleton_snapshot"]; names = parent_order(skeleton)
    bones = {b["name"]: b for b in skeleton["bones"]}
    parents = [names.index(bones[n]["parent"]) if bones[n].get("parent") else -1 for n in names]
    contact_bones = {f"{branch['branch_id']}:{index}":branch["bone_names"][contact["bone_index"]]
                     for branch in skeleton["branches"] for index,contact in enumerate(branch.get("contacts", []))}
    if any(artifact["contact_bones"].get(key) != value for key,value in contact_bones.items()):
        raise ValueError("CC_MOTION_TOPOLOGY: contact-to-joint correspondence changed")
    clips = {clip["name"]: clip for clip in artifact["clips"]}
    selected = [clips[n] for n in (("telegraph", "attack") if category == "telegraph_attack" else (category,))]
    samples = list(selected[0]["samples"])
    if len(selected) == 2:
        samples += selected[1]["samples"][1:]
    height = float(skeleton["anatomy"]["silhouette"]["height_m"])
    if height <= 0:
        raise ValueError("CC_MOTION_HEIGHT")
    bind = [from_rotation6(rotation6(artifact["bind_rotations"][name])) for name in names]
    # Evaluated local deltas are authoritative; independently orthogonalizing raw
    # Blender world bases would introduce hierarchy-inconsistent rotation drift.
    world_samples = canonical_world_samples(samples,names,parents,bind)
    features = encode_frames(world_samples, names, height, contact_bones)
    supports = set(skeleton["anatomy"]["support_branches"])
    neutral_sample = dict(artifact["neutral_world_sample"], contacts=[
        {"contact_id":f"{branch['branch_id']}:{index}","planted":branch["branch_id"] in supports}
        for branch in skeleton["branches"] for index,_ in enumerate(branch.get("contacts", []))])
    neutral_world = canonical_world_samples([neutral_sample],names,parents,bind)
    neutral = encode_frames(neutral_world, names, height, contact_bones)[0]
    split, lineage = lineage_split({**skeleton, "provenance": artifact.get("source_provenance", {})})
    record = {"representation": "cc-motion-world-1", "skeleton_id": skeleton["skeleton_id"],
              "source_fingerprint": artifact["source_fingerprint"], "category": category,
              "split": split, "lineage": lineage, "body_height_m": height, "fps": 30,
              "bone_names": names, "parents": parents,
              "bind_rotations": bind,
              "bind_heads_m": [bones[n]["head_m"] for n in names],
              "root_bind_head_m": bones["root"]["head_m"], "role_vectors": role_vectors(skeleton, names),
              "neutral_features": neutral, "features": features,
              "root_positions_m": [sample["root_position_m"] for sample in samples],
              "contact_schedules": [sample["contacts"] for sample in samples],
              "contact_bones": contact_bones, "clips": [{k: v for k,v in clip.items() if k != "samples"} for clip in selected],
              "seam_frame": len(selected[0]["samples"])-1 if len(selected) == 2 else None,
              "authorship": artifact.get("authorship", "project_procedural")}
    decoded = decode_frames(features, record)
    for source, recovered in zip(samples, decoded):
        positions = recovered_heads(recovered,record)
        for bone in source["bones"]:
            a = bone["rotation_xyzw"]; b = recovered["rotations_xyzw"][bone["name"]]
            error = min(max(abs(x-y) for x,y in zip(a,b)), max(abs(x+y) for x,y in zip(a,b)))
            if error > 1e-5:
                raise ValueError("CC_MOTION_ROUNDTRIP: local bind-relative rotation")
            if max(abs(a-b) for a,b in zip(bone["head_m"],positions[bone["name"]])) > 1e-5:
                raise ValueError("CC_MOTION_ROUNDTRIP: fixed-rest joint position")
        expected = source["root_position_m"][1]-bones["root"]["head_m"][1]
        if abs(recovered["root_position_m"][1]-expected) > 1e-5:
            raise ValueError("CC_MOTION_ROUNDTRIP: root translation")
    record["record_sha256"] = hashlib.sha256(canonical_bytes(record)).hexdigest()
    return record
