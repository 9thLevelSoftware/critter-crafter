"""The single glTF <-> Blender frame mapping (docs/frame.md). No bpy import so it is unit-testable.

Blender authoring puts the catalog's forward (+Z glTF) on Blender +Y. Unity's FBX importer maps
Blender -Y ("front") to Unity -Z whatever the exporter's axis flags say, so this is what makes
catalog +Z land on Unity +Z (net glTF -> Unity: (-x, y, z), the same convention as glTFast).
The mapping is a proper rotation (180 deg about the diagonal) and is its own inverse.
"""

from __future__ import annotations

from typing import Sequence

# Row-major 3x3 matrix of the mapping (used by ops_assemble for rotations).
MATRIX = ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))


def to_blender(v: Sequence[float]) -> tuple[float, float, float]:
    """glTF (x, y, z) -> Blender (-x, z, y)."""
    return (-v[0], v[2], v[1])


def to_gltf(v: Sequence[float]) -> tuple[float, float, float]:
    """Blender (x, y, z) -> glTF (-x, z, y) (the mapping is an involution)."""
    return (-v[0], v[2], v[1])
