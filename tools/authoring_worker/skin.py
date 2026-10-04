"""Weight-only adapters for the inspected official SkinTokens and UniRig APIs.

SkinTokens source: 273b691d35989d71cd17ff2895fdc735097b92d1.
No upstream transfer/export helper is called; decoded joints are never adopted.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from critter_crafter.authoring.jobs import (artifact, canonical_bytes, load_job, resolve_artifact,
                                          sha256_file, write_json, write_result)
from critter_crafter.parts.learned_weights import geometry_identity, vertex_sha256


def _root(backend):
    variable = "CRITTER_SKINTOKENS_ROOT" if backend == "skintokens" else "CRITTER_UNIRIG_ROOT"
    value = os.environ.get(variable)
    if not value or not (Path(value) / "src").is_dir():
        raise RuntimeError("CC_SKIN_MODEL_UNAVAILABLE: set " + variable + " to the owner-installed official checkout")
    root = Path(value).resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    expected = ("273b691d35989d71cd17ff2895fdc735097b92d1" if backend == "skintokens"
                else "6793c6640ff01c8fb389f3993434124bb43d2933")
    if revision != expected:
        raise RuntimeError("CC_SKIN_MODEL_UNAVAILABLE: adapter requires inspected upstream revision " + expected)
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, text=True).strip():
        raise RuntimeError("CC_SKIN_MODEL_UNAVAILABLE: upstream source has unrecorded modifications")
    return root, revision


def _checkpoint(path):
    if not path.is_file():
        raise RuntimeError("CC_SKIN_MODEL_UNAVAILABLE: missing official checkpoint " + str(path))
    return sha256_file(path)


def _triangles(geometry):
    return [[face[0], face[i], face[i + 1]] for face in geometry["faces"] for i in range(1, len(face) - 1)]


def _skintokens(geometry, root, checkpoint, vae_checkpoint, options):
    import numpy as np
    import torch
    from src.server.spec import get_model
    from src.data.transform import Transform
    from src.rig_package.info.asset import Asset
    from src.tokenizer.spec import TokenizeInput

    # Official TokenRig loads its FSQ-CVAE using the path stored in its model config.
    model = get_model(str(checkpoint), device="cuda").eval()
    configured_vae = Path(model.model_config["pretrained_vae"]).resolve()
    if configured_vae != vae_checkpoint.resolve():
        raise ValueError("CC_SKIN_MODEL_UNAVAILABLE: --vae-checkpoint must match TokenRig pretrained_vae")
    vertices = np.asarray(geometry["vertices"], dtype=np.float64)
    names = [bone["name"] for bone in geometry["bones"]]
    parents = np.asarray([names.index(bone["parent"]) if bone["parent"] else -1
                          for bone in geometry["bones"]], dtype=np.int64)
    heads = np.asarray([bone["head_m"] for bone in geometry["bones"]], dtype=np.float64)
    asset = Asset.from_data(vertices=vertices.copy(), faces=np.asarray(_triangles(geometry), dtype=np.int64),
                            joint_names=names.copy(), parents=parents.copy(), joints=heads.copy())
    asset.build_normals()
    # Observe every upstream affine before sampling, instead of guessing normalization/heading.
    affine = np.eye(4)
    original_transform = asset.transform
    def capture(trans):
        nonlocal affine
        affine = np.asarray(trans) @ affine
        original_transform(trans)
    asset.transform = capture
    transform = Transform.parse(**model.transform_config["predict_transform"])
    # Skin vertex groups are training targets, not required prediction inputs.
    transform.vertex_groups = None
    transform.apply(asset)
    if len(asset.vertices) != len(vertices) or not np.array_equal(asset.faces, np.asarray(_triangles(geometry))):
        raise ValueError("CC_SKIN_TOPOLOGY: preprocessing changed canonical mesh topology")
    permutation = [names.index(name) for name in asset.joint_names]
    if sorted(permutation) != list(range(len(names))):
        raise ValueError("CC_SKIN_BIND: nonbijective upstream bone permutation")
    expected_parents = [(-1 if parents[i] < 0 else permutation.index(int(parents[i]))) for i in permutation]
    if asset.parents.tolist() != expected_parents:
        raise ValueError("CC_SKIN_TOPOLOGY: preprocessing changed profile hierarchy")
    inv = np.linalg.inv(affine)
    recovered = asset.vertices @ inv[:3, :3].T + inv[:3, 3]
    if not np.allclose(recovered, vertices, atol=1e-5, rtol=0):
        raise ValueError("CC_SKIN_BIND: preprocessing is not invertible affine on canonical vertices")
    tokens = model.tokenizer.tokenize(TokenizeInput(joints=asset.joints, parents=asset.parents,
                                                   cls=asset.cls, joint_names=asset.joint_names))
    supplied = model.tokenizer.detokenize(tokens)
    if supplied.parents != expected_parents or supplied.J != len(names):
        raise ValueError("CC_SKIN_TOPOLOGY: supplied skeleton tokens do not preserve hierarchy")
    sampling = {key: options[key] for key in ("top_k", "top_p", "temperature", "repetition_penalty", "num_beams", "max_length")}
    with torch.inference_mode():
        prediction = model.generate(vertices=torch.as_tensor(asset.sampled_vertices, dtype=torch.float32, device="cuda"),
                                    normals=torch.as_tensor(asset.sampled_normals, dtype=torch.float32, device="cuda"),
                                    skeleton_tokens=tokens, do_sample=True, num_return_sequences=1, **sampling)
    if prediction.skin_pred is None or prediction.detokenize_output is None:
        raise ValueError("CC_SKIN_WEIGHTS: incomplete model prediction")
    output_tokens = prediction.output_ids.detach().cpu().numpy()
    if not np.array_equal(output_tokens[:len(tokens)], tokens):
        raise ValueError("CC_SKIN_TOPOLOGY: model changed supplied skeleton token prefix")
    if prediction.detokenize_output.parents != supplied.parents:
        raise ValueError("CC_SKIN_TOPOLOGY: decoded token topology changed")
    sampled_skin = prediction.skin_pred.detach().float().cpu().numpy()
    if sampled_skin.shape != (len(asset.sampled_vertices), len(names)):
        raise ValueError("CC_SKIN_BIND: unknown predicted skin columns")
    # Invert sampled coordinates before interpolation onto ORIGINAL canonical vertex indices.
    samples = asset.sampled_vertices @ inv[:3, :3].T + inv[:3, 3]
    dense = Asset.from_data(vertices=vertices, sampled_vertices=samples, sampled_skin=sampled_skin).skin
    weights = np.empty_like(dense)
    for column, original in enumerate(permutation):
        weights[:, original] = dense[:, column]
    return weights, {"canonical_to_model": affine.tolist(), "model_to_canonical": inv.tolist(),
                     "model_column_to_profile": permutation, "skeleton_tokens": tokens.tolist(),
                     "sampling": sampling, "predict_transform": model.transform_config["predict_transform"]}


def _unirig(geometry, root, checkpoint, options):
    import numpy as np
    import torch
    from omegaconf import OmegaConf
    from scipy.spatial import cKDTree
    import trimesh
    from src.model.parse import get_model
    from src.data.asset import Asset
    from src.data.transform import TransformConfig, transform_asset

    config_path = root / "configs/model/unirig_skin.yaml"
    config = OmegaConf.to_container(OmegaConf.load(config_path), resolve=True)
    model = get_model(**config)
    state = torch.load(checkpoint, map_location="cpu", weights_only=False)["state_dict"]
    model.load_state_dict({key[len("model."):]: value for key, value in state.items() if key.startswith("model.")}, strict=True)
    model = model.cuda().eval()
    original = np.asarray(geometry["vertices"], dtype=np.float64)
    names = [b["name"] for b in geometry["bones"]]
    parents = [names.index(b["parent"]) if b["parent"] else None for b in geometry["bones"]]
    faces = np.asarray(_triangles(geometry), dtype=np.int64)
    mesh = trimesh.Trimesh(vertices=original, faces=faces, process=False)
    asset = Asset(cls=None, path="", data_name="", vertices=original.copy(), faces=faces.copy(),
                  vertex_normals=mesh.vertex_normals.copy(), face_normals=mesh.face_normals.copy(),
                  joints=np.asarray([b["head_m"] for b in geometry["bones"]]),
                  tails=np.asarray([b["tail_m"] for b in geometry["bones"]]), parents=parents.copy(), names=names.copy())
    transform_path = root / "configs/transform/inference_skin_transform.yaml"
    config = OmegaConf.load(transform_path).predict_transform_config
    first, second = transform_asset(asset, TransformConfig.parse(config))
    permutation = [names.index(name) for name in asset.names]
    if sorted(permutation) != list(range(len(names))):
        raise ValueError("CC_SKIN_BIND: UniRig nonbijective bone permutation")
    expected_parents = [None if parents[i] is None else permutation.index(parents[i]) for i in permutation]
    if asset.parents != expected_parents or not np.array_equal(asset.faces, faces):
        raise ValueError("CC_SKIN_TOPOLOGY: UniRig preprocessing changed topology")
    affine = np.eye(4)
    for augment in first + second:
        if not hasattr(augment, "trans_vertex"):
            raise ValueError("CC_SKIN_BIND: unsupported non-affine UniRig preprocessing")
        affine = augment.trans_vertex @ affine
    inverse = np.linalg.inv(affine)
    recovered = asset.vertices @ inverse[:3, :3].T + inverse[:3, 3]
    if not np.allclose(recovered, original, atol=1e-5, rtol=0):
        raise ValueError("CC_SKIN_BIND: UniRig preprocessing changed canonical vertex indexing")
    samples = asset.sampled_vertices
    voxel = asset.sampled_vertex_groups["voxel_skin"]
    tensor = lambda value, dtype=torch.float32: torch.as_tensor(value, dtype=dtype, device="cuda")
    batch = {"vertices": tensor(samples)[None], "normals": tensor(asset.sampled_normals)[None],
             "joints": tensor(asset.joints)[None], "tails": tensor(asset.tails)[None],
             "parents": tensor([-1 if p is None else p for p in asset.parents], torch.long)[None],
             "num_bones": tensor([len(names)], torch.long), "voxel_skin": tensor(voxel)[None],
             "offset": [len(samples)]}
    with torch.inference_mode():
        weights, indices = model._get_predict(batch)
    indices = indices.cpu().numpy()
    if sorted(indices.tolist()) != list(range(len(samples))):
        raise ValueError("CC_SKIN_TOPOLOGY: UniRig changed sampled vertex indexing")
    predicted = np.empty((len(samples), len(names)), dtype=np.float32)
    predicted[indices] = weights[0].float().cpu().numpy()
    canonical_samples = samples @ inverse[:3, :3].T + inverse[:3, 3]
    distances, nearest = cKDTree(canonical_samples).query(original, k=min(8, len(samples)))
    interpolation = 1.0 / (distances + 1e-8)
    interpolation /= interpolation.sum(axis=1, keepdims=True)
    interpolated = np.einsum("nk,nkj->nj", interpolation, predicted[nearest])
    dense = np.empty_like(interpolated)
    for column, original_column in enumerate(permutation):
        dense[:, original_column] = interpolated[:, column]
    return dense, {"canonical_to_model": affine.tolist(), "model_to_canonical": inverse.tolist(),
                   "model_column_to_profile": permutation, "model_config_sha256": sha256_file(config_path),
                   "predict_transform_sha256": sha256_file(transform_path)}


def run(args):
    job_root, out = Path(args.job).resolve(), Path(args.out).resolve()
    if out == job_root or job_root in out.parents:
        raise ValueError("CC_AUTHORING_PATH: result must be outside the input job directory")
    job = load_job(job_root, "skin")
    backend = job["options"]["backend"]
    root, revision = _root(backend)
    if out.exists() and any(out.iterdir()):
        raise ValueError("CC_AUTHORING_RESULT: output must be empty")
    checkpoint = Path(args.checkpoint or (root / ("experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt"
                      if backend == "skintokens" else "experiments/skin/articulation-xl/model.ckpt"))).resolve()
    checkpoint_hash = _checkpoint(checkpoint)
    if backend == "skintokens" and checkpoint_hash != "f4e4706a11cfb520cdde65156a0358545e4fbf8f36237aca01ea5e79d5cb5692":
        raise RuntimeError("CC_SKIN_MODEL_UNAVAILABLE: TokenRig checkpoint is not the inspected official MIT artifact")
    vae = Path(args.vae_checkpoint or root / "experiments/skin_vae_2_10_32768/last.ckpt").resolve()
    # Check missing prerequisites before importing large model packages or beginning work.
    if backend == "skintokens":
        if sys.version_info[:2] != (3, 11):
            raise RuntimeError("CC_SKIN_MODEL_UNAVAILABLE: SkinTokens worker requires Python 3.11")
        vae_hash = _checkpoint(vae)
        if vae_hash != "4843f49e58afff88345806b94ca82e6cc9d8def6e7432e2853c677b154de0ed4":
            raise RuntimeError("CC_SKIN_MODEL_UNAVAILABLE: FSQ-CVAE checkpoint is not the inspected official MIT artifact")
    else:
        vae_hash = None
    import numpy as np
    import torch
    if not torch.cuda.is_available() or torch.cuda.get_device_properties(0).total_memory < 16 * 1000**3:
        raise RuntimeError("CC_SKIN_MODEL_UNAVAILABLE: owner-managed CUDA GPU with at least 16GB required")
    if backend == "skintokens" and (torch.__version__.split("+")[0] != "2.7.0" or torch.version.cuda != "12.8"):
        raise RuntimeError("CC_SKIN_MODEL_UNAVAILABLE: use documented torch2.7.0/cu128 environment")
    random.seed(job["options"]["seed"])
    np.random.seed(job["options"]["seed"])
    torch.manual_seed(job["options"]["seed"])
    torch.cuda.manual_seed_all(job["options"]["seed"])
    sys.path.insert(0, str(root))
    geometry = json.loads(resolve_artifact(job_root, job["inputs"]["geometry"]["path"]).read_text())
    inputs = json.loads(resolve_artifact(job_root, job["inputs"]["geometry_inputs"]["path"]).read_text())
    if geometry_identity(inputs, geometry) != job["inputs"]["input_sha256"]:
        raise ValueError("CC_SKIN_STALE: prepared geometry identity mismatch")
    previous = Path.cwd()
    os.chdir(root)  # official checkpoints/configs contain upstream-relative paths
    try:
        weights, mapping = (_skintokens(geometry, root, checkpoint, vae, job["options"]) if backend == "skintokens"
                            else _unirig(geometry, root, checkpoint, job["options"]))
    finally:
        os.chdir(previous)
    if weights.shape != (len(geometry["vertices"]), len(geometry["bones"])):
        raise ValueError("CC_SKIN_BIND: prediction does not match canonical indices/columns")
    if not np.isfinite(weights).all() or np.any(weights < 0) or np.any(weights.sum(axis=1) <= 0):
        raise ValueError("CC_SKIN_WEIGHTS: invalid model influence values")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(job_root, out / "input")
    indptr, columns, values = [0], [], []
    for row in weights:
        for column, value in enumerate(row):
            if value > 0:
                columns.append(column)
                values.append(float(value))
        indptr.append(len(values))
    np.savez(out / "weights.npz", indptr=np.asarray(indptr, dtype=np.int64),
             bone_indices=np.asarray(columns, dtype=np.int64), weights=np.asarray(values, dtype=np.float64),
             bone_ids=np.asarray([b["name"] for b in geometry["bones"]]),
             vertex_sha256=np.asarray(vertex_sha256(geometry["vertices"])))
    write_json(out / "mapping.json", mapping)
    model_hash = hashlib.sha256(canonical_bytes({"checkpoint": checkpoint_hash, "vae": vae_hash})).hexdigest()
    settings = {"options": job["options"], "mapping": mapping, "upstream_revision": revision}
    write_json(out / "settings.json", settings)
    write_result(out, job, revision, model_hash, hashlib.sha256(canonical_bytes(settings)).hexdigest(),
                 {"weights": artifact(out, "weights.npz"), "mapping": artifact(out, "mapping.json"),
                  "settings": artifact(out, "settings.json")}, input_sha256=job["inputs"]["input_sha256"],
                 backend=backend, checkpoint_sha256=checkpoint_hash, vae_sha256=vae_hash,
                 adapter_sha256=sha256_file(Path(__file__)))
    print("wrote weight-only result " + str(out))
