"""Anatomy traits: the behaviours the pipeline used to key on ``family``.

A builder writes ``anatomy.traits`` when it makes a skeleton. Consumers (locomotion block, clip planner, QA,
validator, Blender bake) read the traits instead of comparing family names, so a new body plan only has to
declare what it is. Records made before traits existed fall back to the family table below.
"""

from __future__ import annotations

from typing import Any

# Families whose bodies are held up by four or more legs and so must always keep two feet planted.
_MULTI_LEG = ("quadruped", "hexapod", "crawler", "radial")
_POLE_IK = ("biped", "quadruped", "hexapod", "dragger")


def family_traits(family: str) -> dict[str, Any]:
    """Traits of a curated family (the values the old ``family ==`` checks encoded)."""
    return {
        "min_support": 2 if family in _MULTI_LEG else 1,
        "body_on_ground": family == "dragger",
        "ring": family == "radial",
        "pole_ik": family in _POLE_IK,
        "compact_clip": family in ("crawler", "radial"),
    }


def traits_of(skeleton: dict[str, Any]) -> dict[str, Any]:
    """The skeleton's declared traits, or the family defaults for records that predate them."""
    declared = (skeleton.get("anatomy") or {}).get("traits")
    base = family_traits(skeleton.get("family", ""))
    if declared:
        base.update(declared)
    return base
