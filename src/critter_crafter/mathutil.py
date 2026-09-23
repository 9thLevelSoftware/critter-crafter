"""Small dependency-free vector/quaternion helpers (glTF frame, quaternions are [x, y, z, w])."""

from __future__ import annotations

import math
from typing import Sequence

Vec = tuple[float, float, float]


def mm(x: float) -> int:
    """Metres -> integer millimetres, round half away from zero (see docs/generator.md)."""
    v = abs(x) * 1000.0
    r = int(math.floor(v + 0.5))
    return -r if x < 0 else r


def add(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Sequence[float], s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Sequence[float]) -> float:
    return math.sqrt(dot(a, a))


def normalize(a: Sequence[float]) -> Vec:
    n = length(a)
    if n < 1e-9:
        raise ValueError(f"cannot normalize zero-length vector {tuple(a)}")
    return scale(a, 1.0 / n)


def frame_from_dir_up(direction: Sequence[float], up: Sequence[float]) -> tuple[Vec, Vec, Vec]:
    """Orthonormal right-handed basis (x, y, z) with z = direction and y ~ up."""
    z = normalize(direction)
    y_raw = sub(up, scale(z, dot(up, z)))
    y = normalize(y_raw)
    x = cross(y, z)
    return x, y, z


def quat_from_basis(x: Sequence[float], y: Sequence[float], z: Sequence[float]) -> tuple[float, float, float, float]:
    """Quaternion [x, y, z, w] of the rotation whose matrix columns are x, y, z."""
    m00, m10, m20 = x
    m01, m11, m21 = y
    m02, m12, m22 = z
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        qx = (m21 - m12) / s
        qy = (m02 - m20) / s
        qz = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        w = (m21 - m12) / s
        qx = 0.25 * s
        qy = (m01 + m10) / s
        qz = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        w = (m02 - m20) / s
        qx = (m01 + m10) / s
        qy = 0.25 * s
        qz = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        w = (m10 - m01) / s
        qx = (m02 + m20) / s
        qy = (m12 + m21) / s
        qz = 0.25 * s
    if w < 0:  # canonical hemisphere
        qx, qy, qz, w = -qx, -qy, -qz, -w
    return (qx, qy, qz, w)


def quat_rotate(q: Sequence[float], v: Sequence[float]) -> Vec:
    qx, qy, qz, w = q
    u = (qx, qy, qz)
    t = scale(cross(u, v), 2.0)
    return add(add(v, scale(t, w)), cross(u, t))


def r6(x: float) -> float:
    """Round for stable JSON output."""
    v = round(x, 6)
    return 0.0 if v == 0 else v


def r6v(v: Sequence[float]) -> list[float]:
    return [r6(c) for c in v]
