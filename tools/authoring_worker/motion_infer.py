"""Offline action proposals constrained by authored endpoints, roots and contacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from motion_data import (TRAINING, backend, canonical_bytes, collate, create_model, inpainting,
                         load_records, seed_all, sha256_file)
from motion_train import producer_hash
from critter_crafter.authoring.jobs import artifact, resolve_artifact, write_result
from critter_crafter.authoring.motion_dataset import decode_frames


def run(args):
    root = Path(args.job).resolve(); model_root = Path(args.model).resolve(); out = Path(args.out).resolve()
    job,index,records = load_records(root)
    manifest = json.loads((model_root/"model.json").read_text(encoding="utf-8"))
    digest = manifest.pop("model_sha256")
    if manifest.get("schema_version") != "cc-motion-model-1" or hashlib.sha256(canonical_bytes(manifest)).hexdigest() != digest:
        raise ValueError("CC_MOTION_STALE: model identity")
    if manifest["settings"] != TRAINING or manifest.get("external_weights") is not False or manifest.get("external_corpus") is not False:
        raise ValueError("CC_MOTION_MODEL: owned from-scratch model required")
    api = backend(getattr(args,"device","cuda")); torch = api[0]
    if manifest["producer_revision"] != api[4] or manifest["adapter_sha256"] != producer_hash():
        raise ValueError("CC_MOTION_STALE: worker/upstream producer changed")
    settings_hash = hashlib.sha256(canonical_bytes(TRAINING)).hexdigest()
    if manifest["settings_sha256"] != settings_hash:
        raise ValueError("CC_MOTION_STALE: settings changed")
    seed_all(torch,int(args.seed)); out.mkdir(parents=True,exist_ok=True)
    selected = set(job["options"].get("categories", list(manifest["models"])))
    proposals = {}
    for record in records:
        category = record["category"]
        if category not in selected:
            continue
        entry = manifest["models"][category]; path = resolve_artifact(model_root,entry["path"])
        if sha256_file(path) != entry["sha256"]:
            raise ValueError("CC_MOTION_STALE: checkpoint changed")
        checkpoint = torch.load(path,map_location="cpu",weights_only=True)
        if (checkpoint["category"] != category or checkpoint["settings_sha256"] != settings_hash
                or checkpoint["data_sha256"] != manifest["data_sha256"]):
            raise ValueError("CC_MOTION_MODEL: checkpoint provenance mismatch")
        model,diffusion = create_model(api,TRAINING,getattr(args,"device","cuda"))
        model.load_state_dict(checkpoint["model"],strict=True); model.eval()
        x,cond = collate(api,[record],getattr(args,"device","cuda"))
        fixed = inpainting(api,[record],x)
        cond["y"].update(inpainting_mask=fixed,inpainted_motion=x)
        with torch.no_grad():
            predicted = diffusion.p_sample_loop(model,x.shape,clip_denoised=False,model_kwargs=cond,
                                               device=x.device,progress=False)
        if not torch.isfinite(predicted).all() or not torch.equal(predicted[fixed],x[fixed]):
            raise ValueError("CC_MOTION_INPAINT: invalid output or changed fixed values")
        frames = predicted[0].permute(2,0,1).cpu().tolist()
        decoded = decode_frames(frames,record)
        sid = record["skeleton_id"]
        target = proposals.setdefault(sid,{"source_fingerprint":record["source_fingerprint"],"clips":{},"categories":[]})
        target["categories"].append(category)
        if category == "telegraph_attack":
            seam = record["seam_frame"]
            target["clips"]["telegraph"] = decoded[:seam+1]
            attack = [dict(sample, frame=frame) for frame, sample in enumerate(decoded[seam:])]
            target["clips"]["attack"] = attack
        else:
            target["clips"][category] = decoded
        del model
    if not proposals:
        raise ValueError("CC_MOTION_INFER: no selected category")
    payload = {"schema_version":"cc-motion-proposals-1","skeletons":proposals,
               "seed":int(args.seed),"data_sha256":manifest["data_sha256"],
               "model_sha256":digest,"settings_sha256":settings_hash,
               "record_hashes":[r["record_sha256"] for r in records if r["category"] in selected]}
    (out/"samples.json").write_bytes(canonical_bytes(payload))
    return write_result(out,job,api[4],digest,settings_hash,{"samples":artifact(out,out/"samples.json")},
                        kind="motion",input_job=job,seed=int(args.seed),data_sha256=manifest["data_sha256"],
                        skeleton_sources={sid:p["source_fingerprint"] for sid,p in proposals.items()})
