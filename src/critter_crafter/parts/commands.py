"""`critter part ...`: real parts fitted from sourced meshes in the private asset archive.

Source records (``data/parts/<part_id>.part.json``) hold only the archive-relative path, the source
SHA-256, the fit parameters and the measured envelope. The mesh, its texture and every derivative stay
in the archive or in build output (library/, work/): Meshy outputs may be paid-private and this
repository is public.
"""

from __future__ import annotations

import copy
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

import click

from ..blender.runner import BlenderError, run_op
from ..config import find_asset_archive, paths
from ..library.catalog import compile_part
from ..recipes.generator import generate_for_skeleton, part_accepted
from .fit import AXES, TEMPLATE_DEFAULTS

CATEGORY_BY_TEMPLATE = {"limb3": "limb", "insect_leg4": "limb", "head1": "head", "tentacle8": "appendage",
                        "appendage1": "appendage", "core1": "core", "spine3": "core"}
TRIANGLES_BY_CATEGORY = {"head": 3500, "core": 4000}
MEASURED_KEYS = ("dimensions_m", "uniform_scale", "radial_scale", "radial_scale_wanted", "nominal_girth_m",
                 "up_source", "end_to_end_bend_deg", "strain_p99", "strain_max", "islands")
REVIEW_CLIPS = (("idle", 0.0), ("walk", 0.25), ("walk", 0.75), ("attack", 0.5), ("hit", 0.5))

# Deformation acceptance, relative to the placeholder part the real part replaces on the same branch.
STRAIN_P99_LIMIT = 1.6
STRAIN_P01_LIMIT = 0.6
FLIPPED_LIMIT = 0.005


def _catalog() -> dict[str, Any]:
    from ..library.commands import _catalog as library_catalog
    return library_catalog()


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _profile_index(catalog: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(p["binding_profile_id"], p["binding_profile_version"]): p for p in catalog["binding_profiles"]}


def _profile(catalog: dict[str, Any], profile_id: str, version: str | None = None) -> dict[str, Any]:
    """The named binding profile: the pinned ``version`` when given, else the latest one."""
    if version is not None:
        exact = _profile_index(catalog).get((profile_id, version))
        if exact is None:
            raise click.ClickException(f"unknown binding profile {profile_id}@{version}")
        return exact
    versions = [p for p in catalog["binding_profiles"] if p["binding_profile_id"] == profile_id]
    if not versions:
        known = sorted({p["binding_profile_id"] for p in catalog["binding_profiles"]})
        raise click.ClickException(f"unknown binding profile {profile_id}; known: {', '.join(known)}")
    return max(versions, key=lambda p: tuple(int(x) for x in p["binding_profile_version"].split(".")))


def default_length(catalog: dict[str, Any], profile: dict[str, Any]) -> float:
    """Median branch length for the exact profile (id, version and hash, as acceptance requires), so
    one part covers the most branches (0.8-1.25x)."""
    identity = (profile["binding_profile_id"], profile["binding_profile_version"], profile["binding_profile_hash"])
    lengths = [b["length_m"] for s in catalog["skeletons"] for b in s["branches"]
               if (b["binding_profile_id"], b["binding_profile_version"], b["binding_profile_hash"]) == identity]
    if not lengths:
        raise click.ClickException(f"no skeleton branch uses {profile['binding_profile_id']}")
    return round(round(statistics.median(lengths) / 0.05) * 0.05, 2)


def _resolve_source(source: str) -> tuple[Path, str]:
    archive = find_asset_archive()
    if archive is None:
        raise click.ClickException(
            "CC_ARCHIVE_MISSING: clone synaptic-sea-asset-archive next to this repository or set "
            "CRITTER_ASSET_ARCHIVE to its root")
    path = Path(source)
    if not path.is_absolute():
        path = archive / source
    path = path.resolve()
    try:
        relative = path.relative_to(archive.resolve()).as_posix()
    except ValueError:
        raise click.ClickException(f"{path} is not inside the asset archive {archive}") from None
    if not path.is_file():
        raise click.ClickException(f"source mesh not found: {path}")
    return path, relative


def part_record(*, part_id: str, profile: dict[str, Any], category: str, length_m: float, side: str,
                tags: list[str], roles: list[str], archive_path: str, sha256: str, fit: dict[str, Any],
                max_triangles: int, albedo: str, provenance: dict[str, Any],
                dimensions_m: list[float] | None = None) -> dict[str, Any]:
    girth = round(float(profile["girth_ratio"]) * length_m, 6)
    return {
        "schema_version": "3.0.0",
        "part_id": part_id,
        "category": category,
        "template": profile["template"],
        "binding_profile_id": profile["binding_profile_id"],
        "binding_profile_version": profile["binding_profile_version"],
        "side": side,
        "girth_m": girth,
        "species_tags": sorted(tags),
        "roles": sorted(roles),
        "size_class": None,
        "status": "draft",
        "style_profile": "flesh_stylized_v1",
        "dimensions_m": dimensions_m or [girth, girth, length_m],
        "length_m": length_m,
        "budget": {"max_triangles": max_triangles, "max_material_slots": 1, "texture_size": 1024},
        "fallback": {"primitive": "capsule", "albedo": albedo},
        "provenance": provenance,
        "real": {"archive_path": archive_path, "sha256": sha256, "fit": fit},
    }


def _sheet(pngs: list[str], labels: list[str], out: Path, cols: int, size: int) -> None:
    from PIL import Image, ImageDraw

    rows = (len(pngs) + cols - 1) // cols
    img = Image.new("RGB", (cols * size, rows * (size + 20)), (18, 19, 24))
    draw = ImageDraw.Draw(img)
    for i, (png, label) in enumerate(zip(pngs, labels)):
        x, y = (i % cols) * size, (i // cols) * (size + 20)
        img.paste(Image.open(png).convert("RGB").resize((size, size)), (x, y + 20))
        draw.text((x + 6, y + 4), label, fill=(220, 210, 210))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)


@click.group()
def part() -> None:
    """Real parts fitted from sourced meshes (Meshy downloads in the asset archive)."""


@part.command("import")
@click.argument("source")
@click.option("--part-id", required=True, help="New part id, e.g. meshy_insect_leg_a_v1")
@click.option("--profile", "profile_id", required=True, help="Binding profile, e.g. limb3_plantigrade")
@click.option("--axis", type=click.Choice(sorted(AXES)), required=True,
              help="Source-mesh axis (glTF frame) pointing from the attachment end to the tip")
@click.option("--up", default="auto", show_default=True,
              help="Source axis for the part's dorsal +Y, or 'auto' (the convex side of the modelled bend)")
@click.option("--up-fallback", default=None, type=click.Choice(sorted(AXES)))
@click.option("--length", "length_m", type=float, default=None, help="Chain length (default: median branch)")
@click.option("--category", default=None)
@click.option("--side", type=click.Choice(["symmetric", "L", "R", "C"]), default="symmetric", show_default=True)
@click.option("--tag", "tags", multiple=True)
@click.option("--role", "roles", multiple=True)
@click.option("--joints", default=None, help="Natural interior joint fractions, comma separated")
@click.option("--trim", default=None, help="Root,tip trim quantiles (default 0.005,0.995)")
@click.option("--radial-scale", default=None, help="'auto' or a number")
@click.option("--weights", type=click.Choice(["smooth", "flesh", "jointed", "single"]), default=None)
@click.option("--mirror-x", is_flag=True)
@click.option("--meshy-task", default=None, help="Meshy task id (defaults to the source file name)")
@click.option("--albedo", default="#9b6874", show_default=True, help="Fallback tint")
@click.option("--force", is_flag=True, help="Overwrite an existing source record (keeps nothing)")
def part_import(source: str, part_id: str, profile_id: str, axis: str, up: str, up_fallback: str | None,
                length_m: float | None, category: str | None, side: str, tags: tuple[str, ...],
                roles: tuple[str, ...], joints: str | None, trim: str | None, radial_scale: str | None,
                weights: str | None, mirror_x: bool, meshy_task: str | None, albedo: str, force: bool) -> None:
    """Fit SOURCE (a mesh in the asset archive) onto a profile and write its source record."""
    record_path = paths().data / "parts" / f"{part_id}.part.json"
    if record_path.exists() and not force:
        raise click.ClickException(f"{record_path} exists; pass --force to refit it")
    catalog = _catalog()
    profile = _profile(catalog, profile_id)
    template = profile["template"]
    category = category or CATEGORY_BY_TEMPLATE.get(template)
    if category is None:
        raise click.ClickException(f"pass --category for template {template}")
    path, relative = _resolve_source(source)
    fit: dict[str, Any] = {"axis": axis}
    if up != "auto":
        if up not in AXES:
            raise click.ClickException(f"--up must be 'auto' or one of {sorted(AXES)}")
        fit["up"] = up
    if up_fallback:
        fit["up_fallback"] = up_fallback
    if joints:
        fit["joints_n"] = [float(x) for x in joints.split(",")]
    if trim:
        fit["trim_n"] = [float(x) for x in trim.split(",")]
    if radial_scale:
        fit["radial_scale"] = radial_scale if radial_scale == "auto" else float(radial_scale)
    if weights:
        fit["weights"] = weights
    if mirror_x:
        fit["mirror_x"] = True
    length = length_m or default_length(catalog, profile)
    provenance = {"source": "meshy", "meshy_task_id": meshy_task or path.name.split(".")[0],
                  "license": "meshy-output: may be paid-private; never publish mesh, texture or render bytes"}
    record = part_record(part_id=part_id, profile=profile, category=category, length_m=length, side=side,
                         tags=list(tags), roles=list(roles) or ["detail"], archive_path=relative,
                         sha256=_sha256(path), fit=fit,
                         max_triangles=TRIANGLES_BY_CATEGORY.get(category, 2500), albedo=albedo,
                         provenance=provenance)
    _fit_and_write(catalog, profile, record, record_path, path)
    click.echo(f"wrote {record_path} (status draft)")


def _fit_and_write(catalog: dict[str, Any], profile: dict[str, Any], record: dict[str, Any],
                   record_path: Path, source: Path) -> dict[str, Any]:
    """Run the real-part op for ``record``; store the measured envelope and write previews."""
    part_id = record["part_id"]
    compiled = compile_part(copy.deepcopy(record), _profile_index(catalog))
    out = paths().work / "parts" / part_id
    click.echo(f"fitting {record['real']['archive_path']} -> {part_id} "
               f"({profile['binding_profile_id']}, {record['length_m']} m) ...")
    try:
        result = run_op("realpart", {
            "part": compiled, "template": profile, "source_path": str(source),
            "out_fbx": str(out / f"{part_id}.fbx"), "out_glb": str(out / f"{part_id}.glb"),
            "out_albedo_png": str(out / f"{part_id}_albedo.png"), "preview_prefix": str(out / "preview"),
        })["result"]
    except BlenderError as e:
        raise click.ClickException(str(e))
    metrics = result["fit"]
    record["dimensions_m"] = metrics["dimensions_m"]
    record["real"]["measured"] = {key: metrics[key] for key in MEASURED_KEYS if key in metrics}
    record["real"]["measured"]["triangles"] = result["triangles"]
    _write_json(record_path, record)
    _sheet(result["previews"], [Path(p).stem.replace("preview_", "") for p in result["previews"]],
           out / "preview.png", cols=4, size=320)
    _write_json(out / "fit_report.json", {k: v for k, v in result.items() if k != "previews"})
    click.echo(json.dumps(record["real"]["measured"], indent=1))
    click.echo(f"preview: {out / 'preview.png'} (build output; never commit)")
    return result


@part.command("refit")
@click.argument("part_ids", nargs=-1)
@click.option("--all", "all_parts", is_flag=True, help="Refit every real part")
def part_refit(part_ids: tuple[str, ...], all_parts: bool) -> None:
    """Re-run the fit of existing records with their own parameters (after a fitter change or a
    CC_PART_FIT_DRIFT). The status is kept; a changed envelope changes the part, so review again."""
    from ..library.commands import real_part_source

    catalog = _catalog()
    real = {p["part_id"]: p for p in catalog["parts"] if p.get("real")}
    chosen = sorted(real) if all_parts else list(part_ids)
    unknown = [pid for pid in chosen if pid not in real]
    if not chosen or unknown:
        raise click.ClickException(f"name real parts or pass --all (unknown: {', '.join(unknown) or '-'})")
    for part_id in chosen:
        record_path = paths().data / "parts" / f"{part_id}.part.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        source = real_part_source(real[part_id])
        if source is None:
            raise click.ClickException(f"CC_ARCHIVE_MISSING: source of {part_id} not found")
        if _sha256(source) != record["real"]["sha256"]:
            raise click.ClickException(f"CC_REALPART_SOURCE_CHANGED: {part_id}; re-import it as a new part")
        before = list(record["dimensions_m"])
        profile = _profile(catalog, record["binding_profile_id"], record["binding_profile_version"])
        _fit_and_write(catalog, profile, record, record_path, source)
        changed = "unchanged" if before == record["dimensions_m"] else f"envelope {before} -> {record['dimensions_m']}"
        click.echo(f"refit {part_id}: {changed}")


@part.command("list")
def part_list() -> None:
    """Real parts: profile, length, status, and whether the archive source is available."""
    from ..library.commands import real_part_source

    catalog = _catalog()
    archive = find_asset_archive()
    rows = [p for p in catalog["parts"] if p.get("real")]
    click.echo(f"archive: {archive or 'NOT FOUND (set CRITTER_ASSET_ARCHIVE)'}")
    for p in rows:
        source = real_part_source(p, archive)
        qa = _read_qa(p["part_id"])
        verdict = "-" if qa is None else ("pass" if qa.get("passed") else "FAIL")
        click.echo(f"{p['part_id']:34s} {p['binding_profile_id']:24s} {p['length_m']:5.2f} m  "
                   f"{p['status']:8s} source={'ok' if source else 'missing'}  qa={verdict}")
    click.echo(f"{len(rows)} real part(s)")


def _qa_path(part_id: str) -> Path:
    return paths().work / "review" / "parts" / part_id / "qa.json"


def _read_qa(part_id: str) -> dict[str, Any] | None:
    path = _qa_path(part_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _review_recipe(catalog: dict[str, Any], skeleton: dict[str, Any], index: int, part: dict[str, Any]
                   ) -> tuple[dict[str, Any], dict[str, Any], list[str]] | None:
    """The skeleton's draft-review recipe with ``part`` on every branch that accepts it."""
    branches = {b["branch_id"]: b for b in skeleton["branches"]}
    for attempt in range(16):
        base = generate_for_skeleton(catalog, skeleton["skeleton_id"], index + 1 + 1000 * attempt)
        mixed = copy.deepcopy(base)
        replaced = []
        for fill in mixed["fills"]:
            branch = branches[fill["branch_id"]]
            if part_accepted(part, branch):
                replaced.append(fill["part_id"])
                fill["part_id"] = part["part_id"]
                fill["length_scale"] = round(int(branch["length_mm"]) / int(part["length_mm"]), 6)
                fill["girth_scale"] = round(int(branch["girth_mm"]) / (int(part["girth_mm"]) * fill["length_scale"]), 6)
        if replaced:
            mixed["recipe_id"] = f"partqa_{part['part_id']}_{skeleton['skeleton_id']}"
            return base, mixed, replaced
    return None


def review_skeletons(catalog: dict[str, Any], part: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Skeletons whose branches accept ``part``: one per family first, most accepting branches first."""
    scored = []
    for skeleton in catalog["skeletons"]:
        count = sum(1 for b in skeleton["branches"] if part_accepted(part, b))
        if count:
            scored.append((skeleton["family"], -count, skeleton["skeleton_id"], skeleton))
    scored.sort()
    chosen, seen = [], set()
    for family, _, _, skeleton in scored:
        if family not in seen:
            chosen.append(skeleton)
            seen.add(family)
    for _, _, _, skeleton in scored:
        if len(chosen) >= limit:
            break
        if skeleton not in chosen:
            chosen.append(skeleton)
    return chosen[:limit]


def judge(real: dict[str, Any], reference: dict[str, Any]) -> list[str]:
    """Deformation problems of a real part relative to the placeholder it replaces."""
    problems = []
    p99_limit = max(STRAIN_P99_LIMIT, reference["strain_p99"] * 1.25)
    p01_limit = min(STRAIN_P01_LIMIT, reference["strain_p01"] * 0.8)
    flip_limit = max(FLIPPED_LIMIT, reference["flipped"] * 1.5 + 0.002)
    if real["strain_p99"] > p99_limit:
        problems.append(f"stretch p99 {real['strain_p99']:.3f} > {p99_limit:.3f}")
    if real["strain_p01"] < p01_limit:
        problems.append(f"compression p01 {real['strain_p01']:.3f} < {p01_limit:.3f}")
    if real["flipped"] > flip_limit:
        problems.append(f"flipped faces {real['flipped']:.4f} > {flip_limit:.4f}")
    return problems


def qa_pipeline_fingerprint() -> str:
    """Code that produces a QA verdict: assembly and deformation metrics in Blender, and this whole
    module (review planning, reference aggregation, judge, thresholds, pass/fail). Any edit here
    retires existing reviews; that is deliberate, a verdict must never outlive its logic."""
    package = Path(__file__).resolve().parents[1]
    names = ("blender/ops_partqa.py", "blender/ops_assemble.py", "blender/rigkit.py", "blender/frame.py",
             "parts/commands.py")
    payload: dict[str, Any] = {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in names}
    payload["contract"] = "partqa-v1: all clips every 2nd frame + IK stride poses"
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def plan_review(built: dict[str, Any], part: dict[str, Any], limit: int
                ) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]]:
    """(skeleton, placeholder recipe, mixed recipe, replaced part ids) for each reviewed skeleton."""
    index = {s["skeleton_id"]: i for i, s in enumerate(built["skeletons"])}
    plans = []
    for skeleton in review_skeletons(built, part, limit):
        found = _review_recipe(built, skeleton, index[skeleton["skeleton_id"]], part)
        if found is not None:
            plans.append((skeleton, *found))
    return plans


def _recipe_parts(recipe: dict[str, Any]) -> set[str]:
    return {f["part_id"] for f in recipe["fills"]} | {f["connector_part_id"] for f in recipe["fills"] if f["connector_part_id"]}


def review_inputs(built: dict[str, Any], src: Path, part: dict[str, Any], limit: int,
                  plans: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]]) -> dict[str, Any]:
    """Everything a QA verdict depends on. A verdict is only current while all of it is unchanged:
    the part's build, the reviewed skeletons' built clips, every part in the compared recipes, the
    recipes themselves, and the QA/assembly code and thresholds."""
    from ..library.commands import assembly_pipeline_fingerprint, part_build_state

    parts = {p["part_id"]: p for p in built["parts"]}
    used = sorted(set().union(*(_recipe_parts(base) | _recipe_parts(mixed) for _, base, mixed, _ in plans))
                  - {part["part_id"]}) if plans else []
    return {
        "part": part_build_state(part, src),
        "limit": limit,
        "qa_pipeline": qa_pipeline_fingerprint(),
        "assembly_pipeline": assembly_pipeline_fingerprint(),
        "skeletons": {skeleton["skeleton_id"]: skeleton.get("content_fingerprint", "")
                      for skeleton, _, _, _ in plans},
        "recipes": {skeleton["skeleton_id"]: {"placeholder": base["fills"], "mixed": mixed["fills"]}
                    for skeleton, base, mixed, _ in plans},
        "parts": {pid: part_build_state(parts[pid], src) for pid in used},
    }


@part.command("review")
@click.argument("part_id")
@click.option("--skeletons", "limit", type=int, default=3, show_default=True)
@click.option("--size", type=int, default=320, show_default=True)
def part_review(part_id: str, limit: int, size: int) -> None:
    """Deform PART_ID through the built clips of skeletons that accept it; write QA + review sheet."""
    from ..library.commands import _built_catalog, _gait

    built, src = _built_catalog()
    part = next((p for p in built["parts"] if p["part_id"] == part_id), None)
    if part is None:
        raise click.ClickException(f"unknown part {part_id}")
    if not part.get("asset", {}).get("fbx"):
        raise click.ClickException(f"{part_id} is not built; run `critter library build`")
    parts = {p["part_id"]: p for p in built["parts"]}
    plans = plan_review(built, part, limit)
    if not plans:
        raise click.ClickException(f"no built skeleton accepts {part_id}")
    out = paths().work / "review" / "parts" / part_id
    jobs, meta = [], []
    for skeleton, base, mixed, replaced in plans:
        common = {"skeleton": skeleton, "library_dir": str(src),
                  "gait_profile": _gait(built, skeleton["locomotion_hint"]), "frame_step": 2, "stride": True}
        shots = [{"clip": clip, "at": at, "view": "three_quarter",
                  "path": str(out / f"{skeleton['skeleton_id']}_{i}_{clip}.png")}
                 for i, (clip, at) in enumerate(REVIEW_CLIPS)]
        exports = {} if meta else {"out_fbx": str(out / f"{skeleton['skeleton_id']}_mixed.fbx"),
                                   "out_glb": str(out / f"{skeleton['skeleton_id']}_mixed.glb")}
        jobs.append({"op": "partqa", "args": {**common, "recipe": mixed, "measure": [part_id], "shots": shots,
                                              "size": size, "parts": {p: parts[p] for p in _recipe_parts(mixed)},
                                              **exports}})
        jobs.append({"op": "partqa", "args": {**common, "recipe": base, "measure": sorted(set(replaced)),
                                              "parts": {p: parts[p] for p in _recipe_parts(base)}}})
        meta.append((skeleton, replaced, shots))
    click.echo(f"deforming {part_id} on {len(meta)} skeleton(s) ...")
    try:
        results = run_op("batch", {"jobs": jobs})["results"]
    except BlenderError as e:
        raise click.ClickException(str(e))
    report: dict[str, Any] = {"part_id": part_id, "inputs": review_inputs(built, src, part, limit, plans),
                              "skeletons": []}
    pngs, labels = [], []
    passed = True
    for (skeleton, replaced, shots), real_res, ref_res in zip(meta, results[0::2], results[1::2]):
        real = real_res["result"]["parts"][part_id]
        refs = [ref_res["result"]["parts"][r] for r in sorted(set(replaced))]
        reference = {"strain_p99": max(r["strain_p99"] for r in refs), "strain_p01": min(r["strain_p01"] for r in refs),
                     "flipped": max(r["flipped"] for r in refs)}
        problems = judge(real, reference)
        passed &= not problems
        report["skeletons"].append({"skeleton_id": skeleton["skeleton_id"], "branches": len(replaced),
                                    "real": real, "reference": reference, "problems": problems})
        click.echo(f"  {skeleton['skeleton_id']:48s} p01 {real['strain_p01']:.3f} (ref {reference['strain_p01']:.3f})  "
                   f"p99 {real['strain_p99']:.3f} (ref {reference['strain_p99']:.3f})  "
                   f"flipped {real['flipped']:.4f} (ref {reference['flipped']:.4f})  {'ok' if not problems else '; '.join(problems)}")
        pngs.extend(real_res["result"].get("pngs", []))
        labels.extend(f"{skeleton['skeleton_id'][:28]} {s['clip']}@{s['at']}" for s in shots)
    report["passed"] = passed
    report["exports"] = [results[0]["result"].get(k) for k in ("fbx", "glb") if results[0]["result"].get(k)]
    _write_json(_qa_path(part_id), report)
    _sheet(pngs, labels, out / "sheet.png", cols=len(REVIEW_CLIPS), size=size)
    click.echo(f"{'PASS' if passed else 'FAIL'}: {_qa_path(part_id)}; sheet {out / 'sheet.png'} (never commit)")
    for path in report["exports"]:
        click.echo(f"mixed creature with every clip: {path}")
    if not passed:
        raise SystemExit(1)


def stale_review_inputs(recorded: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    """Names of the QA inputs that changed since the review (all of them if none were recorded)."""
    if not isinstance(recorded, dict):
        return sorted(current)
    return sorted(key for key in current if recorded.get(key) != current[key])


def _set_status(part_id: str, status: str) -> Path:
    path = paths().data / "parts" / f"{part_id}.part.json"
    if not path.is_file():
        raise click.ClickException(f"no source record {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not doc.get("real"):
        raise click.ClickException(f"{part_id} is not a real part")
    doc["status"] = status
    _write_json(path, doc)
    return path


@part.command("approve")
@click.argument("part_id")
def part_approve(part_id: str) -> None:
    """Mark PART_ID approved (generatable) after a passing `critter part review` of the current build."""
    from ..library.commands import _built_catalog

    built, src = _built_catalog()
    part = next((p for p in built["parts"] if p["part_id"] == part_id), None)
    qa = _read_qa(part_id)
    if part is None or qa is None:
        raise click.ClickException(f"run `critter part review {part_id}` first")
    recorded = qa.get("inputs") if isinstance(qa.get("inputs"), dict) else None
    limit = int(recorded.get("limit", 3)) if recorded else 3
    stale = stale_review_inputs(recorded, review_inputs(built, src, part, limit, plan_review(built, part, limit)))
    if stale:
        raise click.ClickException(f"{part_id}: the review is stale ({', '.join(stale)} changed); "
                                   f"run `critter part review {part_id}` again")
    if not qa.get("passed"):
        raise click.ClickException(f"{part_id}: QA failed; see {_qa_path(part_id)}")
    path = _set_status(part_id, "approved")
    click.echo(f"approved {part_id} ({path}). Approved parts join generation: rerun `critter recipe golden`, "
               "copy the goldens into the Unity package and rebuild the library.")


@part.command("reject")
@click.argument("part_id")
def part_reject(part_id: str) -> None:
    """Mark PART_ID rejected (never generated)."""
    path = _set_status(part_id, "rejected")
    click.echo(f"rejected {part_id} ({path})")


__all__ = ["part", "part_record", "default_length", "judge", "review_skeletons", "plan_review",
           "review_inputs", "stale_review_inputs", "qa_pipeline_fingerprint", "TEMPLATE_DEFAULTS"]
