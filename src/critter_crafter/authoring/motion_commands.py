"""Opt-in motion export and hard-QA import through the existing polish path."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import click

from .jobs import artifact, canonical_bytes, load_result, resolve_artifact, sha256_file, write_job, write_json
from .motion_dataset import CATEGORIES, TRAINING, lineage_split, make_record
from ..blender.runner import run_op, BlenderError
from ..config import paths
from ..skeletons.review import resolve_polish


def _sources():
    return {doc["skeleton_id"]: (path,doc) for path in sorted(paths().data.joinpath("skeletons").glob("*_v3.skeleton.json"))
            for doc in [json.loads(path.read_text(encoding="utf-8"))]}


def _arguments(catalog, skeleton, provenance):
    from ..library.commands import _gait, skeleton_profiles, skeleton_source_hash
    fingerprint = skeleton_source_hash(catalog,skeleton)
    polish = resolve_polish(paths().work/"polish"/"skeletons"/skeleton["skeleton_id"],skeleton["skeleton_id"],fingerprint)
    return {"skeleton":skeleton,"gait_profile":_gait(catalog,skeleton["locomotion_hint"]),
            "binding_profiles":skeleton_profiles(catalog,skeleton),"source_fingerprint":fingerprint,
            "source_provenance":provenance,"polish_blend":str(polish) if polish else ""}


def _input_identity(args):
    package = Path(__file__).parent
    producer = {name:sha256_file(package/name) for name in ("motion_commands.py","motion_dataset.py","jobs.py")}
    return {"source_fingerprint":args["source_fingerprint"],
            "profile_sha256":hashlib.sha256(canonical_bytes(args["binding_profiles"])).hexdigest(),
            "source_provenance_sha256":hashlib.sha256(canonical_bytes(args.get("source_provenance",{}))).hexdigest(),
            "polish_sha256":sha256_file(args["polish_blend"]) if args.get("polish_blend") else "",
            "dataset_producer_sha256":hashlib.sha256(canonical_bytes(producer)).hexdigest()}


def _export_motion(args):
    identity = {key:value for key,value in _input_identity(args).items() if key != "dataset_producer_sha256"}
    key = hashlib.sha256(canonical_bytes(identity)).hexdigest()
    cache_root = paths().work/"authoring"/"motion-export"
    directory = cache_root/key
    raw = directory/"motion.json"; cache = directory/"cache.json"
    candidates = [cache] + sorted(cache_root.glob("*/cache.json"))
    for receipt in candidates:
        if not receipt.is_file(): continue
        recorded = json.loads(receipt.read_text(encoding="utf-8"))
        recorded_inputs = {field:recorded.get("inputs",{}).get(field) for field in identity}
        cached_motion = receipt.with_name("motion.json")
        if (recorded_inputs == identity and cached_motion.is_file()
                and recorded.get("motion_sha256") == sha256_file(cached_motion)):
            if cached_motion != raw:
                directory.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(cached_motion,raw)
                write_json(cache,{"inputs":identity,"motion_sha256":sha256_file(raw)})
            return json.loads(raw.read_text(encoding="utf-8"))
    run_op("skeleton",{**args,"mode":"motion_dataset","out_motion":str(raw)})
    write_json(cache,{"inputs":identity,"motion_sha256":sha256_file(raw)})
    return json.loads(raw.read_text(encoding="utf-8"))


def _complete_amalgams(catalog, selected, provenance):
    from ..skeletons.amalgam import build_amalgam, mode_for_seed, AmalgamError
    from ..library.catalog import compile_skeleton, CatalogError
    from ..skeletons.qa import evaluate_motion
    from ..skeletons.action_qa import evaluate_actions
    present = set()
    for skeleton in selected:
        prov = provenance[skeleton["skeleton_id"]]
        if "mode" in prov:
            split,_ = lineage_split({**skeleton,"provenance":prov})
            present.add((prov["mode"],split))
    required = {(mode,split) for mode in ("hauled","walker","slither") for split in ("train","held_out")}
    if required <= present: return
    templates = {t["template_id"]:t for t in catalog["branch_templates"]}
    profiles = {(p["binding_profile_id"],p["binding_profile_version"]):p for p in catalog["binding_profiles"]}
    for seed in range(1,1001):
        mode = mode_for_seed(seed); split = "held_out" if seed%5 == 0 else "train"
        if (mode,split) in present: continue
        try:
            source = build_amalgam(seed)
            compiled = compile_skeleton(source,templates,profiles)
            args = _arguments(catalog,compiled,source["provenance"])
            motion = _export_motion(args)
            if (not evaluate_motion(compiled,motion,profiles=args["binding_profiles"])["passed"]
                    or not evaluate_actions(compiled,motion)["passed"]):
                continue
        except (AmalgamError,CatalogError,BlenderError):
            continue
        selected.append(compiled); provenance[compiled["skeleton_id"]] = source["provenance"]
        present.add((mode,split))
        if required <= present: return
    raise ValueError("CC_MOTION_SPLIT: no feasible generated amalgam for every mode/split")


def prepare(out, skeleton_ids=(), categories=CATEGORIES):
    from ..library.commands import _catalog
    from ..skeletons.qa import evaluate_motion
    from ..skeletons.action_qa import evaluate_actions
    catalog = _catalog(); sources = _sources()
    selected = sorted([s for s in catalog["skeletons"] if not skeleton_ids or s["skeleton_id"] in skeleton_ids],key=lambda s:s["skeleton_id"])
    if not selected or set(skeleton_ids)-{s["skeleton_id"] for s in selected}:
        raise ValueError("CC_MOTION_SOURCE: unknown skeleton")
    provenance = {sid:doc.get("provenance",{}) for sid,(_,doc) in sources.items()}
    if not skeleton_ids:
        _complete_amalgams(catalog,selected,provenance)
    root = Path(out).resolve()
    if (root/"job.json").exists():
        raise ValueError("CC_MOTION_OUTPUT: job already exists; use a new output directory")
    root.mkdir(parents=True,exist_ok=True)
    entries = []; source_identities = {}; split_lists = {"train":[],"held_out":[]}
    for skeleton in sorted(selected,key=lambda s:s["skeleton_id"]):
        sid = skeleton["skeleton_id"]; args = _arguments(catalog,skeleton,provenance[sid])
        motion = _export_motion(args)
        checks = {"motion":evaluate_motion(skeleton,motion,profiles=args["binding_profiles"]),
                  "action":evaluate_actions(skeleton,motion)}
        if not all(check["passed"] for check in checks.values()):
            raise ValueError("CC_MOTION_QA: "+sid+": "+json.dumps(checks))
        source_identities[sid] = _input_identity(args)
        for category in categories:
            record = make_record(motion,category)
            destination = root/"records"/(sid+"-"+category+".json")
            write_json(destination,record)
            entries.append(artifact(root,destination))
            split_lists[record["split"]].append(record["record_sha256"])
    index = {"schema_version":"cc-motion-dataset-1","representation":"cc-motion-world-1",
             "rights":"project-generated or owner-authored; no external corpus", "records":entries,
             "split_lists":{key:sorted(value) for key,value in split_lists.items()}}
    write_json(root/"dataset.json",index)
    return write_job(root,"motion",{"dataset":artifact(root,root/"dataset.json"),"records":entries,
                                    "skeletons":source_identities},
                     {"training":TRAINING,"categories":list(categories),"purpose":"inference" if skeleton_ids else "training"})


def import_result(skeleton_id, result_root):
    from ..library.commands import _catalog
    from ..library.export_validation import validate_glb_rest, validate_glb_motion, validate_glb_surface
    result_root = Path(result_root).resolve(); result = load_result(result_root)
    if result.get("kind") != "motion":
        raise ValueError("CC_MOTION_RESULT: wrong authoring kind")
    job = result["input_job"]
    identity = {key:job[key] for key in ("kind","inputs","options")}
    if (job.get("schema_version") != "cc-authoring-job-1" or job["kind"] != "motion"
            or hashlib.sha256(canonical_bytes(identity)).hexdigest() != result["job_id"]):
        raise ValueError("CC_MOTION_STALE: result input identity")
    catalog = _catalog(); sources = _sources()
    skeleton = next((s for s in catalog["skeletons"] if s["skeleton_id"] == skeleton_id),None)
    if skeleton is None: raise ValueError("CC_MOTION_SOURCE: unknown skeleton")
    args = _arguments(catalog,skeleton,sources[skeleton_id][1].get("provenance",{}))
    source = job["inputs"]["skeletons"].get(skeleton_id,{})
    if source != _input_identity(args):
        raise ValueError("CC_MOTION_STALE: canonical skeleton/profile/owned-polish/producer changed")
    samples_path = resolve_artifact(result_root,result["outputs"]["samples"]["path"])
    payload = json.loads(samples_path.read_text(encoding="utf-8"))
    if (payload.get("schema_version") != "cc-motion-proposals-1"
            or payload["model_sha256"] != result["model_sha256"]
            or payload["settings_sha256"] != result["settings_sha256"]
            or payload["data_sha256"] != result["data_sha256"]):
        raise ValueError("CC_MOTION_RESULT: proposal identity differs")
    proposal = payload["skeletons"].get(skeleton_id)
    if proposal is None or proposal["source_fingerprint"] != args["source_fingerprint"]:
        raise ValueError("CC_MOTION_STALE: proposal source changed")
    authoring = {"job_id":result["job_id"],"data_sha256":result["data_sha256"],
                 "model_sha256":result["model_sha256"],"settings_sha256":result["settings_sha256"],
                 "sample_sha256":sha256_file(samples_path),"producer_revision":result["producer_revision"],
                 "seed":result["seed"],"categories":proposal["categories"]}
    stage = paths().work/"authoring"/"motion-import"/skeleton_id/result["outputs"]["samples"]["sha256"]
    stage.mkdir(parents=True,exist_ok=True)
    blend = stage/"master.blend"; motion_path = stage/"motion.json"
    checked = run_op("skeleton",{**args,"mode":"motion_import","proposal":proposal,"authoring":authoring,
                                  "out_blend":str(blend),"out_motion":str(motion_path)})
    glb = stage/"validated.glb"
    run_op("skeleton",{**args,"polish_blend":str(blend),"out_fbx":str(stage/"validated.fbx"),
                       "out_glb":str(glb),"out_motion":str(motion_path)})
    motion = json.loads(motion_path.read_text(encoding="utf-8"))
    exports = [validate_glb_rest(glb,skeleton),validate_glb_motion(glb,skeleton,motion),validate_glb_surface(glb,skeleton,motion)]
    if any(not check["passed"] for check in exports):
        raise ValueError("CC_MOTION_EXPORT_QA: "+json.dumps(exports))
    directory = paths().work/"polish"/"skeletons"/skeleton_id
    directory.mkdir(parents=True,exist_ok=True)
    name = "motion-"+sha256_file(blend)+".blend"
    destination = directory/name
    if not destination.exists(): shutil.copyfile(blend,destination)
    authoring["blend_sha256"] = sha256_file(destination)
    manifest = {"skeleton_id":skeleton_id,"source_fingerprint":args["source_fingerprint"],
                "blend":name,"authoring":authoring}
    temporary = directory/"override.motion-import.json"; write_json(temporary,manifest)
    os.replace(temporary,directory/"override.json")
    source_path,source_doc = sources[skeleton_id]
    if source_doc.get("status") != "draft":
        source_doc["status"] = "draft"; source_doc.pop("review",None)
        source_path.write_text(json.dumps(source_doc,indent=2)+"\n",encoding="utf-8")
    return {"override":str(directory/"override.json"),"qa":checked,"exports":exports,"authoring":authoring}


def register(authoring):
    @authoring.group("motion")
    def motion():
        """Prepare rights-cleared actions and import passing offline proposals."""

    @motion.command("prepare")
    @click.option("--out",required=True,type=click.Path(path_type=Path))
    @click.option("--skeleton","skeleton_ids",multiple=True)
    @click.option("--category","categories",multiple=True,type=click.Choice(CATEGORIES))
    def motion_prepare(out,skeleton_ids,categories):
        try: click.echo(json.dumps(prepare(out,skeleton_ids,categories or CATEGORIES),indent=2))
        except (ValueError,OSError,BlenderError) as exc: raise click.ClickException(str(exc)) from exc

    @motion.command("import")
    @click.option("--skeleton","skeleton_id",required=True)
    @click.option("--result","result_root",required=True,type=click.Path(path_type=Path,exists=True))
    def motion_import(skeleton_id,result_root):
        try: click.echo(json.dumps(import_result(skeleton_id,result_root),indent=2))
        except (ValueError,OSError,BlenderError) as exc: raise click.ClickException(str(exc)) from exc
