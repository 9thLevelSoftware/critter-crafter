"""Compiler-generated connectors: 800-tri cap, one per requesting branch, split fingerprint."""

from pathlib import Path

from critter_crafter.config import paths
from critter_crafter.library import commands as library_commands
from critter_crafter.library.catalog import CONNECTOR_MAX_TRIANGLES, compile_catalog, load_sources, reference_part_id


def _catalog():
    return compile_catalog(load_sources(paths().data))


def test_reference_connectors_are_capped_at_800_triangles():
    catalog = _catalog()
    connectors = [part for part in catalog["parts"] if part["category"] == "connector"]
    assert connectors
    assert CONNECTOR_MAX_TRIANGLES == 800
    assert all(part["max_triangles"] == 800 for part in connectors)
    assert all(part["inventory_kind"] == "reference" for part in connectors)
    assert not any("gunk_collar" in part["part_id"] for part in catalog["parts"])


def test_one_reference_connector_per_requesting_branch():
    catalog = _catalog()
    for skeleton in catalog["skeletons"]:
        for branch in skeleton["branches"]:
            part_id = reference_part_id(skeleton["skeleton_id"], branch["branch_id"], connector=True)
            found = [part for part in catalog["parts"] if part["part_id"] == part_id]
            if branch["connector_size_class"]:
                assert len(found) == 1
                assert found[0]["category"] == "connector"
                assert found[0]["template"] == "connector2"
            else:
                assert found == []


def test_build_jobs_route_connectors_to_the_connector_op(tmp_path):
    catalog = _catalog()
    connector = next(part for part in catalog["parts"] if part["category"] == "connector")
    body = next(part for part in catalog["parts"]
                if part["category"] != "connector" and not part.get("real")
                and part.get("source") in ("placeholder", "reference"))
    jobs = {job["args"]["part"]["part_id"]: job
            for job in library_commands.build_jobs(catalog, tmp_path, {connector["part_id"], body["part_id"]})}
    assert jobs[connector["part_id"]]["op"] == "connector"
    assert jobs[body["part_id"]]["op"] == "placeholder"


def test_connector_pipeline_is_fingerprinted_separately_from_placeholders_and_real_parts():
    catalog = _catalog()
    real = next(part for part in catalog["parts"] if part.get("real"))
    body = next(part for part in catalog["parts"] if not part.get("real") and part["category"] != "connector")
    connector = next(part for part in catalog["parts"] if part["category"] == "connector")
    assert library_commands.part_pipeline_for(real) == library_commands.realpart_pipeline_fingerprint()
    assert library_commands.part_pipeline_for(body) == library_commands.part_pipeline_fingerprint()
    assert library_commands.part_pipeline_for(connector) == library_commands.connector_pipeline_fingerprint()
    assert library_commands.connector_pipeline_fingerprint() != library_commands.part_pipeline_fingerprint()
    assert library_commands.connector_pipeline_fingerprint() != library_commands.realpart_pipeline_fingerprint()


def test_realpart_pipeline_fingerprint_does_not_hash_ops_connector():
    real_before = library_commands.realpart_pipeline_fingerprint()
    part_before = library_commands.part_pipeline_fingerprint()
    conn_before = library_commands.connector_pipeline_fingerprint()
    path = Path(library_commands.__file__).resolve().parents[1] / "blender" / "ops_connector.py"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n# fingerprint-probe\n")
        library_commands.realpart_pipeline_fingerprint.cache_clear()
        library_commands.part_pipeline_fingerprint.cache_clear()
        library_commands.connector_pipeline_fingerprint.cache_clear()
        assert library_commands.realpart_pipeline_fingerprint() == real_before
        assert library_commands.part_pipeline_fingerprint() == part_before
        assert library_commands.connector_pipeline_fingerprint() != conn_before
    finally:
        path.write_bytes(original)
        library_commands.realpart_pipeline_fingerprint.cache_clear()
        library_commands.part_pipeline_fingerprint.cache_clear()
        library_commands.connector_pipeline_fingerprint.cache_clear()
