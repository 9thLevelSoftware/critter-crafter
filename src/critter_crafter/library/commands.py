"""`critter library ...` and `critter blender ...` commands (Blender-backed)."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import click

from ..blender.runner import BlenderError, run_op, snippet
from ..config import paths
from ..schema.validate import validate_sources
from .catalog import dumps


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


def build_jobs(catalog: dict[str, Any], out: Path, only: set[str] | None = None) -> list[dict[str, Any]]:
    templates = {t["template_id"]: t for t in catalog["branch_templates"]}
    masters = paths().work / "masters"
    jobs = []
    for s in catalog["skeletons"]:
        sid = s["skeleton_id"]
        if only and sid not in only:
            continue
        jobs.append({"op": "skeleton", "args": {
            "skeleton": s, "gait_profile": _gait(catalog, s["locomotion_hint"]),
            "out_fbx": str(out / "skeletons" / sid / f"{sid}.fbx"),
            "out_glb": str(out / "skeletons" / sid / f"{sid}.glb"),
            "out_blend": str(masters / "skeletons" / sid / "master.blend"),
        }})
    for p in catalog["parts"]:
        pid = p["part_id"]
        if only and pid not in only:
            continue
        if p["source"] != "placeholder":
            continue  # real parts come from the Meshy/clean pipeline (M2+)
        kind = "connectors" if p["category"] == "connector" else "parts"
        jobs.append({"op": "placeholder", "args": {
            "part": p, "template": templates[p["template"]],
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
        res = r["result"]
        if r["op"] == "skeleton":
            s = skel[res["skeleton_id"]]
            sid = s["skeleton_id"]
            s["asset"] = {"fbx": f"skeletons/{sid}/{sid}.fbx", "glb": f"skeletons/{sid}/{sid}.glb", "clips": res["clips"]}
            if res["bones"] != len(s["bones"]):
                problems.append(f"CC_BONE_COUNT: {sid}: blender {res['bones']} != catalog {len(s['bones'])}")
        else:
            p = parts[res["part_id"]]
            pid = p["part_id"]
            kind = "connectors" if p["category"] == "connector" else "parts"
            p["asset"] = {"fbx": f"{kind}/{pid}/{pid}.fbx", "glb": f"{kind}/{pid}/{pid}.glb", "triangles": res["triangles"]}
            if res["triangles"] > p["max_triangles"]:
                problems.append(f"CC_BUDGET_TRIS: {pid}: {res['triangles']} > {p['max_triangles']}")
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
    manifest = {"schema_version": "2.0.0", "files": files}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


@click.group()
def library() -> None:
    """Build and package asset libraries."""


@library.command("build")
@click.option("--out", "out_root", type=click.Path(path_type=Path), default=None, help="Output root (default: library/)")
@click.option("--clean/--no-clean", default=True, show_default=True)
def library_build(out_root: Path | None, clean: bool) -> None:
    """Compile the catalog and build every skeleton + placeholder asset in one headless Blender run."""
    catalog = _catalog()
    out = library_dir(catalog, out_root)
    if clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = build_jobs(catalog, out)
    click.echo(f"building {len(jobs)} assets with Blender -> {out}")
    try:
        result = run_op("batch", {"jobs": jobs})
    except BlenderError as e:
        raise click.ClickException(str(e))
    problems = apply_results(catalog, out, result["results"])
    (out / "catalog.json").write_text(dumps(catalog), encoding="utf-8", newline="\n")
    manifest = write_manifest(out)
    for p in problems:
        click.echo(p, err=True)
    click.echo(f"catalog.json + {len(manifest['files'])} files written")
    if problems:
        raise click.ClickException(f"{len(problems)} problem(s)")


@library.command("pack")
@click.option("--out", "out_root", type=click.Path(path_type=Path), default=None)
def library_pack(out_root: Path | None) -> None:
    """Zip a built library as critter-library-<id>-v<ver>.zip (release asset)."""
    catalog = _catalog()
    src = library_dir(catalog, out_root)
    if not (src / "catalog.json").exists():
        raise click.ClickException(f"{src} not built; run `critter library build`")
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


def _built_catalog() -> tuple[dict[str, Any], Path]:
    src = library_dir(_catalog())
    path = src / "catalog.json"
    if not path.exists():
        raise click.ClickException("library not built; run `critter library build` first")
    return json.loads(path.read_text(encoding="utf-8")), src


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
