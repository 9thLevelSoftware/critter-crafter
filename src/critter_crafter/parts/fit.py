"""Fit a sourced mesh (e.g. a Meshy download) onto a binding-profile chain. Pure Python, no bpy.

A real part must satisfy the same contract as a placeholder (docs/frame.md): part space has its origin
at the attachment point, +Z along the part, +Y the dorsal/"up" side, and the part is straight in its
rest pose because bends come from the skeleton's clips. Generated meshes are rarely straight, so the
fit does four things:

1. **Centerline.** Geodesic distance from the root end, binned into level-set centroids, gives a
   centerline that follows bends and S-curves without assuming a straight axis.
2. **Straighten.** Every vertex is expressed in a rotation-minimising frame along the centerline and
   re-laid along +Z. The frame's +Y at the root is the convex side of the mesh's own bend, so a knee
   or elbow that was modelled bent flexes the same way under the profile's one-way flexion (flexion
   folds the distal segment toward part -Y, see ``archetypes._bone_limits``).
3. **Joint remap.** The mesh's natural joints (fractions along its length) are mapped piecewise
   linearly onto the profile's bone fractions, so the elbow bends where the elbow was modelled.
4. **Envelope.** One uniform scale gives the declared length; an optional radial scale brings the
   proximal cross-section to the profile girth so the part matches its branches and connectors.

Weights follow the chain parameter: flesh bands sized by the local radius (limbs), rubber-hose blending
(insect legs, tentacles), rigid segments with narrow joint bands (opt-in ``jointed``) or a single bone
(heads). Edge loops are cut where the weights change so low-poly meshes bend at a loop.
"""

from __future__ import annotations

import bisect
import heapq
import math
from typing import Any, Sequence

from .. import mathutil as mu

Vec = tuple[float, float, float]

AXES: dict[str, Vec] = {
    "+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0),
}

# Default weighting and radial fit per template.
TEMPLATE_DEFAULTS: dict[str, dict[str, Any]] = {
    "limb3": {"weights": "flesh", "radial_scale": "auto", "straighten": True},
    # Generated insect legs are thick and low-poly at their joints; narrow rigid-segment bands
    # collapse them in a crouched stance, rubber-hose blending does not (measured in part review).
    "insect_leg4": {"weights": "smooth", "radial_scale": "auto", "straighten": True},
    "tentacle8": {"weights": "smooth", "radial_scale": "auto", "straighten": True},
    "appendage1": {"weights": "single", "radial_scale": 1.0, "straighten": False},
    "head1": {"weights": "single", "radial_scale": 1.0, "straighten": False},
}

RADIAL_SCALE_RANGE = (0.6, 1.8)
BINS = 48
ROOT_SEED_FRACTION = 0.03
WINDOW = 6
REFINE_PASSES = 3
MAP_BINS = 64
SMOOTH_PASSES = 5
UP_MIN_SAGITTA = 0.04  # bend sagitta / chord below which the bend does not define "up"


class FitError(ValueError):
    pass


def resolve_params(fit: dict[str, Any], template: str) -> dict[str, Any]:
    """Authored fit block + template defaults -> complete parameters."""
    defaults = TEMPLATE_DEFAULTS.get(template, {"weights": "smooth", "radial_scale": 1.0, "straighten": True})
    params = {
        "axis": fit.get("axis"),
        "up": fit.get("up", "auto"),
        "up_fallback": fit.get("up_fallback"),
        "straighten": bool(fit.get("straighten", defaults["straighten"])),
        "trim_n": list(fit.get("trim_n", [0.005, 0.995])),
        "joints_n": fit.get("joints_n"),
        "radial_scale": fit.get("radial_scale", defaults["radial_scale"]),
        "weights": fit.get("weights", defaults["weights"]),
        "mirror_x": bool(fit.get("mirror_x", False)),
        "root_center": fit.get("root_center", "slice" if fit.get("straighten", defaults["straighten"]) else "bbox"),
    }
    if params["axis"] not in AXES:
        raise FitError(f"CC_FIT_AXIS: fit.axis must be one of {sorted(AXES)}, got {params['axis']!r}")
    if params["up"] != "auto" and params["up"] not in AXES:
        raise FitError(f"CC_FIT_UP: fit.up must be 'auto' or a signed axis, got {params['up']!r}")
    if params["up_fallback"] is not None and params["up_fallback"] not in AXES:
        raise FitError(f"CC_FIT_UP: fit.up_fallback must be a signed axis, got {params['up_fallback']!r}")
    lo, hi = params["trim_n"]
    if not 0.0 <= lo < hi <= 1.0:
        raise FitError(f"CC_FIT_TRIM: trim_n must satisfy 0 <= root < tip <= 1, got {params['trim_n']}")
    if params["weights"] not in ("smooth", "flesh", "jointed", "single"):
        raise FitError(f"CC_FIT_WEIGHTS: unknown weighting {params['weights']!r}")
    if params["root_center"] not in ("slice", "bbox"):
        raise FitError(f"CC_FIT_ROOT: root_center must be 'slice' or 'bbox', got {params['root_center']!r}")
    rs = params["radial_scale"]
    if rs != "auto" and not (isinstance(rs, (int, float)) and 0.25 <= float(rs) <= 4.0):
        raise FitError(f"CC_FIT_RADIAL: radial_scale must be 'auto' or 0.25..4, got {rs!r}")
    return params


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise FitError("CC_FIT_EMPTY: no vertices")
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = min(len(ordered) - 1, lo + 1)
    t = pos - lo
    return ordered[lo] * (1 - t) + ordered[hi] * t


def _edges(faces: Sequence[Sequence[int]]) -> set[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()
    for face in faces:
        n = len(face)
        for i in range(n):
            a, b = face[i], face[(i + 1) % n]
            if a != b:
                edges.add((a, b) if a < b else (b, a))
    return edges


def _components(n: int, edges: set[tuple[int, int]]) -> list[int]:
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return [find(i) for i in range(n)]


class _Grid:
    """Uniform-grid nearest neighbour over a fixed point set."""

    def __init__(self, points: Sequence[Vec], indices: Sequence[int], cell: float):
        self.points = points
        self.cell = cell
        self.cells: dict[tuple[int, int, int], list[int]] = {}
        for i in indices:
            self.cells.setdefault(self._key(points[i]), []).append(i)

    def _key(self, p: Sequence[float]) -> tuple[int, int, int]:
        c = self.cell
        return (int(math.floor(p[0] / c)), int(math.floor(p[1] / c)), int(math.floor(p[2] / c)))

    def nearest(self, p: Sequence[float]) -> tuple[int, float]:
        kx, ky, kz = self._key(p)
        best, best_d = -1, math.inf
        ring = 0
        while True:
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    for dz in range(-ring, ring + 1):
                        if max(abs(dx), abs(dy), abs(dz)) != ring:
                            continue
                        for i in self.cells.get((kx + dx, ky + dy, kz + dz), ()):
                            d = mu.length(mu.sub(self.points[i], p))
                            if d < best_d:
                                best, best_d = i, d
            if best >= 0 and best_d <= ring * self.cell:
                return best, best_d
            ring += 1
            if ring > 4096:
                return best, best_d


def _graph(verts: Sequence[Vec], faces: Sequence[Sequence[int]]) -> tuple[list[list[tuple[int, float]]], dict[str, int]]:
    """Surface graph with every disconnected island linked to the largest one at its closest vertex."""
    n = len(verts)
    edges = _edges(faces)
    adjacency: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for a, b in edges:
        w = mu.length(mu.sub(verts[a], verts[b]))
        adjacency[a].append((b, w))
        adjacency[b].append((a, w))
    roots = _components(n, edges)
    groups: dict[int, list[int]] = {}
    for i, r in enumerate(roots):
        groups.setdefault(r, []).append(i)
    main = max(groups.values(), key=len)
    lo = [min(v[k] for v in verts) for k in range(3)]
    hi = [max(v[k] for v in verts) for k in range(3)]
    diag = mu.length(mu.sub(hi, lo)) or 1.0
    grid = _Grid(verts, main, diag / 48.0)
    linked = 0
    for members in groups.values():
        if members is main:
            continue
        best = (math.inf, -1, -1)
        for i in members:
            j, d = grid.nearest(verts[i])
            if d < best[0]:
                best = (d, i, j)
        d, i, j = best
        adjacency[i].append((j, d))
        adjacency[j].append((i, d))
        linked += 1
    return adjacency, {"islands": len(groups), "islands_linked": linked, "main_island_vertices": len(main)}


def _dijkstra(adjacency: list[list[tuple[int, float]]], seeds: Sequence[int]) -> list[float]:
    dist = [math.inf] * len(adjacency)
    heap: list[tuple[float, int]] = []
    for s in seeds:
        dist[s] = 0.0
        heap.append((0.0, s))
    heapq.heapify(heap)
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist[i]:
            continue
        for j, w in adjacency[i]:
            nd = d + w
            if nd < dist[j]:
                dist[j] = nd
                heapq.heappush(heap, (nd, j))
    return dist


def _lerp(a: Sequence[float], b: Sequence[float], t: float) -> Vec:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _perp(v: Sequence[float], axis: Sequence[float]) -> Vec:
    return mu.sub(v, mu.scale(axis, mu.dot(v, axis)))


def _smooth(points: list[Vec], passes: int = 3) -> list[Vec]:
    for _ in range(passes):
        if len(points) < 3:
            return points
        points = [points[0]] + [
            _lerp(_lerp(points[i - 1], points[i + 1], 0.5), points[i], 0.5) for i in range(1, len(points) - 1)
        ] + [points[-1]]
    return points


def _tangents(points: Sequence[Vec]) -> list[Vec]:
    out = []
    for i in range(len(points)):
        a = points[max(0, i - 1)]
        b = points[min(len(points) - 1, i + 1)]
        out.append(mu.normalize(mu.sub(b, a)))
    return out


def _rmf(points: Sequence[Vec], tangents: Sequence[Vec], up0: Vec) -> list[Vec]:
    """Rotation-minimising reference vectors along a polyline (double reflection, Wang et al. 2008)."""
    r = [mu.normalize(_perp(up0, tangents[0]))]
    for i in range(len(points) - 1):
        v1 = mu.sub(points[i + 1], points[i])
        c1 = mu.dot(v1, v1)
        if c1 < 1e-18:
            r.append(r[-1])
            continue
        r_l = mu.sub(r[i], mu.scale(v1, 2.0 / c1 * mu.dot(v1, r[i])))
        t_l = mu.sub(tangents[i], mu.scale(v1, 2.0 / c1 * mu.dot(v1, tangents[i])))
        v2 = mu.sub(tangents[i + 1], t_l)
        c2 = mu.dot(v2, v2)
        r_next = r_l if c2 < 1e-18 else mu.sub(r_l, mu.scale(v2, 2.0 / c2 * mu.dot(v2, r_l)))
        r.append(mu.normalize(_perp(r_next, tangents[i + 1])))
    return r


def _pick_up_fallback(axis: Vec) -> Vec:
    for name in ("+y", "+z", "+x"):
        cand = AXES[name]
        if abs(mu.dot(cand, axis)) < 0.9:
            return cand
    return AXES["+y"]


def _centerline(verts: Sequence[Vec], faces: Sequence[Sequence[int]], axis: Vec,
                straighten: bool, root_center: str = "slice") -> tuple[list[Vec], list[float], dict[str, Any]]:
    """Centerline polyline and the per-vertex chain parameter (arc length along it)."""
    proj = [mu.dot(v, axis) for v in verts]
    p_lo, p_hi = min(proj), max(proj)
    span = p_hi - p_lo
    if span <= 1e-9:
        raise FitError("CC_FIT_DEGENERATE: mesh has no extent along fit.axis")
    seeds = [i for i, p in enumerate(proj) if p <= p_lo + ROOT_SEED_FRACTION * span]
    info: dict[str, Any] = {"root_seeds": len(seeds)}
    if not straighten:
        if root_center == "bbox":
            c = tuple(0.5 * (min(v[k] for v in verts) + max(v[k] for v in verts)) for k in range(3))
        else:
            root_slice = [verts[i] for i, p in enumerate(proj) if p <= p_lo + 0.05 * span]
            c = tuple(sum(v[k] for v in root_slice) / len(root_slice) for k in range(3))
        root = mu.add(_perp(c, axis), mu.scale(axis, p_lo))
        poly = [root, mu.add(root, mu.scale(axis, span))]
        info.update(islands=None, bins=1)
        return poly, [p - p_lo for p in proj], info
    adjacency, graph_info = _graph(verts, faces)
    info.update(graph_info)
    g_root = _dijkstra(adjacency, seeds)
    finite = [d for d in g_root if math.isfinite(d)]
    g_max = max(finite)
    if g_max <= 1e-9:
        raise FitError("CC_FIT_DEGENERATE: geodesic depth is zero")
    g_root = [d if math.isfinite(d) else g_max for d in g_root]
    tip = max(range(len(verts)), key=lambda i: g_root[i])
    g_tip = _dijkstra(adjacency, [tip])
    g_tip = [d if math.isfinite(d) else 0.0 for d in g_tip]
    # Level sets of (distance from root - distance from tip) cut an elongated shape into
    # near-perpendicular cross-sections, even through a bulky root blob where plain depth from the
    # root sweeps around it. Climbing a spike, tendril or finger raises both distances equally, so a
    # protrusion keeps the parameter of the cross-section it grows from and travels rigidly with it.
    diff = [a - b for a, b in zip(g_root, g_tip)]
    d_lo, d_hi = min(diff), max(diff)
    param = [(d - d_lo) / (d_hi - d_lo) for d in diff]
    centroids, areas = _face_samples(verts, faces)
    bins = max(12, min(BINS, len(faces) // 40))
    face_param = [sum(param[i] for i in face) / len(face) for face in faces]
    poly = _bin_polyline(centroids, areas, face_param, bins, span)
    info.update(tip_vertex=tip, bins_requested=bins)
    # Refine: re-bin by arc length on the current centerline (cross-sections, not level sets).
    for _ in range(REFINE_PASSES):
        s_vert = _level_set_arclength(verts, poly, param)
        s_face = [sum(s_vert[i] for i in face) / len(face) for face in faces]
        s_lo, s_hi = min(s_vert), max(s_vert)
        poly = _bin_polyline(centroids, areas, [(x - s_lo) / (s_hi - s_lo) for x in s_face], bins, span)
    info["bins"] = len(poly)
    return poly, _level_set_arclength(verts, poly, param), info


def _closest_s(v: Vec, poly: Sequence[Vec], cum: Sequence[float], hint: int) -> float:
    """Arc length of the closest centerline point near polyline index ``hint`` (ends extrapolate)."""
    last = len(poly) - 2
    best = (math.inf, 0.0)
    for k in range(max(0, hint - WINDOW), min(last, hint + WINDOW) + 1):
        a, b = poly[k], poly[k + 1]
        ab = mu.sub(b, a)
        seg = mu.length(ab)
        t = mu.dot(mu.sub(v, a), ab) / (seg * seg)
        if k > 0:
            t = max(0.0, t)
        if k < last:
            t = min(1.0, t)
        d = mu.length(mu.sub(v, mu.add(a, mu.scale(ab, t))))
        if d < best[0]:
            best = (d, cum[k] + t * seg)
    return best[1]


def _level_set_arclength(verts: Sequence[Vec], poly: Sequence[Vec], param: Sequence[float]) -> list[float]:
    """Map the level-set parameter onto arc length along ``poly``.

    Closest-point projection is locally exact but jumps between branches of a tight bend and shears
    protrusions; the level-set parameter is continuous everywhere. The map between them is a
    monotone median fit over parameter bins, so every vertex gets the arc length of its level set.
    """
    cum = [0.0]
    for a, b in zip(poly, poly[1:]):
        cum.append(cum[-1] + mu.length(mu.sub(b, a)))
    n = len(poly) - 1
    samples: list[list[float]] = [[] for _ in range(MAP_BINS)]
    for v, u in zip(verts, param):
        s = _closest_s(v, poly, cum, min(n - 1, max(0, int(round(u * n)))))
        samples[min(MAP_BINS - 1, int(u * MAP_BINS))].append(s)
    knots: list[tuple[float, float]] = []
    running = -math.inf
    for i, values in enumerate(samples):
        if not values:
            continue
        running = max(running, _quantile(values, 0.5))
        knots.append(((i + 0.5) / MAP_BINS, running))
    if len(knots) < 2:
        return [cum[-1] * u for u in param]

    def f(u: float) -> float:
        if u <= knots[0][0]:
            (a, sa), (b, sb) = knots[0], knots[1]
        elif u >= knots[-1][0]:
            (a, sa), (b, sb) = knots[-2], knots[-1]
        else:
            k = bisect.bisect_right([x for x, _ in knots], u) - 1
            (a, sa), (b, sb) = knots[k], knots[k + 1]
        return sa + (u - a) * (sb - sa) / (b - a)

    return [f(u) for u in param]


def _frame_at(s: float, poly: Sequence[Vec], cum: Sequence[float], tangents: Sequence[Vec],
              refs: Sequence[Vec]) -> tuple[Vec, Vec, Vec]:
    """Centerline point, tangent and up reference at arc length ``s`` (ends extrapolate)."""
    if s <= 0.0:
        return mu.add(poly[0], mu.scale(tangents[0], s)), tangents[0], refs[0]
    if s >= cum[-1]:
        return mu.add(poly[-1], mu.scale(tangents[-1], s - cum[-1])), tangents[-1], refs[-1]
    k = min(len(poly) - 2, bisect.bisect_right(cum, s) - 1)
    t = (s - cum[k]) / (cum[k + 1] - cum[k])
    tan = mu.normalize(_lerp(tangents[k], tangents[k + 1], t))
    return _lerp(poly[k], poly[k + 1], t), tan, mu.normalize(_perp(_lerp(refs[k], refs[k + 1], t), tan))


def _face_samples(verts: Sequence[Vec], faces: Sequence[Sequence[int]]) -> tuple[list[Vec], list[float]]:
    """Face centroids and areas (Newell), so sampling follows surface area, not vertex density."""
    centroids, areas = [], []
    for face in faces:
        pts = [verts[i] for i in face]
        n = len(pts)
        c = (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n, sum(p[2] for p in pts) / n)
        nx = ny = nz = 0.0
        for k in range(n):
            a, b = pts[k], pts[(k + 1) % n]
            nx += (a[1] - b[1]) * (a[2] + b[2])
            ny += (a[2] - b[2]) * (a[0] + b[0])
            nz += (a[0] - b[0]) * (a[1] + b[1])
        centroids.append(c)
        areas.append(0.5 * math.sqrt(nx * nx + ny * ny + nz * nz))
    return centroids, areas


def _bin_polyline(samples: Sequence[Vec], weights: Sequence[float], sample_param: Sequence[float],
                  bins: int, span: float) -> list[Vec]:
    """Area-weighted centroid per parameter bin -> smoothed polyline extended past both ends."""
    sums = [[0.0, 0.0, 0.0, 0.0] for _ in range(bins)]
    for p, w, u in zip(samples, weights, sample_param):
        acc = sums[min(bins - 1, max(0, int(u * bins)))]
        acc[0] += p[0] * w
        acc[1] += p[1] * w
        acc[2] += p[2] * w
        acc[3] += w
    # End bins are partial (caps, claw tips, open rims) and their centroids wander; keep the
    # well-populated interior and extend it along a robust end tangent instead.
    mass = sorted(acc[3] for acc in sums if acc[3] > 0)
    floor = 0.2 * mass[len(mass) // 2] if mass else 0.0
    populated = [i for i, acc in enumerate(sums) if acc[3] > 0 and acc[3] >= floor]
    keep = set(populated[1:-1]) if len(populated) > 4 else set(populated)
    poly = [(acc[0] / acc[3], acc[1] / acc[3], acc[2] / acc[3]) for i, acc in enumerate(sums) if i in keep]
    poly = _smooth(poly, SMOOTH_PASSES)
    if len(poly) >= 4:
        def end_dir(points: Sequence[Vec]) -> Vec:
            return mu.normalize(mu.sub(points[-1], points[0]))
        head = end_dir(list(reversed(poly[:4])))
        tail = end_dir(poly[-4:])
        ext = 0.04 * span
        poly = [mu.add(poly[0], mu.scale(head, ext))] + poly + [mu.add(poly[-1], mu.scale(tail, ext))]
    # Drop near-duplicate points so every segment has a usable tangent.
    clean = [poly[0]]
    for p in poly[1:]:
        if mu.length(mu.sub(p, clean[-1])) > 1e-6 * span:
            clean.append(p)
    if len(clean) < 2:
        raise FitError("CC_FIT_DEGENERATE: centerline collapsed to a point")
    return clean


def _joint_map(natural: Sequence[float], target: Sequence[float]) -> list[tuple[float, float]]:
    knots = [(0.0, 0.0)] + list(zip(natural, target)) + [(1.0, 1.0)]
    for (a, _), (b, _) in zip(knots, knots[1:]):
        if b <= a:
            raise FitError(f"CC_FIT_JOINTS: joints_n must be strictly increasing inside (0, 1), got {list(natural)}")
    return knots


def _remap(f: float, knots: Sequence[tuple[float, float]]) -> float:
    if f <= knots[0][0]:
        (a, ta), (b, tb) = knots[0], knots[1]
    elif f >= knots[-1][0]:
        (a, ta), (b, tb) = knots[-2], knots[-1]
    else:
        k = next(i for i in range(len(knots) - 1) if knots[i][0] <= f <= knots[i + 1][0])
        (a, ta), (b, tb) = knots[k], knots[k + 1]
    return ta + (f - a) * (tb - ta) / (b - a)


def _smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def chain_weights(z: float, bones: Sequence[tuple[float, float]], mode: str,
                  bands: Sequence[float] | None = None) -> dict[str, float]:
    """Per-vertex weights from the chain coordinate z (metres along the part)."""
    names = [f"b{i}" for i in range(len(bones))]
    if mode == "single" or len(bones) == 1:
        return {"b0": 1.0}
    if mode == "smooth":
        centers = [(names[i], 0.5 * (a + b)) for i, (a, b) in enumerate(bones)]
        if z <= centers[0][1]:
            return {centers[0][0]: 1.0}
        if z >= centers[-1][1]:
            return {centers[-1][0]: 1.0}
        for (n0, c0), (n1, c1) in zip(centers, centers[1:]):
            if c0 <= z <= c1:
                u = (z - c0) / (c1 - c0)
                return {n0: 1 - u, n1: u} if 0 < u < 1 else {n0 if u <= 0 else n1: 1.0}
    for k in range(len(bones) - 1):
        joint = bones[k][1]
        half = bands[k] if bands else 0.12 * min(bones[k][1] - bones[k][0], bones[k + 1][1] - bones[k + 1][0])
        if joint - half <= z <= joint + half:
            u = _smoothstep((z - (joint - half)) / (2 * half))
            if u <= 0.0:
                return {names[k]: 1.0}
            if u >= 1.0:
                return {names[k + 1]: 1.0}
            return {names[k]: 1 - u, names[k + 1]: u}
    for i, (a, b) in enumerate(bones):
        if a <= z <= b:
            return {names[i]: 1.0}
    return {names[0] if z < bones[0][0] else names[-1]: 1.0}


def fit(verts: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], fit_block: dict[str, Any],
        template: str, fractions: Sequence[float], length_m: float, girth_m: float) -> dict[str, Any]:
    """Fit ``verts`` (source glTF frame) onto a chain; return part-space positions, weights, metrics."""
    params = resolve_params(fit_block, template)
    src: list[Vec] = [(float(v[0]), float(v[1]), float(v[2])) for v in verts]
    if not src:
        raise FitError("CC_FIT_EMPTY: no vertices")
    axis = AXES[params["axis"]]
    poly, s_vert, info = _centerline(src, faces, axis, params["straighten"], params["root_center"])
    tangents = _tangents(poly)
    cum = [0.0]
    for a, b in zip(poly, poly[1:]):
        cum.append(cum[-1] + mu.length(mu.sub(b, a)))

    # Part +Y: the convex side of the modelled bend when it is clear, else the authored axis.
    up_source = "authored"
    if params["up"] == "auto":
        chord = mu.sub(poly[-1], poly[0])
        chord_len = mu.length(chord)
        up_vec = None
        if chord_len > 1e-9 and len(poly) > 2:
            chord_dir = mu.scale(chord, 1.0 / chord_len)
            offsets = [_perp(mu.sub(p, poly[0]), chord_dir) for p in poly]
            apex = max(offsets, key=mu.length)
            info["bend_sagitta_n"] = round(mu.length(apex) / chord_len, 4)
            if mu.length(apex) / chord_len >= UP_MIN_SAGITTA and mu.length(_perp(apex, tangents[0])) > 1e-9:
                up_vec = mu.normalize(apex)
                up_source = "bend"
        if up_vec is None:
            up_vec = AXES[params["up_fallback"]] if params["up_fallback"] else _pick_up_fallback(tangents[0])
            up_source = "fallback"
    else:
        up_vec = AXES[params["up"]]
    if abs(mu.dot(mu.normalize(up_vec), tangents[0])) > 0.98:
        raise FitError("CC_FIT_UP: up is (nearly) parallel to the part's root direction")
    refs = _rmf(poly, tangents, up_vec)

    # Straighten: (x, y, s) in the rotation-minimising frame. The level-set arc length can lead or
    # lag the true cross-section on a bend; adding the offset's tangent component corrects the
    # along-chain coordinate to first order.
    local: list[Vec] = []
    for v, s in zip(src, s_vert):
        c, tan, ref = _frame_at(s, poly, cum, tangents, refs)
        side = mu.cross(ref, tan)
        d = mu.sub(v, c)
        local.append((mu.dot(d, side), mu.dot(d, ref), s + mu.dot(d, tan)))

    lo_q, hi_q = params["trim_n"]
    s_values = [p[2] for p in local]
    s0, s1 = _quantile(s_values, lo_q), _quantile(s_values, hi_q)
    if s1 - s0 <= 1e-9:
        raise FitError("CC_FIT_DEGENERATE: trimmed length is zero")
    k_scale = float(length_m) / (s1 - s0)
    targets = []
    acc = 0.0
    for frac in fractions[:-1]:
        acc += float(frac)
        targets.append(acc)
    natural = params["joints_n"] if params["joints_n"] is not None else targets
    if len(natural) != len(targets):
        raise FitError(f"CC_FIT_JOINTS: {template} needs {len(targets)} interior joint fraction(s)")
    knots = _joint_map([float(x) for x in natural], targets)
    part = [(x * k_scale, y * k_scale, _remap((s - s0) / (s1 - s0), knots) * float(length_m)) for x, y, s in local]

    # Radial envelope: proximal cross-section to the profile girth.
    radial = []
    for q in range(8):
        a, b = (0.1 + 0.05 * q) * length_m, (0.15 + 0.05 * q) * length_m
        rs = [math.hypot(x, y) for x, y, z in part if a <= z < b]
        if rs:
            radial.append(_quantile(rs, 0.9))
    nominal_girth = 2.0 * _quantile(radial, 0.5) if radial else 0.0
    rs_param = params["radial_scale"]
    if rs_param == "auto":
        raw = girth_m / nominal_girth if nominal_girth > 1e-9 else 1.0
        radial_scale = min(RADIAL_SCALE_RANGE[1], max(RADIAL_SCALE_RANGE[0], raw))
    else:
        raw = radial_scale = float(rs_param)
    mirror = -1.0 if params["mirror_x"] else 1.0
    part = [(mirror * x * radial_scale, y * radial_scale, z) for x, y, z in part]

    # Weights.
    bones = []
    z = 0.0
    for frac in fractions:
        bones.append((z, z + float(frac) * length_m))
        z += float(frac) * length_m
    bands = None
    if params["weights"] in ("flesh", "jointed"):
        bands = []
        for k in range(len(bones) - 1):
            joint = bones[k][1]
            shorter = min(bones[k][1] - bones[k][0], bones[k + 1][1] - bones[k + 1][0])
            if params["weights"] == "jointed":
                bands.append(0.12 * shorter)
            else:
                near = [math.hypot(x, y) for x, y, zz in part if abs(zz - joint) < 0.05 * length_m]
                local_r = _quantile(near, 0.5) if near else 0.1 * length_m
                bands.append(min(0.45 * shorter, max(0.06 * length_m, 0.6 * local_r)))
    weights = [chain_weights(p[2], bones, params["weights"], bands) for p in part]

    # Edge strain of the straightening (uniform scale removed): tears show up as large ratios.
    strains = []
    for a, b in _edges(faces):
        before = mu.length(mu.sub(src[a], src[b])) * k_scale
        if before > 1e-9:
            after = mu.length(mu.sub((local[a][0] * k_scale, local[a][1] * k_scale, part[a][2]),
                                     (local[b][0] * k_scale, local[b][1] * k_scale, part[b][2])))
            strains.append(after / before)
    fifth = min(len(poly) - 1, max(1, len(poly) // 5))
    t_root = mu.normalize(mu.sub(poly[fifth], poly[0]))
    t_tip = mu.normalize(mu.sub(poly[-1], poly[-1 - fifth]))
    bend = math.degrees(math.acos(max(-1.0, min(1.0, mu.dot(t_root, t_tip)))))
    lo = [min(p[k] for p in part) for k in range(3)]
    hi = [max(p[k] for p in part) for k in range(3)]
    outside = sum(1 for p in part if p[2] < -1e-6 or p[2] > length_m + 1e-6) / len(part)
    metrics = {
        **info,
        "up_source": up_source,
        "centerline_length_source": round(cum[-1], 6),
        "end_to_end_bend_deg": round(bend, 3),
        "uniform_scale": round(k_scale, 6),
        "nominal_girth_m": round(nominal_girth, 6),
        "radial_scale_wanted": round(raw, 6),
        "radial_scale": round(radial_scale, 6),
        "bands_m": [round(b, 6) for b in bands] if bands else [],
        "strain_p01": round(_quantile(strains, 0.01), 4) if strains else 1.0,
        "strain_p99": round(_quantile(strains, 0.99), 4) if strains else 1.0,
        "strain_max": round(max(strains), 4) if strains else 1.0,
        "outside_chain_fraction": round(outside, 4),
        "bounds_m": [[round(x, 6) for x in lo], [round(x, 6) for x in hi]],
        "dimensions_m": [round(hi[0] - lo[0], 6), round(hi[1] - lo[1], 6), round(hi[2] - lo[2], 6)],
    }
    return {"positions": part, "weights": weights, "params": params, "metrics": metrics,
            "bones": bones, "bands": bands, "planes": joint_planes(bones, bands, params["weights"]),
            "centerline": [tuple(round(c, 6) for c in p) for p in poly]}


def joint_planes(bones: Sequence[tuple[float, float]], bands: Sequence[float] | None, mode: str) -> list[float]:
    """Chain coordinates where the mesh needs an edge loop so skinning bends at a loop, not across
    one long triangle (generated low-poly meshes rarely have loops where the profile's joints are).

    Banded modes get a loop at each joint and at both band edges; rubber-hose blending gets loops at
    joints and bone centres, where its weights change slope.
    """
    if mode == "single" or len(bones) < 2:
        return []
    planes: list[float] = []
    for k in range(len(bones) - 1):
        joint = bones[k][1]
        if mode == "smooth":
            planes += [0.5 * (bones[k][0] + bones[k][1]), joint]
        else:
            half = bands[k] if bands else 0.0
            planes += [joint - half, joint, joint + half] if half > 0 else [joint]
    if mode == "smooth":
        planes.append(0.5 * (bones[-1][0] + bones[-1][1]))
    return sorted({round(z, 6) for z in planes})
