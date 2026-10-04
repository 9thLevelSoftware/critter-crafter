"""Weight-only adoption with immutable canonical geometry and profile binds."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..authoring.jobs import canonical_bytes, resolve_artifact, sha256_file


def geometry_inputs(part, profile, producer_sha256):
    fit = {key: value for key, value in part["real"]["fit"].items() if key != "weights"}
    return {"part_id": part["part_id"], "source_sha256": part["real"]["sha256"],
            "fit": fit, "profile": profile, "template": part["template"],
            "length_m": part["length_m"], "girth_m": part["girth_m"],
            "max_triangles": part["max_triangles"], "producer_sha256": producer_sha256}


def geometry_producer_sha256():
    from .. import mathutil
    from ..blender import frame
    from . import fit
    base = Path(__file__).resolve().parents[1]
    files = [Path(fit.__file__), base / "blender" / "ops_realpart.py", base / "blender" / "rigkit.py",
             Path(frame.__file__), Path(mathutil.__file__), Path(__file__)]
    return hashlib.sha256(canonical_bytes({p.name: sha256_file(p) for p in files})).hexdigest()


def geometry_identity(inputs, geometry):
    return hashlib.sha256(canonical_bytes({"inputs": inputs, "geometry": geometry})).hexdigest()


def vertex_sha256(vertices):
    return hashlib.sha256(canonical_bytes(vertices)).hexdigest()


def validate_csr(path, geometry, *, out_path=None):
    """Called only inside Blender. Reject changed columns/indices before stable top-four pruning."""
    import numpy as np

    try:
        with np.load(path, allow_pickle=False) as archive:
            required = {"indptr", "bone_indices", "weights", "bone_ids", "vertex_sha256"}
            if set(archive.files) != required:
                raise ValueError("CC_SKIN_WEIGHTS: unsupported CSR fields")
            ptr, indices, values = (archive[k].copy() for k in ("indptr", "bone_indices", "weights"))
            names = archive["bone_ids"].tolist()
            vh = archive["vertex_sha256"].item()
    except (OSError, KeyError, TypeError) as error:
        raise ValueError(f"CC_SKIN_WEIGHTS: unreadable CSR: {error}") from error
    expected = [bone["name"] for bone in geometry["bones"]]
    if names != expected:
        raise ValueError("CC_SKIN_BIND: bone columns do not match original profile order")
    if vh != vertex_sha256(geometry["vertices"]):
        raise ValueError("CC_SKIN_STALE: canonical vertex hash changed")
    n = len(geometry["vertices"])
    if ptr.dtype.kind not in "iu" or indices.dtype.kind not in "iu" or ptr.shape != (n + 1,):
        raise ValueError("CC_SKIN_WEIGHTS: invalid CSR index arrays")
    if indices.ndim != 1 or values.ndim != 1 or len(indices) != len(values):
        raise ValueError("CC_SKIN_WEIGHTS: invalid CSR data shape")
    if ptr[0] != 0 or ptr[-1] != len(values) or np.any(np.diff(ptr) <= 0):
        raise ValueError("CC_SKIN_WEIGHTS: empty row or invalid CSR offsets")
    if np.any(indices >= len(expected)) or np.any(indices < 0):
        raise ValueError("CC_SKIN_TOPOLOGY: unknown bone column")
    if values.dtype.kind not in "fiu" or not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError("CC_SKIN_WEIGHTS: negative or nonfinite influence")
    rows, out_ptr, out_indices, out_values = [], [0], [], []
    for start, end in zip(ptr[:-1], ptr[1:]):
        pairs = [(int(i), float(w)) for i, w in zip(indices[start:end], values[start:end])]
        if len({i for i, _ in pairs}) != len(pairs):
            raise ValueError("CC_SKIN_WEIGHTS: duplicate bone column in row")
        pairs = sorted((p for p in pairs if p[1] > 0), key=lambda p: (-p[1], p[0]))[:4]
        total = sum(w for _, w in pairs)
        if not total > 0:
            raise ValueError("CC_SKIN_WEIGHTS: zero-weight vertex")
        row = {expected[i]: w / total for i, w in pairs}
        rows.append(row)
        for i, w in pairs:
            out_indices.append(i)
            out_values.append(w / total)
        out_ptr.append(len(out_values))
    if out_path:
        np.savez(out_path, indptr=np.asarray(out_ptr, dtype=np.int64),
                 bone_indices=np.asarray(out_indices, dtype=np.int64),
                 weights=np.asarray(out_values, dtype=np.float64), bone_ids=np.asarray(expected),
                 vertex_sha256=np.asarray(vh))
    return rows


def resolve_weights(part, root):
    metadata = part.get("real", {}).get("learned_weights")
    if not metadata:
        raise ValueError("CC_SKIN_MODEL_UNAVAILABLE: no learned artifact selected")
    path = resolve_artifact(root, metadata["artifact_path"])
    if not path.is_file():
        raise ValueError("CC_SKIN_MODEL_UNAVAILABLE: learned artifact missing")
    if sha256_file(path) != metadata["sha256"]:
        raise ValueError("CC_SKIN_STALE: learned weight artifact changed")
    return path
