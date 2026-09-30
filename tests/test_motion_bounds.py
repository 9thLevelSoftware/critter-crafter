"""The offline motion box that stands in for baking every part through every clip at spawn time."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from critter_crafter.library.commands import add_motion_bounds, motion_bounds

ROOT = Path(__file__).resolve().parents[1]


def _skeleton():
    return {"skeleton_id": "s", "bones": [
        {"name": "root", "head_m": [0, 0, 0], "tail_m": [0, .1, 0]},
        {"name": "a", "head_m": [.25, 1.0, -.1], "tail_m": [.3, .5, .2]}]}


def _motion():
    return {"clips": [{"name": "idle", "samples": [
        {"bones": [{"name": "a", "head_m": [.2501, 1.0004, -.1], "tail_m": [-.4, .55, .3]}]},
        {"bones": [{"name": "a", "head_m": [.25, 1.0, -.1], "tail_m": [.3, .2, .2001]}]}]}]}


def test_motion_bounds_cover_every_bone_end_in_every_sample_and_the_bind_pose_rounded_outward():
    box = motion_bounds(_skeleton(), _motion())
    # y: the 1.0004 head rounds up to 1.001; x min: -.4 from a tail; z: .3 is the largest tail z, .2001 is smaller
    assert box == {"min": [-.4, 0.0, -.1], "max": [.3, 1.001, .3]}


def test_a_clip_that_reaches_further_grows_the_box():
    motion = _motion()
    motion["clips"][0]["samples"][0]["bones"][0]["tail_m"] = [-.9, .55, .3]
    assert motion_bounds(_skeleton(), motion)["min"][0] == -.9


def test_add_motion_bounds_records_it_on_the_asset_and_the_schema_accepts_it(tmp_path):
    (tmp_path / "motion.json").write_text(json.dumps(_motion()), encoding="utf-8")
    catalog = {"skeletons": [{**_skeleton(), "asset": {"fbx": "x.fbx", "glb": "x.glb", "motion": "motion.json"}},
                             {**_skeleton(), "skeleton_id": "t", "asset": {"fbx": "y.fbx", "glb": "y.glb"}}]}
    add_motion_bounds(catalog, tmp_path)
    assert catalog["skeletons"][0]["asset"]["motion_bounds_m"] == motion_bounds(_skeleton(), _motion())
    assert "motion_bounds_m" not in catalog["skeletons"][1]["asset"]          # no motion file, no box
    schema = json.loads((ROOT / "schemas" / "library.v3.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(catalog["skeletons"][0]["asset"], {"$ref": "#/$defs/asset", "$defs": schema["$defs"]})
