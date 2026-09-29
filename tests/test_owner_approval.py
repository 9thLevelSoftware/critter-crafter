"""The owner's own approval path: no review receipt, recorded as the owner's decision.

These tests work on temporary copies. They never touch the real skeleton or part data."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import jsonschema
from click.testing import CliRunner

from critter_crafter.library import commands as library_commands
from critter_crafter.parts import commands as part_commands
from critter_crafter.skeletons import commands as skeleton_commands
from critter_crafter.skeletons.review import source_fingerprint

ROOT = Path(__file__).resolve().parents[1]
SKELETONS = sorted((ROOT / "data" / "skeletons").glob("*_v3.skeleton.json"))


def _skeleton_data(tmp_path: Path, monkeypatch, count: int = 2) -> list[dict]:
    data = tmp_path / "data"
    (data / "skeletons").mkdir(parents=True)
    for src in SKELETONS[:count]:
        shutil.copy(src, data / "skeletons" / src.name)
    monkeypatch.setattr(skeleton_commands, "paths", lambda: SimpleNamespace(data=data, work=tmp_path / "work"))
    return [json.loads((data / "skeletons" / s.name).read_text(encoding="utf-8")) for s in SKELETONS[:count]]


def _read(tmp_path: Path, skeleton_id: str) -> dict:
    return json.loads((tmp_path / "data" / "skeletons" / f"{skeleton_id}.skeleton.json").read_text(encoding="utf-8"))


def test_owner_approval_records_the_decision_and_keeps_the_source_valid(tmp_path, monkeypatch):
    first, second = _skeleton_data(tmp_path, monkeypatch)
    schema = json.loads((ROOT / "schemas" / "skeleton.v3.schema.json").read_text(encoding="utf-8"))
    before = source_fingerprint(first, [], {})

    result = CliRunner().invoke(skeleton_commands.skeleton, ["approve", "--owner", first["skeleton_id"]])

    assert result.exit_code == 0, result.output
    approved = _read(tmp_path, first["skeleton_id"])
    assert approved["status"] == "approved"
    assert approved["review"]["by"] == "owner" and approved["review"]["method"] == "owner"
    jsonschema.validate(approved, schema)
    # A decision is not a change to the creature, so built assets and receipts stay valid.
    assert source_fingerprint(approved, [], {}) == before
    # Only the named skeleton changed.
    assert _read(tmp_path, second["skeleton_id"])["status"] == second["status"]


def test_owner_reject_and_family_selection(tmp_path, monkeypatch):
    first, _ = _skeleton_data(tmp_path, monkeypatch)
    result = CliRunner().invoke(skeleton_commands.skeleton, ["reject", "--owner", "--family", first["family"]])
    assert result.exit_code == 0, result.output
    assert _read(tmp_path, first["skeleton_id"])["status"] == "rejected"


def test_owner_approval_rejects_unknown_ids_without_writing(tmp_path, monkeypatch):
    first, _ = _skeleton_data(tmp_path, monkeypatch)
    result = CliRunner().invoke(skeleton_commands.skeleton, ["approve", "--owner", first["skeleton_id"], "no_such_v3"])
    assert result.exit_code != 0 and "unknown skeletons" in result.output
    assert _read(tmp_path, first["skeleton_id"])["status"] == first["status"]


def test_approval_without_owner_still_needs_a_review_receipt(tmp_path, monkeypatch):
    first, _ = _skeleton_data(tmp_path, monkeypatch)
    catalog = {"skeletons": [{"skeleton_id": first["skeleton_id"], "family": first["family"]}]}
    monkeypatch.setattr(library_commands, "_built_catalog", lambda *a, **k: (catalog, tmp_path))

    result = CliRunner().invoke(skeleton_commands.skeleton, ["approve", first["skeleton_id"]])

    assert result.exit_code != 0 and "CC_REVIEW_MISSING" in result.output
    assert _read(tmp_path, first["skeleton_id"])["status"] == first["status"]


def _part_setup(tmp_path: Path, monkeypatch) -> Path:
    data = tmp_path / "data"
    (data / "parts").mkdir(parents=True)
    record = data / "parts" / "demo_part_v1.part.json"
    record.write_text(json.dumps({"part_id": "demo_part_v1", "status": "draft", "real": {"fit": {}}}), encoding="utf-8")
    monkeypatch.setattr(part_commands, "paths", lambda: SimpleNamespace(data=data))
    monkeypatch.setattr(library_commands, "_built_catalog",
                        lambda *a, **k: ({"parts": [{"part_id": "demo_part_v1"}]}, tmp_path))
    monkeypatch.setattr(library_commands, "part_build_state", lambda part, out, **_: {"pipeline": "f" * 64})
    return record


def test_owner_part_approval_needs_no_qa_report_but_pins_the_pipeline(tmp_path, monkeypatch):
    record = _part_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(part_commands, "_read_qa", lambda pid: None)

    result = CliRunner().invoke(part_commands.part, ["approve", "--owner", "demo_part_v1"])

    assert result.exit_code == 0, result.output
    doc = json.loads(record.read_text(encoding="utf-8"))
    assert doc["status"] == "approved" and doc["real"]["approved_pipeline"] == "f" * 64


def test_part_approval_without_owner_still_needs_a_review(tmp_path, monkeypatch):
    record = _part_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(part_commands, "_read_qa", lambda pid: None)

    result = CliRunner().invoke(part_commands.part, ["approve", "demo_part_v1"])

    assert result.exit_code != 0 and "critter part review" in result.output
    assert json.loads(record.read_text(encoding="utf-8"))["status"] == "draft"
