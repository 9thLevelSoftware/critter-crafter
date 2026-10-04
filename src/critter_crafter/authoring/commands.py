"""Offline preparation/import; no model or numerical libraries enter the host CLI."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import click

from .jobs import (artifact, canonical_bytes, load_job, load_result, resolve_artifact, sha256_file,
                   write_job, write_json)


class AuthoringGroup(click.Group):
    def get_command(self, ctx, cmd_name):
        if cmd_name == "motion" and cmd_name not in self.commands:
            from . import motion_commands
            motion_commands.register(self)
        return super().get_command(ctx, cmd_name)

    def list_commands(self, ctx):
        return sorted(set(super().list_commands(ctx)) | {"motion"})


@click.group(cls=AuthoringGroup)
def authoring():
    """Prepare owner-transferred offline GPU jobs and import verified proposals."""


@authoring.group()
def skin():
    """Learn weights on the existing profile, never replace its skeleton or geometry."""


def _inputs(part_id):
    from ..config import paths
    from ..parts.commands import _catalog, _profile, _resolve_source
    catalog = _catalog()
    compiled = next((p for p in catalog["parts"] if p["part_id"] == part_id), None)
    if compiled is None or not compiled.get("real"):
        raise click.ClickException("CC_SKIN_STALE: unknown or non-real part " + part_id)
    profile = _profile(catalog, compiled["binding_profile_id"], compiled["binding_profile_version"])
    source, _ = _resolve_source(compiled["real"]["archive_path"])
    record_path = paths().data / "parts" / (part_id + ".part.json")
    return compiled, profile, source, record_path


@skin.command("prepare")
@click.option("--part", "part_id", required=True)
@click.option("--out", type=click.Path(path_type=Path), required=True)
@click.option("--backend", type=click.Choice(["skintokens", "unirig"]), default="skintokens")
def skin_prepare(part_id, out, backend):
    """Freeze cleanup/fit/post-loop geometry; compute no skin weights."""
    from ..blender.runner import BlenderError, run_op
    part, profile, source, _ = _inputs(part_id)
    out = out.resolve()
    if (out / "job.json").exists():
        raise click.ClickException("CC_AUTHORING_JOB: output already contains a job")
    try:
        prepared = run_op("realpart", {"part": part, "template": profile, "source_path": str(source),
                                      "mode": "prepare", "job_dir": str(out)})["result"]
        inputs = {"part_id": part_id, "input_sha256": prepared["input_sha256"],
                  "profile_sha256": prepared["profile_sha256"],
                  "vertex_sha256": prepared["vertex_sha256"],
                  "geometry": artifact(out, "geometry.json"),
                  "geometry_inputs": artifact(out, "geometry_inputs.json"),
                  "fitted_glb": artifact(out, "fitted.glb")}
        options = {"backend": backend, "seed": 1, "top_k": 5, "top_p": 0.95, "temperature": 1.0,
                   "repetition_penalty": 2.0, "num_beams": 10, "max_length": 2048,
                   "use_postprocess": False}
        job = write_job(out, "skin", inputs, options)
    except (BlenderError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"prepared {job['job_id']}: {out}")
    click.echo(f"Owner-managed worker: python tools/authoring_worker/cli.py skin --job {out} --out <result-dir>")


@skin.command("import")
@click.option("--part", "part_id", required=True)
@click.option("--result", "result_dir", type=click.Path(path_type=Path, exists=True), required=True)
def skin_import(part_id, result_dir):
    """Adopt a validated weight-only proposal as draft, pending deformation/owner review."""
    from ..blender.runner import BlenderError, run_op
    from ..config import paths
    part, profile, source, record_path = _inputs(part_id)
    result_dir = result_dir.resolve()
    try:
        # Workers copy the exact input job/artifacts into result/input for independent import verification.
        job = load_job(result_dir / "input", "skin")
        result = load_result(result_dir, job)
        if job["inputs"]["part_id"] != part_id:
            raise ValueError("CC_SKIN_STALE: result belongs to another part")
        if result.get("input_sha256") != job["inputs"]["input_sha256"]:
            raise ValueError("CC_SKIN_STALE: result geometry identity mismatch")
        settings = json.loads(resolve_artifact(result_dir, result["outputs"]["settings"]["path"]).read_text())
        if hashlib.sha256(canonical_bytes(settings)).hexdigest() != result["settings_sha256"]:
            raise ValueError("CC_SKIN_STALE: model settings artifact changed")
        if settings["options"] != job["options"] or result.get("backend") != job["options"]["backend"]:
            raise ValueError("CC_SKIN_STALE: model used different authoring settings")
        model_identity = {"checkpoint": result["checkpoint_sha256"], "vae": result.get("vae_sha256")}
        if hashlib.sha256(canonical_bytes(model_identity)).hexdigest() != result["model_sha256"]:
            raise ValueError("CC_SKIN_STALE: model/checkpoint identity mismatch")
        raw = resolve_artifact(result_dir, result["outputs"]["weights"]["path"])
        destination = paths().work / "authoring" / "weights" / part_id / job["job_id"]
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / "weights.npz"
        run_op("realpart", {"part": part, "template": profile, "source_path": str(source),
                           "mode": "validate_weights", "weights_path": str(raw),
                           "out_weights": str(target), "input_sha256": job["inputs"]["input_sha256"]})
        shutil.copytree(result_dir / "input", destination / "input", dirs_exist_ok=True)
        write_json(destination / "producer.json", result)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["real"]["fit"]["weights"] = "learned"
        record["real"]["learned_weights"] = {
            "artifact_path": target.relative_to(paths().root).as_posix(), "sha256": sha256_file(target),
            "input_sha256": job["inputs"]["input_sha256"], "profile_sha256": job["inputs"]["profile_sha256"],
            "model_sha256": result["model_sha256"], "settings_sha256": result["settings_sha256"]}
        record["status"] = "draft"
        record["real"].pop("approved_pipeline", None)
        write_json(record_path, record)
    except (BlenderError, ValueError, OSError, KeyError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"imported draft weights for {part_id}; build then critter part review {part_id}; owner approval remains required")
