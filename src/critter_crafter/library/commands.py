"""`critter library ...` and `critter blender ...` commands (Blender-backed)."""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import click

from ..blender.runner import BlenderError, run_op, snippet
from ..config import find_asset_archive, paths
from ..schema.validate import validate_sources
from ..recipes.generator import generate_for_skeleton
from .catalog import dumps
from ..skeletons.review import ReviewError, content_fingerprint, resolve_polish, source_fingerprint


def _catalog() -> dict[str, Any]:
    diags, catalog = validate_sources(paths().data, paths().schemas)
    errors = [d for d in diags if not d.startswith("CC_NO_CANDIDATE_OPTIONAL")]
    if errors or catalog is None:
        raise click.ClickException("sources invalid:\n" + "\n".join(diags))
    return catalog


def library_dir(catalog: dict[str, Any], root: Path | None = None) -> Path:
    return (root or paths().library_out) / f"{catalog['library_id']}-v{catalog['version']}"


def _gait(catalog: dict[str, Any], hint: str) -> dict[str, Any]:
    return next(g for g in catalog["gait_profiles"] if g["hint"] == hint)


def skeleton_profiles(catalog: dict, skeleton: dict) -> list[dict]:
    identities = {(b.get("binding_profile_id"), b.get("binding_profile_version")) for b in skeleton["branches"]}
    return [p for p in catalog.get("binding_profiles", [])
            if (p["binding_profile_id"], p["binding_profile_version"]) in identities]


def build_pipeline_fingerprint() -> str:
    """Invalidate existing builds when baking, reference geometry or exporters change."""
    package = Path(__file__).resolve().parents[1]
    names = ("blender/ops_skeleton.py", "blender/ops_reference.py", "blender/rigkit.py",
             "blender/frame.py", "skeletons/motion.py", "skeletons/actions.py")
    payload = {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in names}
    payload["contract"] = "cc-gen-3/blender-5.2/fbx-secondary-X/glTF-2/rest-v1"
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@functools.lru_cache(maxsize=None)
def part_pipeline_fingerprint() -> str:
    """Fingerprint only code that produces placeholder/reference part assets."""
    package = Path(__file__).resolve().parents[1]
    names = ("blender/ops_placeholder.py", "blender/rigkit.py", "blender/frame.py")
    payload = {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in names}
    payload["contract"] = "cc-gen-3/blender-5.2/placeholder-fbx-secondary-X-v1"
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@functools.lru_cache(maxsize=None)
def realpart_pipeline_fingerprint() -> str:
    """Fingerprint the code that cleans, fits, weights and exports real (sourced) parts."""
    package = Path(__file__).resolve().parents[1]
    names = ("blender/ops_realpart.py", "parts/fit.py", "blender/rigkit.py", "blender/frame.py", "mathutil.py")
    payload = {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in names}
    payload["contract"] = "cc-gen-3/blender-5.2/realpart-fbx-secondary-X-v1"
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@functools.lru_cache(maxsize=None)
def connector_pipeline_fingerprint() -> str:
    """Fingerprint only the compiler-generated connector baker. Never hashed into real parts."""
    package = Path(__file__).resolve().parents[1]
    names = ("blender/ops_connector.py", "blender/ops_placeholder.py", "blender/rigkit.py", "blender/frame.py")
    payload = {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in names}
    payload["contract"] = "cc-gen-3/blender-5.2/connector-sdf-fbx-secondary-X-v1"
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def require_part_pipelines_unchanged() -> None:
    """Fail when part pipeline code changed after the build pinned its fingerprints.

    Blender imports the op modules only once it starts, so an edit saved between pinning and that
    import would put new code's artifacts under the old fingerprint. Recompute uncached and compare.
    """
    for fingerprint in (part_pipeline_fingerprint, realpart_pipeline_fingerprint, connector_pipeline_fingerprint):
        fresh = getattr(fingerprint, "__wrapped__", fingerprint)()
        if fresh != fingerprint():
            raise click.ClickException("CC_BUILD_STALE: part pipeline code changed while Blender was "
                                       "building; nothing was recorded, rebuild")


def part_pipeline_for(part: dict[str, Any]) -> str:
    if part.get("real"):
        return realpart_pipeline_fingerprint()
    if part.get("category") == "connector":
        return connector_pipeline_fingerprint()
    return part_pipeline_fingerprint()


def real_part_source(part: dict[str, Any], archive: Path | None = None) -> Path | None:
    """Absolute path of a real part's source mesh in the asset archive, or None when unavailable."""
    real = part.get("real")
    if not real:
        return None
    root = archive or find_asset_archive()
    if root is None:
        return None
    base = root.resolve()
    path = (base / real["archive_path"]).resolve()
    # An absolute or `..` path would read an arbitrary local file instead of the private archive.
    if not path.is_relative_to(base):
        raise click.ClickException(f"CC_ARCHIVE_PATH: {part['part_id']}: {real['archive_path']} leaves the asset archive")
    return path if path.is_file() else None


def stale_approvals(catalog: dict[str, Any]) -> list[str]:
    """Approved real parts whose approval pinned a different part pipeline than the current one."""
    current = realpart_pipeline_fingerprint()
    return sorted(p["part_id"] for p in catalog["parts"]
                  if p.get("real") and p.get("status") == "approved"
                  and p["real"].get("approved_pipeline") != current)


def real_source_unchanged(part: dict[str, Any]) -> bool:
    """False when the asset archive is available but a real part's source is missing from it or no
    longer has its recorded hash.

    Cached artifacts are only reusable while they can still be reproduced from the archive; a missing
    or replaced source forces a rebuild, which then fails with CC_ARCHIVE_MISSING or
    CC_REALPART_SOURCE_CHANGED. Without any archive the recorded build state is the best evidence
    available and the cache is kept.
    """
    if not part.get("real"):
        return True
    archive = find_asset_archive()
    if archive is None:
        return True
    source = real_part_source(part, archive)
    if source is None:
        return False
    digest = hashlib.sha256()
    with source.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == part["real"]["sha256"]


def assembly_pipeline_fingerprint() -> str:
    """Fingerprint assembly code separately from the baked skeleton and parts."""
    package = Path(__file__).resolve().parents[1]
    names = ("blender/ops_assemble.py", "blender/rigkit.py", "blender/frame.py")
    payload = {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in names}
    payload["contract"] = "cc-gen-3/blender-5.2/assembled-glTF-v1"
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _part_source_state(part: dict[str, Any]) -> dict[str, Any]:
    """Stable compiled part input, excluding paths/results written by a build."""
    return {key: value for key, value in part.items() if key != "asset"}


def _part_artifact_fingerprints(part: dict[str, Any], out: Path,
                                asset: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Hash the two part transports consumed by assembly and Unity."""
    asset = asset if asset is not None else part.get("asset", {})
    result: dict[str, dict[str, Any]] = {}
    names = ("fbx", "glb", "albedo_png") if isinstance(asset, dict) and asset.get("albedo_png") else ("fbx", "glb")
    for name in names:
        relative = asset.get(name) if isinstance(asset, dict) else None
        if not isinstance(relative, str) or not relative:
            raise ReviewError(f"CC_PART_ASSET_MISSING: {part['part_id']}: {name}")
        path = out / relative
        if not path.is_file():
            raise ReviewError(f"CC_PART_ASSET_MISSING: {part['part_id']}: {relative}")
        result[name] = {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def part_build_state(part: dict[str, Any], out: Path, *, asset: dict[str, Any] | None = None,
                     pipeline: str | None = None) -> dict[str, Any]:
    """Current source, producer, and exact built bytes for one part."""
    return {
        "pipeline": pipeline or part_pipeline_for(part),
        "source": _part_source_state(part),
        "artifacts": _part_artifact_fingerprints(part, out, asset),
    }


def _review_recipe(catalog: dict[str, Any], skeleton: dict[str, Any]) -> dict[str, Any]:
    index = next((index for index, item in enumerate(catalog["skeletons"])
                  if item["skeleton_id"] == skeleton["skeleton_id"]), None)
    if index is None:
        raise ReviewError(f"CC_BUILD_STALE: unknown skeleton {skeleton['skeleton_id']}")
    return generate_for_skeleton(catalog, skeleton["skeleton_id"], index + 1)


def _used_part_ids(recipe: dict[str, Any]) -> set[str]:
    used = {fill["part_id"] for fill in recipe["fills"]}
    used.update(fill["connector_part_id"] for fill in recipe["fills"] if fill["connector_part_id"])
    return used


def assembly_part_states(catalog: dict[str, Any], recipe: dict[str, Any], out: Path,
                         *, pipeline: str | None = None) -> dict[str, dict[str, Any]]:
    """Fingerprint the exact part sources and files consumed by an assembly."""
    parts = {part["part_id"]: part for part in catalog["parts"]}
    result = {}
    for part_id in sorted(_used_part_ids(recipe)):
        if part_id not in parts:
            raise ReviewError(f"CC_BUILD_STALE: assembly references missing part {part_id}")
        result[part_id] = part_build_state(parts[part_id], out, pipeline=pipeline)
    return result


def _assembly_state(catalog: dict[str, Any], recipe: dict[str, Any], out: Path,
                    base_skeleton_fingerprint: str,
                    part_states: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    used = _used_part_ids(recipe)
    states = part_states or assembly_part_states(catalog, recipe, out)
    return {
        "pipeline": assembly_pipeline_fingerprint(),
        "skeleton": base_skeleton_fingerprint,
        "recipe": recipe,
        "parts": {part_id: states[part_id] for part_id in sorted(used)},
    }


def _build_settings(catalog: dict, skeleton: dict) -> dict:
    return {"gait": _gait(catalog, skeleton["locomotion_hint"]),
            "pipeline": build_pipeline_fingerprint()}


def skeleton_source_hash(catalog: dict, skeleton: dict) -> str:
    return source_fingerprint(skeleton, skeleton_profiles(catalog, skeleton),
                              _build_settings(catalog, skeleton))


def skeleton_polish_hash(catalog: dict, skeleton: dict) -> str:
    sid = skeleton["skeleton_id"]
    polish = resolve_polish(paths().work / "polish" / "skeletons" / sid, sid,
                            skeleton_source_hash(catalog, skeleton))
    return hashlib.sha256(polish.read_bytes()).hexdigest() if polish else ""


def skeleton_content_hash(catalog: dict, skeleton: dict, out: Path) -> str:
    asset = skeleton["asset"]
    if asset.get("polish_fingerprint", "") != skeleton_polish_hash(catalog, skeleton):
        raise ReviewError(f"CC_BUILD_STALE: {skeleton['skeleton_id']}: authored polish changed; rebuild")
    required = [out / asset[key] for key in ("fbx", "glb", "blend", "motion") if asset.get(key)]
    if len(required) != 4:
        raise ReviewError(f"CC_REVIEW_ASSET_MISSING: {skeleton['skeleton_id']} requires FBX, GLB, blend and motion")
    settings = _build_settings(catalog, skeleton)
    if asset.get("assembled_glb"):
        required.append(out / asset["assembled_glb"])
        recipe = _review_recipe(catalog, skeleton)
        part_states = assembly_part_states(catalog, recipe, out)
        required.extend(out / fingerprint["path"]
                        for state in part_states.values()
                        for fingerprint in state["artifacts"].values())
        settings = settings | {
            "assembly": {
                "pipeline": assembly_pipeline_fingerprint(),
                "recipe": recipe,
                "parts": part_states,
            }
        }
    return content_fingerprint(skeleton, skeleton_profiles(catalog, skeleton),
                               settings, required)


def _base_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in asset.items() if key != "assembled_glb"}


def _cached_base_fingerprint(old: dict[str, Any], state: dict[str, Any]) -> str | None:
    """Last published fingerprint of a skeleton's base artifacts, without assembled.glb."""
    asset = old.get("asset")
    if not isinstance(asset, dict):
        return None
    if asset.get("assembled_glb"):
        return (state.get("assemblies") or {}).get(old.get("skeleton_id"), {}).get("skeleton")
    return old.get("content_fingerprint") or None


def _current_base_fingerprint(
    catalog: dict[str, Any], skeleton: dict[str, Any], asset: dict[str, Any], out: Path
) -> str | None:
    probe = {**skeleton, "asset": _base_asset(asset)}
    try:
        return skeleton_content_hash(catalog, probe, out)
    except ReviewError:
        return None


def build_jobs(catalog: dict[str, Any], out: Path, only: set[str] | None = None) -> list[dict[str, Any]]:
    templates = {t["template_id"]: t for t in catalog["branch_templates"]}
    masters = paths().work / "masters"
    jobs = []
    for s in catalog["skeletons"]:
        sid = s["skeleton_id"]
        if only and sid not in only:
            continue
        source_hash = skeleton_source_hash(catalog, s)
        polish = resolve_polish(paths().work / "polish" / "skeletons" / sid, sid, source_hash)
        jobs.append({"op": "skeleton", "args": {
            "skeleton": s, "gait_profile": _gait(catalog, s["locomotion_hint"]),
            "binding_profiles": skeleton_profiles(catalog, s), "source_fingerprint": source_hash,
            "out_fbx": str(out / "skeletons" / sid / f"{sid}.fbx"),
            "out_glb": str(out / "skeletons" / sid / f"{sid}.glb"),
            "out_blend": str(out / "skeletons" / sid / f"{sid}.blend"),
            "out_motion": str(out / "skeletons" / sid / "motion.json"),
            "polish_blend": str(polish) if polish else "",
        }})
    for p in catalog["parts"]:
        pid = p["part_id"]
        if only and pid not in only:
            continue
        kind = "connectors" if p["category"] == "connector" else "parts"
        profile = next((pr for pr in catalog.get("binding_profiles", [])
                        if pr["binding_profile_id"] == p.get("binding_profile_id")
                        and pr["binding_profile_version"] == p.get("binding_profile_version")), None)
        if p.get("real"):
            source = real_part_source(p)
            jobs.append({"op": "realpart", "args": {
                "part": p, "template": profile or templates[p["template"]],
                "source_path": str(source) if source else "",
                "out_fbx": str(out / kind / pid / f"{pid}.fbx"),
                "out_glb": str(out / kind / pid / f"{pid}.glb"),
                "out_albedo_png": str(out / kind / pid / f"{pid}_albedo.png"),
                "out_blend": str(masters / kind / pid / "master.blend"),
            }})
            continue
        if p["source"] not in ("placeholder", "reference"):
            continue
        op = "connector" if p["category"] == "connector" else "placeholder"
        jobs.append({"op": op, "args": {
            "part": p, "template": profile or templates[p["template"]],
            "out_fbx": str(out / kind / pid / f"{pid}.fbx"),
            "out_glb": str(out / kind / pid / f"{pid}.glb"),
            "out_blend": str(masters / kind / pid / "master.blend"),
        }})
    return jobs


def apply_results(catalog: dict[str, Any], out: Path, results: list[dict[str, Any]]) -> list[str]:
    problems = []
    skel = {s["skeleton_id"]: s for s in catalog["skeletons"]}
    parts = {p["part_id"]: p for p in catalog["parts"]}
    for r in results:
        if not r.get("ok", True):
            problems.append(f"CC_BUILD_FAILED: {r.get('error', 'unknown build failure')}")
            continue
        res = r["result"]
        if r["op"] == "skeleton":
            s = skel[res["skeleton_id"]]
            sid = s["skeleton_id"]
            s["asset"] = {"fbx": f"skeletons/{sid}/{sid}.fbx", "glb": f"skeletons/{sid}/{sid}.glb", "clips": res["clips"]}
            if catalog["schema_version"] == "3.0.0":
                s["asset"].update(blend=f"skeletons/{sid}/{sid}.blend", motion=f"skeletons/{sid}/motion.json")
                s["asset"]["polish_fingerprint"] = skeleton_polish_hash(catalog, s)
                motion_path = out / s["asset"]["motion"]
                baked_source = json.loads(motion_path.read_text(encoding="utf-8")).get("source_fingerprint")
                s["source_fingerprint"] = baked_source
                if baked_source != skeleton_source_hash(catalog, s):
                    problems.append(f"CC_BUILD_STALE: {sid}: source or pipeline changed during the build")
                if all((out / s["asset"][key]).is_file() for key in ("fbx", "glb", "blend", "motion")):
                    s["content_fingerprint"] = skeleton_content_hash(catalog, s, out)
            if res["bones"] != len(s["bones"]):
                problems.append(f"CC_BONE_COUNT: {sid}: blender {res['bones']} != catalog {len(s['bones'])}")
        else:
            p = parts[res["part_id"]]
            pid = p["part_id"]
            kind = "connectors" if p["category"] == "connector" else "parts"
            p["asset"] = {"fbx": f"{kind}/{pid}/{pid}.fbx", "glb": f"{kind}/{pid}/{pid}.glb", "triangles": res["triangles"]}
            if res["triangles"] > p["max_triangles"]:
                problems.append(f"CC_BUDGET_TRIS: {pid}: {res['triangles']} > {p['max_triangles']}")
            if r["op"] == "realpart":
                if res.get("texture"):
                    p["asset"]["albedo_png"] = f"{kind}/{pid}/{pid}_albedo.png"
                problems.extend(real_part_problems(p, res))
    return problems


FIT_DRIFT_M = 0.002


def real_part_problems(part: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """A real part must rebuild to the envelope its source record declares."""
    pid = part["part_id"]
    problems = []
    if result.get("source_sha256") != part["real"]["sha256"]:
        problems.append(f"CC_REALPART_SOURCE_CHANGED: {pid}")
    measured = result.get("fit", {}).get("dimensions_m", [])
    declared = part["dimensions_m"]
    if len(measured) != 3 or any(abs(float(a) - float(b)) > FIT_DRIFT_M for a, b in zip(measured, declared)):
        problems.append(f"CC_PART_FIT_DRIFT: {pid}: built {measured} != declared {declared}; "
                        f"run `critter part refit {pid}` to refresh the source record")
    if result.get("max_influences", 0) > 4 or abs(result.get("min_weight_sum", 0) - 1) > 1e-4 \
            or abs(result.get("max_weight_sum", 0) - 1) > 1e-4:
        problems.append(f"CC_PART_WEIGHTS: {pid}")
    return problems


def write_manifest(out: Path) -> dict[str, Any]:
    files = []
    for f in sorted(out.rglob("*")):
        if f.is_file() and f.name != "manifest.json":
            files.append({
                "path": f.relative_to(out).as_posix(),
                "bytes": f.stat().st_size,
                "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
            })
    manifest = {"schema_version": "3.0.0", "files": files}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


@click.group()
def library() -> None:
    """Build and package asset libraries."""


@library.command("build")
@click.option("--out", "out_root", type=click.Path(path_type=Path), default=None, help="Output root (default: library/)")
@click.option("--clean/--no-clean", default=True, show_default=True)
def library_build(out_root: Path | None, clean: bool) -> None:
    """Build fresh skeleton, reference, and draft-review assembly artifacts."""
    catalog = _catalog()
    # Pin the part pipelines to the code as it is now: the fingerprints are cached, so sources edited
    # while Blender runs cannot be recorded as the code that produced this build.
    part_pipeline_fingerprint()
    realpart_pipeline_fingerprint()
    connector_pipeline_fingerprint()
    stale = stale_approvals(catalog)
    if stale:
        raise click.ClickException(
            f"CC_APPROVAL_STALE: {', '.join(stale)} were approved for a different real-part pipeline, so "
            "their rebuilt geometry is unreviewed. Run `critter part refit <id>` (back to draft), then "
            "build, `critter part review` and `critter part approve` again.")
    out = library_dir(catalog, out_root)
    if clean and out.exists():
        output_root = (out_root or paths().library_out).resolve()
        if out.resolve().parent != output_root:
            raise click.ClickException(f"refusing to clean library outside its output root: {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    previous_path = out / "catalog.json"
    state_path = out / ".build-state.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.is_file() else {}
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}

    def files_exist(asset: dict[str, Any], names: tuple[str, ...]) -> bool:
        return all(isinstance(asset.get(name), str) and (out / asset[name]).is_file() for name in names)

    fresh: set[str] = set()
    old_skeletons = {item["skeleton_id"]: item for item in previous.get("skeletons", [])}
    if not clean:
        for skeleton in catalog["skeletons"]:
            old = old_skeletons.get(skeleton["skeleton_id"], {})
            asset = old.get("asset")
            source = skeleton_source_hash(catalog, skeleton)
            if (isinstance(asset, dict) and old.get("source_fingerprint") == source
                    and asset.get("polish_fingerprint", "") == skeleton_polish_hash(catalog, skeleton)
                    and files_exist(asset, ("fbx", "glb", "blend", "motion"))):
                expected = _cached_base_fingerprint(old, state)
                current = _current_base_fingerprint(catalog, skeleton, asset, out)
                if expected is None or current is None or current != expected:
                    # Source is unchanged, but the cached bytes are not the last
                    # published build. Rebuild instead of blessing the disk copy.
                    continue
                skeleton["asset"] = asset
                if asset.get("assembled_glb") and not (out / asset["assembled_glb"]).is_file():
                    skeleton["asset"].pop("assembled_glb")
                skeleton["source_fingerprint"] = source
                skeleton["content_fingerprint"] = old.get("content_fingerprint", "")
                fresh.add(skeleton["skeleton_id"])
        old_parts = {item["part_id"]: item for item in previous.get("parts", [])}
        for part in catalog["parts"]:
            old = old_parts.get(part["part_id"], {})
            asset = old.get("asset")
            try:
                expected_part_state = (part_build_state(part, out, asset=asset)
                                       if isinstance(asset, dict) else None)
            except ReviewError:
                expected_part_state = None
            if (expected_part_state is not None
                    and state.get("parts", {}).get(part["part_id"]) == expected_part_state
                    and real_source_unchanged(part)):
                part["asset"] = asset
                fresh.add(part["part_id"])
    jobs = build_jobs(catalog, out)
    pending = [job for job in jobs if (job["args"]["skeleton"]["skeleton_id"] if job["op"] == "skeleton"
                                       else job["args"]["part"]["part_id"]) not in fresh]
    missing = [job["args"]["part"]["part_id"] for job in pending
               if job["op"] == "realpart" and not job["args"]["source_path"]]
    if missing:
        archive = find_asset_archive()
        where = (f"missing from the asset archive at {archive} (moved or deleted?)" if archive else
                 "unavailable: clone synaptic-sea-asset-archive next to this repository, or set "
                 "CRITTER_ASSET_ARCHIVE (or [paths].asset_archive in critter.toml) to its root")
        raise click.ClickException(f"CC_ARCHIVE_MISSING: real part source(s) for {', '.join(missing)} {where}")
    click.echo(f"building {len(pending)} pending / {len(jobs)} assets with Blender -> {out}")
    try:
        result = run_op("batch", {"jobs": pending}) if pending else {"results": []}
    except BlenderError as e:
        raise click.ClickException(str(e))
    require_part_pipelines_unchanged()
    problems = apply_results(catalog, out, result["results"])
    if problems:
        for problem in problems:
            click.echo(problem, err=True)
        raise click.ClickException(f"{len(problems)} problem(s)")
    state["parts"] = {
        part["part_id"]: part_build_state(part, out)
        for part in catalog["parts"]
        if files_exist(part.get("asset", {}), ("fbx", "glb"))
    }
    # Persist a resumable base build before the independent assembled-review
    # pass. If one assembly fails, --no-clean can reuse these verified assets.
    for skeleton in catalog["skeletons"]:
        # Publish assemblies only after verifying their current recipe/parts.
        skeleton.get("asset", {}).pop("assembled_glb", None)
        skeleton["content_fingerprint"] = skeleton_content_hash(catalog, skeleton, out)
    (out / "catalog.json").write_text(dumps(catalog), encoding="utf-8", newline="\n")
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    assembly_jobs: list[dict[str, Any]] = []
    part_states = state["parts"]
    rebuilt_parts = {
        job["args"]["part"]["part_id"] for job in pending if job["op"] in ("placeholder", "realpart", "connector")
    }
    for index, skeleton in enumerate(catalog["skeletons"]):
        sid = skeleton["skeleton_id"]
        recipe = generate_for_skeleton(catalog, sid, index + 1)
        used = _used_part_ids(recipe)
        assembly_state = _assembly_state(
            catalog, recipe, out, skeleton["content_fingerprint"], part_states
        )
        assembled = out / "skeletons" / sid / "assembled.glb"
        if (not clean and not used.intersection(rebuilt_parts) and assembled.is_file()
                and state.get("assemblies", {}).get(sid) == assembly_state):
            skeleton.setdefault("asset", {})["assembled_glb"] = assembled.relative_to(out).as_posix()
            try:
                actual = skeleton_content_hash(catalog, skeleton, out)
            except ReviewError:
                actual = None
            if actual == old_skeletons.get(sid, {}).get("content_fingerprint"):
                continue
            skeleton["asset"].pop("assembled_glb", None)
        assembly_jobs.append({"op": "assemble", "args": {
            "skeleton": skeleton,
            "parts": {part["part_id"]: part for part in catalog["parts"] if part["part_id"] in used},
            "library_dir": str(out), "recipe": recipe,
            "gait_profile": _gait(catalog, skeleton["locomotion_hint"]), "out_glb": str(assembled),
        }})
    click.echo(f"assembling {len(assembly_jobs)} pending / {len(catalog['skeletons'])} draft-review GLBs")
    try:
        assembled_results = run_op("batch", {"jobs": assembly_jobs}) if assembly_jobs else {"results": []}
    except BlenderError as e:
        raise click.ClickException(str(e))
    for job, result_item in zip(assembly_jobs, assembled_results["results"], strict=True):
        if not result_item.get("ok"):
            raise click.ClickException(f"CC_ASSEMBLY_FAILED: {job['args']['skeleton']['skeleton_id']}: {result_item.get('error')}")
        skeleton = job["args"]["skeleton"]
        sid = skeleton["skeleton_id"]
        skeleton.setdefault("asset", {})["assembled_glb"] = f"skeletons/{sid}/assembled.glb"
        state.setdefault("assemblies", {})[sid] = _assembly_state(
            catalog, job["args"]["recipe"], out, skeleton["content_fingerprint"], part_states
        )
    for skeleton in catalog["skeletons"]:
        skeleton["content_fingerprint"] = skeleton_content_hash(catalog, skeleton, out)
    (out / "catalog.json").write_text(dumps(catalog), encoding="utf-8", newline="\n")
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    manifest = write_manifest(out)
    click.echo(f"catalog.json + {len(manifest['files'])} files written")


@library.command("pack")
@click.option("--out", "out_root", type=click.Path(path_type=Path), default=None)
def library_pack(out_root: Path | None) -> None:
    """Zip a built library as critter-library-<id>-v<ver>.zip (release asset)."""
    catalog, src = _built_catalog(out_root, require_assemblies=True)
    dist = paths().root / "dist"
    dist.mkdir(exist_ok=True)
    zpath = dist / f"critter-library-{catalog['library_id']}-v{catalog['version']}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(src).as_posix())
    click.echo(f"wrote {zpath}")


@click.group()
def blender() -> None:
    """Run individual Blender ops headless or print MCP snippets."""


@blender.command("snippet")
@click.argument("asset_id")
def blender_snippet(asset_id: str) -> None:
    """Print Python for the Blender MCP that rebuilds ASSET_ID (skeleton or placeholder part) live."""
    catalog = _catalog()
    jobs = build_jobs(catalog, paths().work / "mcp_preview", only={asset_id})
    if not jobs:
        raise click.ClickException(f"unknown asset {asset_id}")
    args = dict(jobs[0]["args"])
    args.pop("out_blend", None)
    click.echo(snippet(jobs[0]["op"], args))


@click.group()
def assemble() -> None:
    """Assemble recipes in Blender (review renders, baked single-rig export)."""


def _built_catalog(out_root: Path | None = None, *, require_assemblies: bool = False) -> tuple[dict[str, Any], Path]:
    current = _catalog()
    src = library_dir(current, out_root)
    path = src / "catalog.json"
    if not path.exists():
        raise click.ClickException("library not built; run `critter library build` first")
    built = json.loads(path.read_text(encoding="utf-8"))
    state_path = src / ".build-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    if (built.get("schema_version"), built.get("version")) != (current["schema_version"], current["version"]):
        raise click.ClickException("CC_LIBRARY_VERSION: rebuild the active library")
    current_parts = {part["part_id"]: part for part in current["parts"]}
    built_parts = {part["part_id"]: part for part in built.get("parts", [])}
    if set(current_parts) != set(built_parts) or len(built_parts) != len(built.get("parts", [])):
        raise click.ClickException("CC_BUILD_STALE: part inventory changed; rebuild")
    for part_id, current_part in current_parts.items():
        built_part = built_parts[part_id]
        if _part_source_state(built_part) != _part_source_state(current_part):
            raise click.ClickException(f"CC_BUILD_STALE: {part_id}: part source changed; rebuild")
        try:
            actual = part_build_state(current_part, src, asset=built_part.get("asset"))
        except ReviewError as exc:
            raise click.ClickException(str(exc)) from exc
        if state.get("parts", {}).get(part_id) != actual:
            raise click.ClickException(f"CC_BUILD_STALE: {part_id}: part pipeline or artifacts changed; rebuild")
    current_by_id = {s["skeleton_id"]: s for s in current["skeletons"]}
    for s in built["skeletons"]:
        if s["skeleton_id"] not in current_by_id or s.get("source_fingerprint") != skeleton_source_hash(current, current_by_id[s["skeleton_id"]]):
            raise click.ClickException(f"CC_BUILD_STALE: {s['skeleton_id']}; run `critter library build`")
        if s.get("asset", {}).get("polish_fingerprint", "") != skeleton_polish_hash(current, current_by_id[s["skeleton_id"]]):
            raise click.ClickException(f"CC_BUILD_STALE: {s['skeleton_id']}: authored polish changed; rebuild")
        if require_assemblies and not s.get("asset", {}).get("assembled_glb"):
            raise click.ClickException(
                f"CC_REVIEW_ASSEMBLY_MISSING: {s['skeleton_id']}: release package requires an assembled GLB"
            )
        try:
            if s.get("asset", {}).get("assembled_glb"):
                base = copy.deepcopy(s)
                base["asset"].pop("assembled_glb", None)
                base_fingerprint = skeleton_content_hash(built, base, src)
                recipe = _review_recipe(built, s)
                expected_assembly = _assembly_state(built, recipe, src, base_fingerprint)
                if state.get("assemblies", {}).get(s["skeleton_id"]) != expected_assembly:
                    raise click.ClickException(
                        f"CC_BUILD_STALE: {s['skeleton_id']}: assembly inputs changed; rebuild"
                    )
            actual_content = skeleton_content_hash(built, s, src)
        except ReviewError as exc:
            raise click.ClickException(str(exc)) from exc
        if s.get("content_fingerprint") != actual_content:
            raise click.ClickException(
                f"CC_BUILD_STALE: {s['skeleton_id']}: built content changed; rebuild"
            )
    if set(current_by_id) != {s["skeleton_id"] for s in built["skeletons"]}:
        raise click.ClickException("CC_BUILD_STALE: skeleton inventory changed; rebuild")
    return built, src


def _assemble_job(cat: dict[str, Any], src: Path, recipe: dict[str, Any], **extra: Any) -> dict[str, Any]:
    skel = next(s for s in cat["skeletons"] if s["skeleton_id"] == recipe["skeleton_id"])
    used = {f["part_id"] for f in recipe["fills"]} | {f["connector_part_id"] for f in recipe["fills"] if f["connector_part_id"]}
    return {"op": "assemble", "args": {
        "skeleton": skel, "parts": {p["part_id"]: p for p in cat["parts"] if p["part_id"] in used},
        "library_dir": str(src), "recipe": recipe, "gait_profile": _gait(cat, skel["locomotion_hint"]), **extra}}


@assemble.command("preview")
@click.option("--pool", "pool_id", default="any", show_default=True)
@click.option("--seeds", default="1..12", show_default=True)
@click.option("--clip", default="walk", show_default=True)
@click.option("--frame", default=4, show_default=True)
@click.option("--view", type=click.Choice(["iso", "front", "side", "three_quarter"]), default="three_quarter", show_default=True)
@click.option("--size", default=384, show_default=True)
@click.option("--sheet", type=click.Path(path_type=Path), default=None, help="Contact sheet PNG (default work/review/<pool>.png)")
def assemble_preview(pool_id: str, seeds: str, clip: str, frame: int, view: str, size: int, sheet: Path | None) -> None:
    """Render generated creatures and compose a labelled contact sheet."""
    from PIL import Image, ImageDraw

    from ..cli import _parse_range
    from ..recipes.generator import generate

    cat, src = _built_catalog()
    renders = paths().work / "review" / "renders"
    jobs, labels = [], []
    for s in _parse_range(seeds):
        r = generate(cat, pool_id, s)
        png = renders / f"{r['recipe_id']}_{clip}_{view}.png"
        jobs.append(_assemble_job(cat, src, r, out_png=str(png), clip=clip, frame=frame, view=view, size=size))
        labels.append((png, f"{pool_id} #{s}  {r['skeleton_id']}"))
    click.echo(f"rendering {len(jobs)} creatures ...")
    try:
        run_op("batch", {"jobs": jobs})
    except BlenderError as e:
        raise click.ClickException(str(e))
    cols = min(4, len(labels))
    rows = (len(labels) + cols - 1) // cols
    img = Image.new("RGB", (cols * size, rows * (size + 22)), (18, 19, 24))
    draw = ImageDraw.Draw(img)
    for i, (png, label) in enumerate(labels):
        x, y = (i % cols) * size, (i // cols) * (size + 22)
        img.paste(Image.open(png).convert("RGB"), (x, y + 22))
        draw.text((x + 6, y + 5), label, fill=(220, 210, 210))
    sheet = sheet or paths().work / "review" / f"{pool_id}_{clip}_{view}.png"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    img.save(sheet)
    click.echo(f"wrote {sheet}")


@assemble.command("export")
@click.option("--pool", "pool_id", required=True)
@click.option("--seed", type=int, required=True)
@click.option("--out", "out_dir", type=click.Path(path_type=Path), default=None)
def assemble_export(pool_id: str, seed: int, out_dir: Path | None) -> None:
    """Bake one generated creature to a single-skeleton FBX + GLB with the full clip set."""
    from ..recipes.generator import generate

    cat, src = _built_catalog()
    r = generate(cat, pool_id, seed)
    out_dir = out_dir or paths().work / "baked"
    base = out_dir / r["recipe_id"]
    try:
        res = run_op("assemble", _assemble_job(cat, src, r, out_fbx=str(base) + ".fbx", out_glb=str(base) + ".glb")["args"])
    except BlenderError as e:
        raise click.ClickException(str(e))
    (out_dir / f"{r['recipe_id']}.recipe.json").write_text(json.dumps(r, indent=2), encoding="utf-8")
    click.echo(f"baked {r['recipe_id']} ({res['result']['triangles']} tris) -> {base}.fbx/.glb")
