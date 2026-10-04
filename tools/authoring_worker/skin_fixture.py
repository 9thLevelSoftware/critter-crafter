"""Host-side rights-cleared asymmetric transfer fixture; actual Blender preparation, no fake weights."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from critter_crafter.authoring.jobs import artifact, load_job, load_result, resolve_artifact, write_job
from critter_crafter.blender.runner import run_op
from critter_crafter.config import paths
from critter_crafter.library.catalog import compile_catalog, load_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "validate"])
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--backend", choices=["skintokens", "unirig"], default="skintokens")
    args = parser.parse_args()
    out = args.job.resolve()
    catalog = compile_catalog(load_sources(paths().data))
    profile = next(p for p in catalog["binding_profiles"] if p["binding_profile_id"] == "limb3_brachial")
    part = {"part_id": "public_asymmetric_skin_fixture", "real": {"fit": {"axis": "+z", "up": "+y"}},
            "template": profile["template"], "binding_profile_hash": profile["binding_profile_hash"],
            "length_m": 1.0, "girth_m": profile["girth_ratio"], "max_triangles": 2000, "albedo": "#9b6874"}
    source = out / "public_source.glb"
    common = {"part": part, "template": profile, "source_path": str(source), "public_fixture": True}
    if args.action == "prepare":
        if (out / "job.json").exists():
            parser.error("job already exists")
        result = run_op("realpart", dict(common, mode="prepare", job_dir=str(out)))["result"]
        inputs = {"part_id": part["part_id"], "input_sha256": result["input_sha256"],
                  "vertex_sha256": result["vertex_sha256"], "profile_sha256": result["profile_sha256"],
                  "geometry": artifact(out, "geometry.json"), "geometry_inputs": artifact(out, "geometry_inputs.json"),
                  "fitted_glb": artifact(out, "fitted.glb")}
        job = write_job(out, "skin", inputs, {"backend": args.backend, "seed": 1, "top_k": 5, "top_p": 0.95,
                        "temperature": 1.0, "repetition_penalty": 2.0, "num_beams": 10, "max_length": 2048,
                        "use_postprocess": False})
        print(job["job_id"])
    else:
        if args.result is None:
            parser.error("validate requires --result")
        job = load_job(out, "skin")
        result = load_result(args.result, job)
        weights = resolve_artifact(args.result, result["outputs"]["weights"]["path"])
        proof = run_op("realpart", dict(common, mode="validate_weights", weights_path=str(weights),
                       input_sha256=job["inputs"]["input_sha256"], out_weights=str(out / "validated_weights.npz")))
        print(json.dumps(proof["result"], sort_keys=True))


if __name__ == "__main__":
    main()
