import os
import subprocess

import pytest

from critter_crafter.library.bake import SCHEMA, compact, confined, digest, index_input_paths


def test_linked_dependency_is_rejected_even_when_target_stays_inside_library(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "texture.png").write_bytes(b"source texture")
    link = tmp_path / "linked"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit creating directory symlinks")
    with pytest.raises(ValueError, match="CC_BAKE_PATH"):
        confined(tmp_path, "linked/texture.png")


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions require NTFS")
@pytest.mark.parametrize("outside", [False, True])
def test_index_rejects_unprivileged_directory_junction_dependencies(tmp_path, outside):
    root = tmp_path / "library"
    root.mkdir()
    target = tmp_path / "outside" if outside else root / "real"
    target.mkdir()
    (target / "texture.png").write_bytes(b"original source texture")
    link = root / "linked"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True, text=True)
    try:
        assert link.is_junction()
        catalog = compact({"library_id": "fixture", "version": "0.3.0"})
        (root / "catalog.json").write_bytes(catalog)
        key = digest(b"fixture\n0.3.0\nskeleton:composition")
        source_bytes = (target / "texture.png").read_bytes()
        (root / "source.png").write_bytes(source_bytes)
        folder = root / "baked_creatures" / key
        folder.mkdir(parents=True)
        material = f"baked_creatures/{key}/material.json"
        material_bytes = compact({"opaque": True, "atlas_size": 4096})
        (root / material).write_bytes(material_bytes)
        (folder / f"{key}.fbx").write_bytes(b"indexed body mesh")
        (folder / f"{key}_albedo.png").write_bytes(b"indexed opaque atlas")
        rows = [{"path": material, "sha256": digest(material_bytes)},
                {"path": "catalog.json", "sha256": digest(catalog)},
                {"path": "source.png", "sha256": digest(source_bytes)}]
        entry = {
            "key": key, "skeleton_id": "skeleton", "composition": "skeleton:composition",
            "source_sha256": digest(compact(rows)), "producer_sha256": "a" * 64,
            "material_sha256": digest(material_bytes),
            "asset_sha256": digest((folder / f"{key}.fbx").read_bytes()),
            "texture_sha256": digest((folder / f"{key}_albedo.png").read_bytes()),
            "fbx": f"baked_creatures/{key}/{key}.fbx",
            "albedo_png": f"baked_creatures/{key}/{key}_albedo.png", "inputs": rows,
        }
        index = {"schema_version": SCHEMA, "library_id": "fixture", "library_version": "0.3.0",
                 "source_catalog_sha256": digest(catalog), "entries": [entry]}
        (root / "baked_creatures/index.json").write_bytes(compact(index))
        assert set(index_input_paths(root)) == {
            "baked_creatures/index.json", material, "catalog.json", "source.png",
            entry["fbx"], entry["albedo_png"],
        }
        rows[-1]["path"] = "linked/texture.png"
        entry["source_sha256"] = digest(compact(rows))
        (root / "baked_creatures/index.json").write_bytes(compact(index))
        with pytest.raises(ValueError, match="CC_BAKE_PATH"):
            index_input_paths(root)
    finally:
        if link.is_junction():
            os.rmdir(link)
    assert (target / "texture.png").read_bytes() == b"original source texture"
