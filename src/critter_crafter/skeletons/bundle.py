"""Portable, local-only browser review bundles for built skeleton assets."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class BundleError(ValueError):
    """A requested review bundle cannot truthfully represent built artifacts."""


_VIEWER_DIR = Path(__file__).with_name("viewer")


def _load_catalog(catalog: Mapping[str, Any] | Path) -> dict[str, Any]:
    if isinstance(catalog, Path):
        try:
            return json.loads(catalog.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BundleError(f"CC_REVIEW_CATALOG: {catalog}: {error}") from error
    return dict(catalog)


def _artifact(library_dir: Path, relative: object, skeleton_id: str, name: str,
              *, required: bool) -> Path | None:
    if not isinstance(relative, str) or not relative:
        if required:
            raise BundleError(f"CC_REVIEW_ASSET_MISSING: {skeleton_id} has no {name} asset")
        return None
    path = (library_dir / relative).resolve()
    if not path.is_relative_to(library_dir.resolve()) or not path.is_file():
        if required:
            raise BundleError(f"CC_REVIEW_ASSET_MISSING: {skeleton_id}: {path}")
        return None
    return path


def _copy_viewer(out_dir: Path) -> None:
    target = out_dir / "viewer"
    shutil.copytree(_VIEWER_DIR, target, dirs_exist_ok=True)


def write_bundle(catalog: Mapping[str, Any] | Path, library_dir: Path, out_dir: Path,
                 skeleton_ids: Sequence[str] | None = None) -> list[Path]:
    """Write a self-contained review site from actual built GLBs and motion samples.

    ``catalog`` may be the built catalog document or its ``catalog.json`` path.  Every
    selected skeleton must have its built GLB and ``motion.json``; this routine never
    synthesizes an animation or substitutes a placeholder.  The returned paths are the
    per-skeleton entry pages, suitable for recording in review receipts.
    """
    document = _load_catalog(catalog)
    library_dir, out_dir = Path(library_dir), Path(out_dir)
    selected = set(skeleton_ids) if skeleton_ids is not None else None
    skeletons = [s for s in document.get("skeletons", [])
                 if selected is None or s.get("skeleton_id") in selected]
    found = {s.get("skeleton_id") for s in skeletons}
    if selected is not None and found != selected:
        missing = ", ".join(sorted(selected - found))
        raise BundleError(f"CC_REVIEW_SKELETON_UNKNOWN: {missing}")
    if not skeletons:
        raise BundleError("CC_REVIEW_SKELETON_EMPTY: no skeletons selected")

    out_dir.mkdir(parents=True, exist_ok=True)
    _copy_viewer(out_dir)
    assets_dir = out_dir / "assets"
    review_skeletons: list[dict[str, Any]] = []
    pages: list[Path] = []
    for skeleton in sorted(skeletons, key=lambda item: str(item["skeleton_id"])):
        skeleton_id = str(skeleton["skeleton_id"])
        asset = skeleton.get("asset") or {}
        if not isinstance(asset, Mapping):
            raise BundleError(f"CC_REVIEW_ASSET_MISSING: {skeleton_id} has invalid asset metadata")
        glb = _artifact(library_dir, asset.get("glb"), skeleton_id, "GLB", required=True)
        motion = _artifact(library_dir, asset.get("motion"), skeleton_id, "motion.json", required=True)
        assembled = _artifact(library_dir, asset.get("assembled_glb"), skeleton_id,
                              "assembled GLB", required=False)
        destination = assets_dir / skeleton_id
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(glb, destination / "skeleton.glb")
        shutil.copy2(motion, destination / "motion.json")
        if assembled:
            shutil.copy2(assembled, destination / "assembled.glb")
        review_skeletons.append({
            "skeleton_id": skeleton_id,
            "family": skeleton.get("family", "unknown"),
            "status": skeleton.get("status", "draft"),
            "source_fingerprint": skeleton.get("source_fingerprint", "unavailable"),
            "content_fingerprint": skeleton.get("content_fingerprint", "unavailable"),
            "provenance": skeleton.get("provenance", {}),
            "neutral_pose": skeleton.get("neutral_pose", {}),
            "bones": skeleton.get("bones", []),
            "branches": skeleton.get("branches", []),
            "asset": {
                "glb": f"assets/{skeleton_id}/skeleton.glb",
                "motion": f"assets/{skeleton_id}/motion.json",
                "assembled_glb": f"assets/{skeleton_id}/assembled.glb" if assembled else None,
                "clips": asset.get("clips", []),
            },
        })
        page = out_dir / "skeletons" / skeleton_id / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            "<!doctype html><meta charset=\"utf-8\"><title>Critter review</title>"
            f"<script>location.replace('../../index.html?skeleton={skeleton_id}')</script>"
            f"<a href=\"../../index.html?skeleton={skeleton_id}\">Open review</a>\n",
            encoding="utf-8", newline="\n",
        )
        pages.append(page)

    manifest = {
        "schema_version": "review-bundle-1",
        "library_id": document.get("library_id"),
        "library_version": document.get("version"),
        "frame": document.get("frame", "gltf_rh_yup_zfwd_m"),
        "skeletons": review_skeletons,
    }
    (out_dir / "review-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    index = out_dir / "index.html"
    page_html = (_VIEWER_DIR / "index.html").read_text(encoding="utf-8")
    page_html = page_html.replace('href="viewer.css"', 'href="viewer/viewer.css"')
    page_html = page_html.replace('src="viewer.js"', 'src="viewer/viewer.js"')
    index.write_text(page_html,
                     encoding="utf-8", newline="\n")
    return pages
