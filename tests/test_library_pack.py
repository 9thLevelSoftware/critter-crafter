"""`critter library pack --slim` keeps what the Unity importer reads and nothing else."""

from __future__ import annotations

from critter_crafter.library.commands import pack_files


def _library(root):
    for name in ("catalog.json", "manifest.json", ".build-state.json",
                 "skeletons/s/s.fbx", "skeletons/s/s.glb", "skeletons/s/s.blend", "skeletons/s/assembled.glb",
                 "skeletons/s/motion.json", "parts/p/p.fbx", "parts/p/p.glb", "parts/p/p_albedo.png",
                 "connectors/c/c.fbx", "connectors/c/c.glb"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return root


def test_slim_keeps_the_catalog_models_textures_and_motion_files_only(tmp_path):
    src = _library(tmp_path)
    kept = sorted(f.relative_to(src).as_posix() for f in pack_files(src, slim=True))
    assert kept == ["catalog.json", "connectors/c/c.fbx", "parts/p/p.fbx", "parts/p/p_albedo.png",
                    "skeletons/s/motion.json", "skeletons/s/s.fbx"]


def test_the_full_pack_keeps_everything(tmp_path):
    src = _library(tmp_path)
    assert len(pack_files(src, slim=False)) == 13
