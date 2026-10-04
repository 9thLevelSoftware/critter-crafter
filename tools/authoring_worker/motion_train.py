"""Five owned-motion models, accumulated AdamW and held-out checkpoint selection."""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

from motion_data import (CATEGORIES, TRAINING, backend, canonical_bytes, collate, contract_check,
                         create_model, load_records, seed_all, sha256_file)


def producer_hash():
    root = Path(__file__).parent
    files = ("motion_train.py", "motion_infer.py", "motion_data.py")
    payload = b"".join(name.encode()+ (root/name).read_bytes() for name in files)
    host = root.parents[1]/"src"/"critter_crafter"/"authoring"
    payload += b"".join(name.encode()+(host/name).read_bytes() for name in ("motion_dataset.py","jobs.py"))
    return hashlib.sha256(payload).hexdigest()


def validation(api, model, diffusion, records, device):
    torch = api[0]; model.eval(); total = 0.; count = 0
    with torch.no_grad():
        for start in range(0,len(records),2):
            batch = records[start:start+2]; x, cond = collate(api, batch, device)
            generator = torch.Generator(device=device).manual_seed(1701+start)
            noise = torch.randn(x.shape, generator=generator, device=device)
            timesteps = torch.randint(0,100,(len(batch),), generator=generator, device=device)
            losses = diffusion.training_losses(model,x,timesteps,model_kwargs=cond,noise=noise)["loss"]
            if not torch.isfinite(losses).all():
                raise ValueError("CC_MOTION_TRAINING: nonfinite held-out loss")
            total += float(losses.sum()); count += len(batch)
    model.train()
    return total/count


def run(args):
    root = Path(args.job).resolve(); out = Path(args.out).resolve()
    device = getattr(args,"device","cuda")
    job, index, records = load_records(root)
    settings = job["options"].get("training", TRAINING)
    if settings != TRAINING:
        raise ValueError("CC_MOTION_SETTINGS: first-run training configuration must match the owned-motion contract")
    lineages = {}
    for record in records:
        previous = lineages.setdefault(record["lineage"],record["split"])
        if previous != record["split"]:
            raise ValueError("CC_MOTION_SPLIT: lineage crosses train and held-out")
    if not getattr(args,"check_only",False):
        for category in CATEGORIES:
            if {r["split"] for r in records if r["category"] == category} != {"train","held_out"}:
                raise ValueError("CC_MOTION_SPLIT: every model requires train and held-out data before training")
    if not getattr(args,"check_only",False) and not device.startswith("cuda"):
        raise RuntimeError("CC_MOTION_MODEL_UNAVAILABLE: full training requires the owner-managed CUDA worker")
    api = backend(device); torch = api[0]; seed_all(torch,1)
    model, diffusion = create_model(api,settings,device)
    model.eval()
    smoke = contract_check(api,model,diffusion,records,device)
    out.mkdir(parents=True,exist_ok=True)
    (out/"contract-check.json").write_bytes(canonical_bytes(smoke))
    if getattr(args,"check_only",False):
        return smoke
    del model
    settings_hash = hashlib.sha256(canonical_bytes(settings)).hexdigest()
    data_hash = job["job_id"]
    models = {}
    for category in CATEGORIES:
        train = sorted([r for r in records if r["category"] == category and r["split"] == "train"],key=lambda r:r["record_sha256"])
        held = sorted([r for r in records if r["category"] == category and r["split"] == "held_out"],key=lambda r:r["record_sha256"])
        if not train or not held:
            raise ValueError("CC_MOTION_SPLIT: each action requires train and held-out lineages")
        seed_all(torch,1); rng = random.Random(1)
        model,diffusion = create_model(api,settings,device); model.train()
        optimizer = torch.optim.AdamW(model.parameters(),lr=0.0001)
        best = math.inf; history = []; best_step = 0
        path = out/(category+".pt")
        for update in range(1,100001):
            optimizer.zero_grad(set_to_none=True)
            for micro in range(8):
                batch = [train[rng.randrange(len(train))] for _ in range(2)]
                x,cond = collate(api,batch,device)
                timestep = torch.randint(0,100,(2,),device=device)
                loss = diffusion.training_losses(model,x,timestep,model_kwargs=cond)["loss"].mean()/8
                if not torch.isfinite(loss):
                    raise ValueError("CC_MOTION_TRAINING: nonfinite training loss")
                loss.backward()
            optimizer.step()
            if update == 1 or update % 1000 == 0:
                value = validation(api,model,diffusion,held,device)
                history.append({"update":update,"held_out_loss":value})
                if value < best:
                    best = value; best_step = update
                    torch.save({"model":model.state_dict(),"category":category,"settings":settings,
                                "settings_sha256":settings_hash,"data_sha256":data_hash,
                                "producer_revision":api[4],"adapter_sha256":producer_hash(),
                                "update":update,"held_out_loss":value},path)
                (out/(category+"-validation.json")).write_bytes(canonical_bytes(history))
        models[category] = {"path":path.name,"sha256":sha256_file(path),"selected_update":best_step,
                            "held_out_loss":best,"optimizer_updates":100000}
        del model,optimizer
    manifest = {"schema_version":"cc-motion-model-1","job_id":job["job_id"],
                "producer_revision":api[4],"adapter_sha256":producer_hash(),"settings":settings,
                "settings_sha256":settings_hash,"data_sha256":data_hash,"models":models,
                "split_lists":index["split_lists"],"external_weights":False,"external_corpus":False}
    manifest["model_sha256"] = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    (out/"model.json").write_bytes(canonical_bytes(manifest))
    return {"model_sha256":manifest["model_sha256"],"models":models}
