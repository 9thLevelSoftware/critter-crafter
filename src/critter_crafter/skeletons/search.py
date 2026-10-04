"""Sparse deterministic MAP-Elites with real Blender/Unity functional evaluation."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
import os
from pathlib import Path
from typing import Any, Callable

import click

from ..config import find_blender, find_unity, paths
from ..recipes.rng import SplitMix64
from .morphology import (LINEAGES, MorphologyError, MorphologyParams, build_morphology, canonical,
                         choices, count_range, morphology_identity, parameter_ranges)


class SearchError(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _write(path: Path, value: Any) -> None:
    if os.name == "nt" and len(str(path.resolve())) >= 260:
        raise SearchError(f"CC_SEARCH_PATH: source/cache path exceeds 259 characters; shorten the search root: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def _shuffle(values: list[Any], rng: SplitMix64) -> None:
    for index in range(len(values) - 1, 0, -1):
        other = rng.below(index + 1)
        values[index], values[other] = values[other], values[index]


def _short_directory(root: Path, kind: str, digest: str) -> Path:
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise SearchError("CC_SEARCH_HASH_COLLISION: invalid full cache identity")
    directory = root / kind / digest[:12]
    identity = directory / "identity.json"
    if identity.is_file():
        if json.loads(identity.read_text(encoding="utf-8")) != {"sha256": digest}:
            raise SearchError("CC_SEARCH_HASH_COLLISION: compact cache token has a different full identity")
    elif directory.exists() and any(directory.iterdir()):
        raise SearchError("CC_SEARCH_HASH_COLLISION: compact cache directory has no authoritative identity")
    else:
        _write(identity, {"sha256": digest})
    return directory


def asset_path_length(catalog: dict, root: Path, *, windows: bool | None = None) -> int:
    """Check the exact existing build filenames before Blender encounters MAX_PATH."""
    from ..library.commands import library_dir
    out = library_dir(catalog, root).resolve()
    filenames = []
    for skeleton in catalog["skeletons"]:
        sid = skeleton["skeleton_id"]
        filenames.extend(out / "skeletons" / sid / f"{sid}{suffix}" for suffix in (".fbx", ".glb", ".blend"))
        filenames.extend((out / "skeletons" / sid / "motion.json", out / "skeletons" / sid / "assembled.glb"))
    for part in catalog["parts"]:
        pid = part["part_id"]
        kind = "connectors" if part["category"] == "connector" else "parts"
        filenames.extend(out / kind / pid / f"{pid}{suffix}" for suffix in (".fbx", ".glb"))
        if part.get("real"):
            filenames.append(out / kind / pid / f"{pid}_albedo.png")
        filenames.append(paths().work / "masters" / kind / pid / "master.blend")
    longest = max(filenames, key=lambda filename: len(str(filename)), default=out)
    length = len(str(longest))
    if (os.name == "nt" if windows is None else windows) and length >= 260:
        raise SearchError(f"CC_SEARCH_PATH: asset path is {length} characters (maximum 259); "
                          f"shorten the search root: {longest}")
    return length


@dataclass(frozen=True)
class SearchSettings:
    seed: int = 1
    initial: int = 256
    mutations: int = 768
    lineages: tuple[str, ...] = LINEAGES
    evaluator_revision: str = "cc-functional-search-1"
    # Hard regressions are compared to the same lineage's authored control, not rewarded.
    regression_tolerance_m: float = 1e-5

    def validate(self) -> None:
        if self.seed < 0 or self.initial < 1 or self.mutations < 0 or not self.lineages:
            raise SearchError("CC_SEARCH_SETTINGS: invalid proposal counts, seed or lineages")
        if len(set(self.lineages)) != len(self.lineages) or set(self.lineages) - set(LINEAGES):
            raise SearchError("CC_SEARCH_SETTINGS: unknown or duplicate lineage")


def initial_samples(settings: SearchSettings) -> list[tuple[str, int, MorphologyParams]]:
    settings.validate()
    lineages = tuple(lineage for lineage in LINEAGES if lineage in settings.lineages)
    lineage_rows = {lineage: list(range(index, settings.initial, len(lineages)))
                    for index, lineage in enumerate(lineages)}
    result: dict[int, tuple[str, int, MorphologyParams]] = {}
    for lineage in lineages:
        indices = lineage_rows[lineage]
        rng = SplitMix64(settings.seed * 104729 + int(_hash(lineage)[:16], 16))
        continuous: dict[str, list[float]] = {}
        for field, (low, high) in parameter_ranges(lineage).items():
            strata = list(range(len(indices)))
            _shuffle(strata, rng)
            continuous[field] = [low + (high - low) * (stratum + rng.below(10000) / 10000) / len(indices)
                                 for stratum in strata]
        for ordinal, index in enumerate(indices):
            values = {field: samples[ordinal] for field, samples in continuous.items()}
            counts = count_range(lineage)
            if counts is not None:
                values["topology_count"] = counts[0] + rng.below(counts[1] - counts[0] + 1)
            for field, allowed in choices(lineage).items():
                values[field] = allowed[rng.below(len(allowed))]
            result[index] = lineage, settings.seed + index, MorphologyParams(**values).quantized()
    return [result[index] for index in range(settings.initial)]


def mutate(lineage: str, params: MorphologyParams, rng: SplitMix64) -> MorphologyParams:
    ranges = parameter_ranges(lineage)
    discrete = choices(lineage)
    counts = count_range(lineage)
    fields = sorted((*ranges, *discrete, *(("topology_count",) if counts and counts[0] != counts[1] else ())))
    field = fields[rng.below(len(fields))]
    if field in ranges:
        low, high = ranges[field]
        value = getattr(params, field) + (1 if rng.below(2) else -1) * (high - low) / 20
        if not low - 1e-12 <= value <= high + 1e-12:
            raise MorphologyError("CC_MORPHOLOGY_PARAMETER: mutation outside bounds")
    elif field == "topology_count":
        value = params.topology_count + (1 if rng.below(2) else -1)
        if not counts[0] <= value <= counts[1]:
            raise MorphologyError("CC_MORPHOLOGY_PARAMETER: mutation count outside bounds")
    else:
        alternatives = [value for value in discrete[field] if value != getattr(params, field)]
        value = alternatives[rng.below(len(alternatives))]
    return replace(params, **{field: value}).quantized()


def descriptor(source: dict) -> tuple[int, int, int, int]:
    silhouette = source["anatomy"]["silhouette"]
    contacts = sum(len(branch.get("contacts", [])) for branch in source["branches"])
    values = (contacts, silhouette["height_m"] / silhouette["length_m"],
              silhouette["width_m"] / silhouette["length_m"], int(source["anatomy"]["traits"]["body_on_ground"]))
    bins = []
    for value, (low, high, count) in zip(values, ((0, 17, 17), (.05, 5, 12), (.05, 5, 12), (0, 2, 2))):
        if not math.isfinite(value) or not low <= value < high:
            raise SearchError("CC_SEARCH_DESCRIPTOR: descriptor outside archive domain")
        bins.append(math.floor((value - low) / (high - low) * count))
    return tuple(bins)


def screen(source: dict, directory: Path) -> tuple[dict | None, list[str]]:
    """Validate the ordinary source/compiler/fit contracts in an ignored source tree."""
    from ..library.catalog import load_sources
    from ..schema.validate import validate_sources
    from ..locomotion.qa import evaluate_locomotion
    from .actions import resolve_attack
    from .motion import build_motion
    sources = load_sources(paths().data)
    sources["skeletons"] = [source]
    sources["parts"] = []  # public reference assets; never copy private meshes into search inputs
    sources["pools"] = [{"pool_id": "search", "families": [source["family"]],
                         "skeleton_ids": [source["skeleton_id"]]}]
    _write(directory / "library.json", sources["library"])
    _write(directory / "gait_profiles.v3.json", sources["gait"])
    _write(directory / "pools/pools.json", {"schema_version": "3.0.0", "pools": sources["pools"]})
    for group, subdir, identity, suffix in (("templates", "branch_templates", "template_id", ".json"),
            ("binding_profiles", "binding_profiles", "binding_profile_id", ".binding.json"),
            ("skeletons", "skeletons", "skeleton_id", ".skeleton.json")):
        for item in sources[group]:
            _write(directory / subdir / (item[identity] + suffix), item)
    for key, name in (("binding_profile_registry", "registry"), ("binding_profile_baseline", "released_registry")):
        _write(directory / f"binding_profiles/{name}.json", {"schema_version": "3.0.0", "profiles": sources[key]})
    diagnostics, catalog = validate_sources(directory, paths().schemas)
    diagnostics = [diagnostic for diagnostic in diagnostics if not diagnostic.startswith("CC_NO_CANDIDATE_OPTIONAL")]
    if catalog is None or diagnostics:
        return None, diagnostics
    skeleton = catalog["skeletons"][0]
    try:
        resolve_attack(skeleton)
        gait = next(gait for gait in catalog["gait_profiles"] if gait["hint"] == skeleton["locomotion_hint"])
        build_motion(skeleton, gait)
        diagnostics.extend(str(value) for value in evaluate_locomotion(skeleton))
    except (ValueError, KeyError) as exc:
        diagnostics.append(str(exc))
    return (catalog if not diagnostics else None), diagnostics


REQUIRED_METRICS = ("all_frames_max_planted_slip_m", "all_frames_max_planted_hover_m",
    "all_frames_max_stance_drift_m", "all_frames_max_body_rise_m", "max_body_contact_hover_m",
    "max_surface_penetration_m", "max_penetration_m", "max_reach_fraction", "support_violations")


def functional_score(rows: dict[str, list[dict]], height: float, baseline: dict[str, list[dict]],
                     tolerance: float = 1e-5) -> tuple[float | None, list[str]]:
    diagnostics = []
    if not math.isfinite(height) or height <= 0 or tolerance < 0:
        return None, ["CC_SEARCH_MEASUREMENT_INCOMPLETE: invalid normalization height/tolerance"]
    maxima = {field: 0. for field in REQUIRED_METRICS}
    for course, count in (("review", 3), ("turns", 3), ("reactions", 1)):
        measurements = rows.get(course, [])
        controls = baseline.get(course, [])
        if len(measurements) != count or len(controls) != count:
            diagnostics.append(f"CC_SEARCH_MEASUREMENT_INCOMPLETE: {course} expected {count} jobs")
            continue
        for index, row in enumerate(measurements):
            control = controls[index]
            expected_label = ("walk", "run", "max")[index] if course != "reactions" else "run"
            for item in (row, control):
                if (item.get("grounding_measurement_complete") is not True or item.get("support_measurement_complete") is not True
                        or item.get("frames", 0) < 2 or item.get("speed_label") != expected_label
                        or any(field not in item for field in REQUIRED_METRICS)
                        or any(type(item[field]) not in (int, float) or not math.isfinite(item[field]) or item[field] < 0
                               for field in REQUIRED_METRICS if field in item)):
                    diagnostics.append(f"CC_SEARCH_MEASUREMENT_INCOMPLETE: {course}: {item.get('grounding_measurement_problem', '')}")
                    break
            else:
                if row["support_violations"] or row["max_reach_fraction"] > .970001:
                    diagnostics.append(f"CC_SEARCH_SUPPORT_REACH: {course}")
                if course != "reactions":
                    if row["max_body_contact_hover_m"] > .01 * height or row["max_surface_penetration_m"] > .005 * height:
                        diagnostics.append(f"CC_SEARCH_GROUNDING: {course}")
                    for field in ("all_frames_max_stance_drift_m", "all_frames_max_body_rise_m"):
                        if row[field] > control[field] + tolerance:
                            diagnostics.append(f"CC_SEARCH_REGRESSION: {course}: {field}")
                    for field in REQUIRED_METRICS:
                        maxima[field] = max(maxima[field], row[field])
    if diagnostics:
        return None, diagnostics
    score = 1 / (1 + 10 * maxima["all_frames_max_planted_slip_m"] / height
                 + 10 * max(maxima["all_frames_max_planted_hover_m"], maxima["max_body_contact_hover_m"]) / height
                 + 10 * max(maxima["max_penetration_m"], maxima["max_surface_penetration_m"]) / height)
    return score, []


def evaluation_fingerprint(settings: SearchSettings) -> str:
    root = paths().root
    files = {}
    for directory, pattern in ((root / "src/critter_crafter", "*.py"), (root / "schemas", "*.json"),
                               (root / "data", "*.json"), (root / "unity/com.ninthlevelsoftware.crittercrafter", "*.cs")):
        for file in sorted(directory.rglob(pattern)):
            files[file.relative_to(root).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    for directory in (root / "work/polish/skeletons", root / "unity/TestProject/ProjectSettings",
                      root / "unity/TestProject/Packages"):
        if directory.is_dir():
            for file in sorted(directory.rglob("*")):
                if file.is_file():
                    files[file.relative_to(root).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    files["tools/frames_to_gif.py"] = hashlib.sha256((root / "tools/frames_to_gif.py").read_bytes()).hexdigest()
    tools = {}
    for name, executable in (("blender", find_blender()), ("unity", find_unity())):
        if executable and Path(executable).is_file():
            stat = Path(executable).stat()
            tools[name] = {"path": executable, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        else:
            tools[name] = None
    return _hash({"settings": asdict(settings), "files": files, "tools": tools})


class UnityEvaluator:
    def __init__(self, settings: SearchSettings, fingerprint: str):
        self.settings, self.fingerprint = settings, fingerprint

    def capture(self, library: Path, skeleton_id: str, out: Path) -> dict[str, list[dict]]:
        unity = find_unity()
        if not unity:
            raise SearchError("CC_SEARCH_UNITY_UNAVAILABLE: configure CRITTER_UNITY")
        rows = {}
        for course, speeds in (("review", "walk,run,max"), ("turns", "walk,run,max"), ("reactions", "run")):
            destination = (out / course).resolve()
            destination.mkdir(parents=True, exist_ok=True)
            args = [unity, "-batchmode", "-projectPath", str((paths().root / "unity/TestProject").resolve()),
                    "-executeMethod", "CritterCrafter.Editor.LocomotionCapture.CaptureFromCommandLine",
                    "-critterLibrary", str(library.resolve()), "-critterOut", str(destination),
                    "-critterSkeletons", skeleton_id, "-critterSpeeds", speeds, "-critterCourse", course,
                    "-logFile", str(destination / "unity.log")]
            subprocess.run(args, check=True)
            metrics = destination / "metrics.json"
            if not metrics.is_file():
                raise SearchError(f"CC_SEARCH_MEASUREMENT_INCOMPLETE: {course}: capture produced no metrics")
            rows[course] = json.loads(metrics.read_text(encoding="utf-8"))
            if not isinstance(rows[course], list) or any(row.get("skeleton_id") != skeleton_id for row in rows[course]):
                raise SearchError(f"CC_SEARCH_MEASUREMENT_INCOMPLETE: {course}: wrong capture identity")
            subprocess.run([sys.executable, str(paths().root / "tools/frames_to_gif.py"), str(destination)], check=True)
        return rows

    def __call__(self, source: dict, catalog: dict, directory: Path) -> dict:
        from ..library.commands import build_catalog
        from .commands import _qa
        from .archetypes import build_candidate
        from .amalgam import _build_amalgam
        asset_path_length(catalog, directory)
        library = build_catalog(copy.deepcopy(catalog), directory, clean=False)
        baked = json.loads((library / "catalog.json").read_text(encoding="utf-8"))
        qa = _qa(baked, library, baked["skeletons"])
        if any(not report["passed"] for report in qa):
            return {"passed": False, "diagnostics": ["CC_SEARCH_QA: baked motion/action/export failed"], "qa": qa}
        lineage, seed = source["anatomy"]["archetype_id"], source["provenance"]["seed"]
        control = (_build_amalgam(seed, mode=lineage.removeprefix("amalgam_")) if lineage.startswith("amalgam_")
                   else build_candidate(lineage, "balanced", seed))
        control_dir = _short_directory(directory.parent.parent, "k", _hash({"source": control, "evaluation": self.fingerprint}))
        control_metrics_path = control_dir / "metrics.json"
        if _cache_valid(control_dir / "result.json", self.fingerprint):
            baseline = json.loads(control_metrics_path.read_text(encoding="utf-8"))
        else:
            control_catalog, diagnostics = screen(control, control_dir / "source")
            if diagnostics or control_catalog is None:
                return {"passed": False, "diagnostics": ["CC_SEARCH_CONTROL_INVALID", *diagnostics]}
            asset_path_length(control_catalog, control_dir)
            control_library = build_catalog(control_catalog, control_dir, clean=False)
            control_baked = json.loads((control_library / "catalog.json").read_text(encoding="utf-8"))
            control_qa = _qa(control_baked, control_library, control_baked["skeletons"])
            if any(not report["passed"] for report in control_qa):
                return {"passed": False, "diagnostics": ["CC_SEARCH_CONTROL_QA"], "qa": control_qa}
            baseline = self.capture(control_library, control["skeleton_id"], control_dir / "capture")
            _write(control_metrics_path, baseline)
            _save_cache(control_dir / "result.json", {"passed": True, "qa": control_qa}, self.fingerprint)
        measurements = self.capture(library, source["skeleton_id"], directory / "capture")
        score, diagnostics = functional_score(measurements, source["anatomy"]["silhouette"]["height_m"], baseline,
                                              self.settings.regression_tolerance_m)
        return {"passed": score is not None, "score": score, "diagnostics": diagnostics,
                "metrics": measurements, "baseline": baseline,
                "control_capture": control_dir.relative_to(directory.parent.parent).as_posix() + "/capture", "qa": qa}


def _artifact_hashes(directory: Path) -> dict[str, str]:
    return {file.relative_to(directory).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in sorted(directory.rglob("*")) if file.is_file() and file.name != "result.json"}


def _save_cache(path: Path, result: dict, fingerprint: str) -> None:
    _write(path, {**result, "evaluation_fingerprint": fingerprint, "artifacts": _artifact_hashes(path.parent)})


def _cache_valid(path: Path, fingerprint: str) -> bool:
    if not path.is_file():
        return False
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("evaluation_fingerprint") != fingerprint or result.get("artifacts") != _artifact_hashes(path.parent):
        return False
    if "control_capture" in result:
        control = path.parent.parent.parent / result["control_capture"]
        return _cache_valid(control.parent / "result.json", fingerprint)
    return True


def run_search(settings: SearchSettings, *, evaluator: Callable | None = None, root: Path | None = None) -> dict:
    settings.validate()
    fingerprint = evaluation_fingerprint(settings)
    run_sha256 = _hash({"settings": asdict(settings), "evaluation": fingerprint})
    run_id = run_sha256[:16]
    directory = (root or paths().work / "search") / run_id
    directory.mkdir(parents=True, exist_ok=True)
    settings_path = directory / "settings.json"
    if settings_path.is_file() and json.loads(settings_path.read_text(encoding="utf-8")).get("run_sha256") != run_sha256:
        raise SearchError("CC_SEARCH_HASH_COLLISION: compact run ID has a different full identity")
    _write(settings_path, {"settings": asdict(settings), "evaluation_fingerprint": fingerprint, "run_sha256": run_sha256})
    evaluator = evaluator or UnityEvaluator(settings, fingerprint)
    rng = SplitMix64(settings.seed * 7919 + 41)
    archive: dict[tuple, dict] = {}
    diagnostics = []
    prefixes: dict[str, str] = {}
    proposals = initial_samples(settings)
    for ordinal in range(settings.initial + settings.mutations):
        try:
            if ordinal < settings.initial:
                lineage, seed, params = proposals[ordinal]
            else:
                if not archive:
                    break
                elite = archive[sorted(archive)[rng.below(len(archive))]]
                lineage, seed = elite["lineage"], elite["seed"]
                params = mutate(lineage, MorphologyParams(**elite["parameters"]), rng)
            digest, identity = morphology_identity(lineage, seed, params)
            prefix = digest[:12]
            if prefix in prefixes and prefixes[prefix] != digest:
                raise SearchError("CC_SEARCH_HASH_COLLISION: morphology ID prefix collision")
            prefixes[prefix] = digest
            candidate_dir = _short_directory(directory, "c", digest)
            _write(candidate_dir / "parameters.json", {"lineage": lineage, "seed": seed, "parameters": asdict(params), "identity": identity})
            source = build_morphology(lineage, seed, params)
            cell = descriptor(source)
            _write(candidate_dir / "skeleton.json", source)
            result_path = candidate_dir / "result.json"
            if _cache_valid(result_path, fingerprint):
                result = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                catalog, problems = screen(source, candidate_dir / "source")
                result = ({"passed": False, "diagnostics": problems} if problems or catalog is None
                          else evaluator(source, catalog, candidate_dir))
                _save_cache(result_path, result, fingerprint)
            if not result.get("passed"):
                diagnostics.append({"proposal": ordinal, "candidate": digest, "diagnostics": result.get("diagnostics", [])})
                continue
            score = result.get("score")
            if not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 < score <= 1:
                raise SearchError("CC_SEARCH_MEASUREMENT_INCOMPLETE: invalid functional score")
            row = {"candidate": digest, "lineage": lineage, "seed": seed, "parameters": asdict(params),
                   "cell": list(cell), "score": score, "metrics": result["metrics"]}
            previous = archive.get(cell)
            if previous is None or score > previous["score"] or (score == previous["score"] and digest < previous["candidate"]):
                archive[cell] = row
        except (MorphologyError, SearchError, ValueError, OSError, subprocess.CalledProcessError, click.ClickException) as exc:
            if isinstance(exc, SearchError) and "HASH_COLLISION" in str(exc):
                raise
            diagnostics.append({"proposal": ordinal, "diagnostics": [str(exc)]})
    elites = [archive[cell] for cell in sorted(archive)]
    result = {"run_id": run_id, "run_sha256": run_sha256, "evaluation_fingerprint": fingerprint, "settings": asdict(settings),
              "elites": elites, "diagnostics": diagnostics,
              "code": "OK" if elites else ("CC_SEARCH_MEASUREMENT_INCOMPLETE" if any("MEASUREMENT_INCOMPLETE" in str(row) for row in diagnostics)
                                              else "CC_SEARCH_NO_FEASIBLE")}
    _write(directory / "archive.json", result)
    _comparison_sheets(directory, elites)
    return result


def _comparison_sheets(directory: Path, elites: list[dict]) -> None:
    from PIL import Image, ImageDraw
    families: dict[str, list[dict]] = {}
    for elite in elites:
        source = json.loads((_short_directory(directory, "c", elite["candidate"]) / "skeleton.json").read_text(encoding="utf-8"))
        families.setdefault(source["family"], []).append(elite)
    for family, rows in sorted(families.items()):
        sheet = Image.new("RGB", (960, 550 * len(rows)), "#242428")
        draw = ImageDraw.Draw(sheet)
        for index, elite in enumerate(rows):
            candidate = _short_directory(directory, "c", elite["candidate"])
            result = json.loads((candidate / "result.json").read_text(encoding="utf-8"))
            for panel, capture in enumerate((candidate / "capture", directory / result["control_capture"])):
                for column, label in enumerate(("walk", "run", "max")):
                    files = list((capture / "turns").glob(f"*_{label}_turns/frame_0060.png"))
                    if len(files) != 1:
                        raise SearchError("CC_SEARCH_MEASUREMENT_INCOMPLETE: paired review frame missing")
                    with Image.open(files[0]) as image:
                        image.thumbnail((320, 240))
                        sheet.paste(image, (column * 320, index * 550 + 55 + panel * 240))
            draw.text((8, index * 550 + 5), f"{elite['lineage']}  {elite['candidate'][:12]}  score={elite['score']:.6f}", fill="white")
            draw.text((8, index * 550 + 23), f"cell={elite['cell']} walk/run/max; candidate above, curated control below", fill="white")
        destination = directory / "comparisons" / f"{family}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(destination)


def selected_source(run_id: str, candidate: str) -> dict:
    if len(run_id) != 16 or any(char not in "0123456789abcdef" for char in run_id):
        raise SearchError("CC_SEARCH_RUN: invalid run ID")
    directory = paths().work / "search" / run_id
    archive = json.loads((directory / "archive.json").read_text(encoding="utf-8"))
    if not any(elite["candidate"] == candidate for elite in archive["elites"]):
        raise SearchError("CC_SEARCH_CANDIDATE: select an evaluated elite full hash")
    candidate_directory = _short_directory(directory, "c", candidate)
    if not _cache_valid(candidate_directory / "result.json", archive["evaluation_fingerprint"]):
        raise SearchError("CC_SEARCH_STALE: evaluation artifacts changed")
    settings_doc = archive["settings"]
    settings = SearchSettings(**{**settings_doc, "lineages": tuple(settings_doc["lineages"])})
    expected_run = _hash({"settings": asdict(settings), "evaluation": archive["evaluation_fingerprint"]})
    if archive.get("run_sha256") != expected_run or expected_run[:16] != run_id:
        raise SearchError("CC_SEARCH_HASH_COLLISION: run archive does not match its full identity")
    if evaluation_fingerprint(settings) != archive["evaluation_fingerprint"]:
        raise SearchError("CC_SEARCH_STALE: evaluator/source settings changed; rerun search")
    source = json.loads((candidate_directory / "skeleton.json").read_text(encoding="utf-8"))
    record = json.loads((candidate_directory / "parameters.json").read_text(encoding="utf-8"))
    expected = build_morphology(record["lineage"], record["seed"], MorphologyParams(**record["parameters"]))
    if source != expected or source["provenance"]["morphology_sha256"] != candidate:
        raise SearchError("CC_SEARCH_STALE: candidate no longer matches parameter/source identity")
    source["status"] = "draft"
    source["provenance"]["search_run"] = run_id
    return source
