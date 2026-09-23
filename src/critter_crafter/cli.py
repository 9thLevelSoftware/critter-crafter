"""`critter` command line entry point."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click

from . import __version__
from .config import find_blender, find_unity, paths
from .library.catalog import compile_catalog, dumps, load_sources
from .recipes.generator import GenerationError, canonical, generate
from .recipes.validate import validate_recipe
from .schema.validate import validate_sources


def _compiled_catalog() -> dict:
    diags, catalog = validate_sources(paths().data, paths().schemas)
    errors = [d for d in diags if not d.startswith("CC_NO_CANDIDATE_OPTIONAL")]
    if errors or catalog is None:
        for d in diags:
            click.echo(d, err=True)
        raise click.ClickException(f"{len(errors)} validation error(s)")
    return catalog


def _parse_range(text: str) -> range:
    if ".." in text:
        a, b = text.split("..", 1)
        return range(int(a), int(b) + 1)
    return range(int(text), int(text) + 1)


@click.group()
@click.version_option(__version__)
def main() -> None:
    """critter-crafter: skeleton-first procedural monster asset pipeline."""


@main.command()
def doctor() -> None:
    """Check external tools (Blender 5.x, Unity, Git LFS)."""
    ok = True
    blender = find_blender()
    click.echo(f"blender : {blender or 'NOT FOUND (set CRITTER_BLENDER)'}")
    if blender:
        out = subprocess.run([blender, "--version"], capture_output=True, text=True, timeout=120).stdout
        first = out.splitlines()[0] if out else "?"
        click.echo(f"          {first}")
        ok &= first.startswith("Blender 5.")
    else:
        ok = False
    unity = find_unity()
    click.echo(f"unity   : {unity or 'not found (optional; set CRITTER_UNITY)'}")
    lfs = subprocess.run(["git", "lfs", "version"], capture_output=True, text=True)
    click.echo(f"git-lfs : {lfs.stdout.strip() or 'NOT FOUND'}")
    if not ok:
        raise click.ClickException("doctor found problems")


@main.group()
def schema() -> None:
    """Schema validation."""


@schema.command("validate")
def schema_validate() -> None:
    """Validate data/ against schemas/ plus cross-record rules."""
    diags, _ = validate_sources(paths().data, paths().schemas)
    for d in diags:
        click.echo(d)
    errors = [d for d in diags if not d.startswith("CC_NO_CANDIDATE_OPTIONAL")]
    click.echo(f"{len(diags)} diagnostic(s), {len(errors)} error(s)")
    if errors:
        sys.exit(1)


@main.group()
def catalog() -> None:
    """Compiled catalog (library.v2)."""


@catalog.command("compile")
@click.option("--out", type=click.Path(path_type=Path), default=None, help="Output file (default: stdout)")
def catalog_compile(out: Path | None) -> None:
    """Compile data/ into a mesh-less library.v2 catalog."""
    text = dumps(_compiled_catalog())
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        click.echo(f"wrote {out}")
    else:
        click.echo(text, nl=False)


@main.group()
def recipe() -> None:
    """Recipe generation."""


@recipe.command("generate")
@click.option("--pool", "pool_id", required=True)
@click.option("--seed", type=int, required=True)
def recipe_generate(pool_id: str, seed: int) -> None:
    """Generate one recipe and print it as JSON."""
    cat = _compiled_catalog()
    r = generate(cat, pool_id, seed)
    diags = validate_recipe(cat, r)
    click.echo(json.dumps(r, indent=2))
    if diags:
        raise click.ClickException("; ".join(diags))


@recipe.command("sweep")
@click.option("--pool", "pool_ids", multiple=True, help="Pool(s); default: all pools")
@click.option("--seeds", default="1..100", show_default=True)
def recipe_sweep(pool_ids: tuple[str, ...], seeds: str) -> None:
    """Generate across a seed range; report validity and distinct creature count."""
    cat = _compiled_catalog()
    pools = pool_ids or tuple(p["pool_id"] for p in cat["pools"])
    bad = 0
    for pid in pools:
        forms: set[str] = set()
        errors = 0
        n = 0
        for s in _parse_range(seeds):
            n += 1
            try:
                r = generate(cat, pid, s)
            except GenerationError as e:
                errors += 1
                click.echo(f"  {pid} seed {s}: {e}", err=True)
                continue
            if validate_recipe(cat, r):
                errors += 1
            forms.add(canonical(r))
        bad += errors
        click.echo(f"{pid:16s} seeds={n:4d} distinct={len(forms):4d} invalid={errors}")
    if bad:
        sys.exit(1)


@recipe.command("golden")
@click.option("--seeds", default="1..100", show_default=True)
def recipe_golden(seeds: str) -> None:
    """Rewrite tests/golden (catalog + canonical recipes) consumed by Python and C# tests."""
    cat = _compiled_catalog()
    gdir = paths().root / "tests" / "golden"
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "catalog.json").write_text(dumps(cat), encoding="utf-8", newline="\n")
    rows = []
    for p in cat["pools"]:
        for s in _parse_range(seeds):
            rows.append({"pool_id": p["pool_id"], "seed": s, "canonical": canonical(generate(cat, p["pool_id"], s))})
    doc = {"generator": "cc-gen-2", "rows": rows}
    (gdir / "recipes.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
    click.echo(f"wrote {len(rows)} golden rows to {gdir}")


from .library import commands as _library_commands  # noqa: E402

main.add_command(_library_commands.library)
main.add_command(_library_commands.blender)
main.add_command(_library_commands.assemble)


if __name__ == "__main__":
    main()
