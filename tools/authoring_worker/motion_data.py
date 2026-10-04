"""AnyTop conditioning for project motion; never import the upstream data loader."""
from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from critter_crafter.authoring.jobs import canonical_bytes, load_job, resolve_artifact, sha256_file
from critter_crafter.authoring.motion_dataset import CATEGORIES, TRAINING


def backend(device="cuda"):
    root = os.environ.get("CRITTER_ANYTOP_ROOT")
    if not root or not (Path(root) / "model" / "anytop.py").is_file():
        raise RuntimeError("CC_MOTION_MODEL_UNAVAILABLE: set CRITTER_ANYTOP_ROOT to the owner-installed AnyTop checkout")
    import torch
    if device.startswith("cuda") and (not torch.cuda.is_available() or torch.cuda.get_device_properties(device).total_memory < 16_000_000_000):
        raise RuntimeError("CC_MOTION_MODEL_UNAVAILABLE: owner-managed CUDA device with at least 16GB required")
    root = Path(root).resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    source_names = ("model/anytop.py", "model/motion_transformer.py", "data_loaders/tensors.py",
                    "utils/model_util.py", "diffusion/gaussian_diffusion.py", "diffusion/respace.py")
    source_hash = hashlib.sha256(b"".join(name.encode()+(root/name).read_bytes() for name in source_names)).hexdigest()
    revision += "/source-sha256:" + source_hash
    sys.path.insert(0, str(root))
    from model.anytop import AnyTop
    from utils.model_util import create_gaussian_diffusion
    from data_loaders.tensors import truebones_batch_collate
    return torch, owned_model_class(AnyTop, torch), create_gaussian_diffusion, truebones_batch_collate, revision

def owned_model_class(AnyTop, torch):
    from model.anytop import create_sin_embedding

    class OwnedAnyTop(AnyTop):
        def _apply(self, fn, recurse=True):
            return torch.nn.Module._apply(self, fn, recurse=recurse)

        def train(self, mode=True):
            return torch.nn.Module.train(self, mode)

        def forward(self, x, timesteps, get_layer_activation=-1, y=None):
            batch, joints, _, frames = x.shape
            spatial = y["joints_mask"][:,0,0,1:,1:].to(x.device)
            # GraphMotionDecoder flattens (time,batch), not (batch,time).
            spatial = spatial[None,:,None].expand(frames+1,batch,self.num_heads,joints,joints)
            spatial = (1.-spatial).reshape(-1,self.num_heads,joints,joints)
            spatial = spatial.masked_fill(spatial == 1., -1e9)
            temporal = 1.-y["mask"].to(x.device).repeat(1,joints,self.num_heads,1,1).reshape(-1,frames+1,frames+1).float()
            temporal = temporal.masked_fill(temporal == 1., -1e9)
            neutral = y["tpos_first_frame"].to(x.device).unsqueeze(0)
            embedded = self.input_process(x,neutral,y["joints_names_embs"],y["crop_start_ind"])
            timestep_embedding = create_sin_embedding(timesteps.view(1,-1,1),self.latent_dim)[0]
            output = self.seqTransDecoder(tgt=embedded,timesteps_embs=timestep_embedding,memory=None,
                                         spatial_mask=spatial,temporal_mask=temporal,y=y,
                                         get_layer_activation=get_layer_activation)
            if 0 <= get_layer_activation < self.num_layers:
                return self.output_process(output[0]), output[1]
            return self.output_process(output)

    return OwnedAnyTop



def create_model(api, settings, device):
    torch, AnyTop, create_diffusion, _, _ = api
    model = AnyTop(max_joints=120, feature_len=13, latent_dim=settings["latent_dim"], ff_size=1024,
                   num_layers=settings["layers"], num_heads=4, dropout=.1, activation="gelu",
                   t5_out_dim=768, root_input_feats=13, cond_mode="object_type", cond_mask_prob=0.,
                   skip_t5=False, value_emb=False).to(device)
    args = SimpleNamespace(noise_schedule="cosine", sigma_small=False, lambda_fs=0., lambda_geo=0.)
    return model, create_diffusion(args)


def load_records(root):
    job = load_job(root, "motion")
    index_path = resolve_artifact(root, job["inputs"]["dataset"]["path"])
    index = json.loads(index_path.read_text(encoding="utf-8"))
    records = []
    for item in index["records"]:
        path = resolve_artifact(root, item["path"])
        if sha256_file(path) != item["sha256"]:
            raise ValueError("CC_MOTION_STALE: dataset record changed")
        record = json.loads(path.read_text(encoding="utf-8"))
        digest = record.pop("record_sha256")
        if hashlib.sha256(canonical_bytes(record)).hexdigest() != digest:
            raise ValueError("CC_MOTION_STALE: canonical record identity")
        record["record_sha256"] = digest
        if record["representation"] != "cc-motion-world-1" or record["fps"] != 30:
            raise ValueError("CC_MOTION_DATASET: unsupported representation")
        records.append(record)
    if not records:
        raise ValueError("CC_MOTION_DATASET: empty dataset")
    expected_splits = {split:sorted(r["record_sha256"] for r in records if r["split"] == split)
                       for split in ("train","held_out")}
    if index.get("split_lists") != expected_splits or sum(map(len,expected_splits.values())) != len(records):
        raise ValueError("CC_MOTION_SPLIT: saved split lists differ from whole records")
    return job, index, records


def topology(parents):
    import numpy as np
    count = len(parents)
    distance = np.full((count, count), count, dtype=np.int64)
    relation = np.full((count, count), 4, dtype=np.int64)
    for i, parent in enumerate(parents):
        distance[i,i] = 0; relation[i,i] = 0
        if parent >= 0:
            distance[i,parent] = distance[parent,i] = 1
            relation[i,parent] = 1; relation[parent,i] = 2
    for k in range(count):
        distance = np.minimum(distance, distance[:, k:k+1] + distance[k:k+1, :])
    for i in range(count):
        for j in range(count):
            if i != j and parents[i] >= 0 and parents[i] == parents[j]:
                relation[i,j] = 3
    return np.minimum(distance, 5), relation


def collate(api, records, device):
    import numpy as np
    torch, _, _, collator, _ = api
    maximum = max(len(r["bone_names"]) for r in records)
    items = []
    for record in records:
        motion = np.asarray(record["features"], dtype=np.float32)
        frames, joints, _ = motion.shape
        distances, relations = topology(record["parents"])
        neutral = np.asarray(record["neutral_features"], dtype=np.float32)
        offsets = np.zeros((joints,3), dtype=np.float32)
        items.append((motion, frames, record["parents"], neutral, offsets,
                      torch.ones((frames+1, frames+1), dtype=torch.bool), distances, relations,
                      record["category"], np.asarray(record["role_vectors"], dtype=np.float32), 0,
                      np.zeros((joints,13), dtype=np.float32), np.ones((joints,13), dtype=np.float32), maximum))
    motion, condition = collator(items)
    # Upstream collator supplies pairwise masks including its neutral time token.
    for key, value in condition["y"].items():
        if torch.is_tensor(value):
            condition["y"][key] = value.to(device)
    return motion.to(device), condition


def inpainting(api, records, baseline):
    torch = api[0]
    fixed = torch.zeros_like(baseline, dtype=torch.bool)
    for b, record in enumerate(records):
        frames = len(record["features"]); joints = len(record["bone_names"])
        for frame in {0, 1, max(0,frames-2), frames-1}:
            fixed[b,:joints,:,frame] = True
        fixed[b,0,(0,2),:frames] = True
        seam = record["seam_frame"]
        if seam is not None:
            for frame in range(max(0,seam-1), min(frames,seam+2)):
                fixed[b,:joints,:,frame] = True
        for t, contacts in enumerate(record["contact_schedules"]):
            fixed[b,:joints,12,t] = True
            for contact in contacts:
                if contact["planted"]:
                    name = record["contact_bones"][contact["contact_id"]]
                    j = record["bone_names"].index(name)
                    fixed[b,j,:3,t] = True
                    fixed[b,j,9:12,t] = True
    return fixed


def contract_check(api, model, diffusion, records, device):
    torch = api[0]
    distinct = sorted(records, key=lambda r:(len(r["bone_names"]), len(r["features"]), r["record_sha256"]))
    first = distinct[0]
    mixed = [record for record in reversed(distinct)
             if len(record["bone_names"]) != len(first["bone_names"]) and len(record["features"]) != len(first["features"])]
    if not mixed:
        raise ValueError("CC_MOTION_DATASET: contract smoke requires mixed joint and frame counts")
    batch = [first, mixed[0]]
    x, cond = collate(api, batch, device)
    t = torch.tensor([0,99], device=device)
    noise = torch.randn_like(x)
    loss = diffusion.training_losses(model, x, t, model_kwargs=cond, noise=noise)["loss"]
    if loss.shape != (2,) or not torch.isfinite(loss).all():
        raise ValueError("CC_MOTION_MODEL_CONTRACT: forward/loss")
    loss.mean().backward()
    if any(parameter.grad is not None and not torch.isfinite(parameter.grad).all() for parameter in model.parameters()):
        raise ValueError("CC_MOTION_MODEL_CONTRACT: nonfinite gradient")
    model.zero_grad(set_to_none=True)
    fixed = inpainting(api, batch, x)
    cond["y"].update(inpainting_mask=fixed, inpainted_motion=x)
    with torch.no_grad():
        prediction = diffusion.p_mean_variance(model, noise, t, clip_denoised=False, model_kwargs=cond)["pred_xstart"]
    if not torch.equal(prediction[fixed], x[fixed]):
        raise ValueError("CC_MOTION_MODEL_CONTRACT: True=fixed inpainting")
    return {"mixed_joints": [len(r["bone_names"]) for r in batch],
            "mixed_frames": [len(r["features"]) for r in batch], "loss": loss.detach().cpu().tolist(),
            "neutral_token_mask": list(cond["y"]["mask"].shape), "inpainting_fixed": int(fixed.sum())}


def seed_all(torch, seed):
    import numpy as np
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
