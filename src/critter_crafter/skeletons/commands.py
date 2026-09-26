"""Curated anatomy, evaluated motion QA, and fresh review gates."""
from __future__ import annotations
import json
from pathlib import Path
import click
from ..config import paths
from .archetypes import ARCHETYPES, FAMILIES, PRESETS, build_candidate, generate_extras
from .review import ReviewError, make_receipt, verify_qa


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")


def _skeleton_files(family: str | None = None) -> list[Path]:
    files = sorted((paths().data / "skeletons").glob("*.skeleton.json"))
    return [f for f in files if (d := json.loads(f.read_text(encoding="utf-8"))).get("schema_version") == "3.0.0"
            and (family is None or d["family"] == family)]


def _selection(cat: dict, families: tuple[str, ...], ids: tuple[str, ...] = ()) -> list[dict]:
    unknown = set(ids) - {s["skeleton_id"] for s in cat["skeletons"]}
    if unknown:
        raise click.ClickException(f"unknown skeletons: {', '.join(sorted(unknown))}")
    selected = [s for s in cat["skeletons"] if (not families or s["family"] in families)
                and (not ids or s["skeleton_id"] in ids)]
    if not selected:
        raise click.ClickException("no skeletons match the selection")
    return selected


@click.group()
def skeleton() -> None:
    """Curated v3 skeletons: vary, QA, review, and human approval."""


@skeleton.command("vary")
@click.option("--family", "families", multiple=True, type=click.Choice(FAMILIES))
@click.option("--archetype", "archetypes", multiple=True, type=click.Choice(sorted(ARCHETYPES)))
@click.option("--preset", "presets", multiple=True, type=click.Choice(PRESETS))
@click.option("--style", type=click.Choice(["anatomical", "horror"]), default="anatomical", show_default=True)
@click.option("--seed", type=int, default=1, show_default=True)
@click.option("--count", type=click.IntRange(1, 6), default=None, help="Maximum selected candidates per family")
@click.option("--force", is_flag=True, help="Regenerate reviewed candidates as drafts")
@click.option("--extras/--no-extras", default=False, help="Write the 41 extras drafts instead of the original 39")
def skeleton_vary(families, archetypes, presets, style, seed, count, force, extras) -> None:
    """Write 13 curated archetypes x 3 presets by default; keep legacy IDs frozen."""
    written = kept = 0
    if extras:
        if style != "anatomical":
            raise click.ClickException("horror+extras is out of scope")
        docs = generate_extras(seed=seed)
        if families:
            docs = [doc for doc in docs if doc["family"] in families]
        if archetypes:
            docs = [doc for doc in docs if doc["anatomy"]["archetype_id"] in archetypes]
        if presets:
            docs = [doc for doc in docs if doc["provenance"]["preset"] in presets]
        for doc in docs:
            path = paths().data / "skeletons" / f"{doc['skeleton_id']}.skeleton.json"
            if path.exists() and not force and json.loads(path.read_text(encoding="utf-8")).get("status") != "draft":
                kept += 1
                continue
            _write_json(path, doc)
            written += 1
        click.echo(f"wrote {written} drafts; preserved {kept} reviewed candidates")
        return
    chosen = [a for a in (archetypes or sorted(ARCHETYPES)) if not families or ARCHETYPES[a]["family"] in families]
    per_family: dict[str, int] = {}
    for archetype in chosen:
        family = ARCHETYPES[archetype]["family"]
        for preset in presets or PRESETS:
            if count is not None and per_family.get(family, 0) >= count:
                continue
            per_family[family] = per_family.get(family, 0) + 1
            doc = build_candidate(archetype, preset, seed=seed, style=style)
            path = paths().data / "skeletons" / f"{doc['skeleton_id']}.skeleton.json"
            if path.exists() and not force and json.loads(path.read_text(encoding="utf-8")).get("status") != "draft":
                kept += 1
                continue
            _write_json(path, doc)
            written += 1
    click.echo(f"wrote {written} drafts; preserved {kept} reviewed candidates")


@skeleton.command("status")
def skeleton_status() -> None:
    """Count current v3 draft/approved/rejected candidates."""
    table: dict[str, dict[str, int]] = {}
    for file in _skeleton_files():
        doc = json.loads(file.read_text(encoding="utf-8"))
        row = table.setdefault(doc["family"], {})
        row[doc["status"]] = row.get(doc["status"], 0) + 1
    for family, row in sorted(table.items()):
        click.echo(f"{family:11s} " + ", ".join(f"{status}={n}" for status, n in sorted(row.items())))


def _qa(cat: dict, source: Path, selected: list[dict]) -> list[dict]:
    from ..library.commands import skeleton_content_hash, skeleton_source_hash, skeleton_profiles
    from ..library.export_validation import validate_glb_rest, validate_glb_motion, validate_glb_surface
    from .qa import evaluate_motion
    from .action_qa import evaluate_actions
    reports = []
    for skel in selected:
        try:
            fingerprint = skeleton_content_hash(cat, skel, source)
            motion = json.loads((source / skel["asset"]["motion"]).read_text(encoding="utf-8"))
            report = evaluate_motion(skel, motion, profiles=cat.get("binding_profiles", []))
            report["content_fingerprint"] = fingerprint
            report["attack"] = evaluate_actions(skel, motion)
            report["diagnostics"].extend(report["attack"]["diagnostics"])
            # Runtime locomotion: speed bands, support and step rate at the published speeds.
            from ..locomotion.qa import evaluate_locomotion
            report["locomotion"] = evaluate_locomotion(skel)
            report["diagnostics"].extend(report["locomotion"])
            expected_profiles = [{k: p[k] for k in ("binding_profile_id", "binding_profile_version", "binding_profile_hash")}
                                 for p in skeleton_profiles(cat, skel)]
            key = lambda p: (p["binding_profile_id"], p["binding_profile_version"])
            snapshot_keys = ("schema_version", "skeleton_id", "family", "locomotion_hint", "bones", "branches", "neutral_pose", "anatomy")
            expected_snapshot = {k: skel[k] for k in snapshot_keys if k in skel}
            if (motion.get("source_fingerprint") != skeleton_source_hash(cat, skel)
                    or sorted(motion.get("binding_profiles", []), key=key) != sorted(expected_profiles, key=key)
                    or motion.get("skeleton_snapshot") != expected_snapshot):
                report["diagnostics"].append({"code": "CC_MOTION_PROVENANCE", "detail": "bake does not match current source/profile identity"})
            rest = validate_glb_rest(source / skel["asset"]["glb"], skel)
            exported_motion = validate_glb_motion(source / skel["asset"]["glb"], skel, motion)
            surface = validate_glb_surface(source / skel["asset"]["glb"], skel, motion)
            report["exports"] = {"glb_rest": rest, "glb_motion": exported_motion, "glb_surface": surface}
            for result in (rest, exported_motion, surface):
                report["diagnostics"].extend({"code": "CC_GLB_EXPORT", "detail": str(d)} for d in result["diagnostics"])
            if not skel["asset"].get("assembled_glb") or not (source / skel["asset"]["assembled_glb"]).is_file():
                report["diagnostics"].append({"code": "CC_REVIEW_ASSEMBLY_MISSING", "detail": "build compatible assembled parts before approval"})
            else:
                assembled_path = source / skel["asset"]["assembled_glb"]
                assembled_rest = validate_glb_rest(assembled_path, skel)
                assembled_motion = validate_glb_motion(assembled_path, skel, motion)
                assembled_surface = validate_glb_surface(assembled_path, skel, motion)
                report["exports"].update(assembled_rest=assembled_rest, assembled_motion=assembled_motion,
                                         assembled_surface=assembled_surface)
                for result in (assembled_rest, assembled_motion, assembled_surface):
                    report["diagnostics"].extend({"code": "CC_ASSEMBLED_EXPORT", "detail": str(d)} for d in result["diagnostics"])
            report["passed"] = not report["diagnostics"]
        except (ReviewError, KeyError, ValueError, OSError) as exc:
            report = {"skeleton_id": skel["skeleton_id"], "passed": False,
                      "diagnostics": [{"code": "CC_QA_INPUT", "detail": str(exc)}]}
        reports.append(report)
    return reports


def _print_reports(reports: list[dict]) -> None:
    for report in reports:
        status = "PASS" if report["passed"] else "FAIL"
        click.echo(f"{status} {report['skeleton_id']}: {report.get('clips_checked', 0)} clips, {len(report['diagnostics'])} diagnostics")
        seen = set()
        for diagnostic in report["diagnostics"]:
            if diagnostic["code"] not in seen:
                click.echo(f"  {diagnostic['code']}: {diagnostic['detail']}")
                seen.add(diagnostic["code"])


@skeleton.command("qa")
@click.option("--family", "families", multiple=True, type=click.Choice(FAMILIES))
@click.option("--json-out", type=click.Path(path_type=Path), default=None)
@click.argument("skeleton_ids", nargs=-1)
def skeleton_qa(families, json_out, skeleton_ids) -> None:
    """Validate every frame of built clips; save readable and JSON diagnostics."""
    from ..library.commands import _built_catalog
    cat, source = _built_catalog()
    reports = _qa(cat, source, _selection(cat, families, skeleton_ids))
    out = json_out or paths().work / "review" / "foundation-v3" / "qa.json"
    _write_json(out, _merge_qa_reports(out, reports))
    _print_reports(reports)
    click.echo(f"report: {out}")
    if not all(r["passed"] for r in reports):
        raise click.ClickException("built motion failed QA; see the report for complete diagnostics")


@skeleton.command("review")
@click.option("--family", "families", multiple=True, type=click.Choice(FAMILIES))
@click.option("--out", type=click.Path(path_type=Path), default=None)
@click.argument("skeleton_ids", nargs=-1)
def skeleton_review(families, out, skeleton_ids) -> None:
    """Bundle built clips in three modes and four views, with fresh QA receipts."""
    from ..library.commands import _built_catalog
    from .bundle import write_bundle
    cat, source = _built_catalog()
    selected = _selection(cat, families, skeleton_ids)
    directory = out or paths().work / "review" / "foundation-v3"
    reports = _qa(cat, source, selected)
    write_bundle(cat, source, directory, [s["skeleton_id"] for s in selected])
    _write_json(directory / "qa.json", _merge_qa_reports(directory / "qa.json", reports))
    shared = [p for p in directory.rglob("*") if p.is_file() and not ({"skeletons", "assets"} & set(p.relative_to(directory).parts))
              and p.name != "qa.json"]
    for report in reports:
        sid = report["skeleton_id"]
        if report.get("content_fingerprint"):
            own = [p for p in (directory / "skeletons" / sid).rglob("*") if p.is_file()]
            own += [p for p in (directory / "assets" / sid).rglob("*") if p.is_file()]
            skel = next(s for s in selected if s["skeleton_id"] == sid)
            modes = ["bones", "mannequin"]
            if skel["asset"].get("assembled_glb") and (source / skel["asset"]["assembled_glb"]).is_file():
                modes.append("assembled")
            receipt = make_receipt(sid, report["content_fingerprint"], report, shared + own,
                                   modes=modes, views=["front", "side", "top", "three_quarter"])
            _write_json(paths().work / "review" / "receipts" / f"{sid}.json", receipt)
    _print_reports(reports)
    click.echo(f"review bundle: {directory / 'index.html'}")
    click.echo(f"serve with: python -m http.server 8765 --directory \"{directory}\"")


def _merge_qa_reports(path: Path, reports: list[dict]) -> dict:
    updated = {r["skeleton_id"]: r for r in reports}
    merged: list[dict] = []
    seen: set[str] = set()
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            doc = {}
        if isinstance(doc, dict) and isinstance(doc.get("results"), list):
            for row in doc["results"]:
                if not isinstance(row, dict):
                    continue
                sid = row.get("skeleton_id")
                if not isinstance(sid, str) or sid in seen:
                    continue
                merged.append(updated.get(sid, row))
                seen.add(sid)
    for report in reports:
        sid = report["skeleton_id"]
        if sid not in seen:
            merged.append(report)
            seen.add(sid)
    return {"schema_version": "3.0.0", "passed": all(r.get("passed") for r in merged), "results": merged}


def _qa_result(skeleton_id: str) -> dict:
    path = paths().work / "review" / "foundation-v3" / "qa.json"
    if not path.is_file():
        raise ReviewError(f"CC_REVIEW_QA: {skeleton_id} has no current bound QA result")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"CC_REVIEW_QA: {skeleton_id} has no current bound QA result") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        raise ReviewError(f"CC_REVIEW_QA: {skeleton_id} has no current bound QA result")
    for result in doc["results"]:
        if isinstance(result, dict) and result.get("skeleton_id") == skeleton_id:
            return result
    raise ReviewError(f"CC_REVIEW_QA: {skeleton_id} is not in the current QA report")


def _set_status(family: str | None, ids: tuple[str, ...], status: str) -> int:
    from ..library.commands import _built_catalog, skeleton_content_hash
    cat, source = _built_catalog()
    selected = _selection(cat, (family,) if family else (), ids)
    # Validate the entire request before changing any status.
    for skel in selected:
        sid = skel["skeleton_id"]
        try:
            verify_qa(_qa_result(sid), sid, skeleton_content_hash(cat, skel, source),
                      require_passing=status == "approved")
        except ReviewError as exc:
            raise click.ClickException(str(exc)) from exc
    for skel in selected:
        path = paths().data / "skeletons" / f"{skel['skeleton_id']}.skeleton.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["status"] = status
        _write_json(path, doc)
    return len(selected)


@skeleton.command("approve")
@click.option("--family", type=click.Choice(FAMILIES), default=None)
@click.argument("skeleton_ids", nargs=-1)
def skeleton_approve(family, skeleton_ids) -> None:
    """Approve from current passing skeleton QA bound to content_fingerprint."""
    if not family and not skeleton_ids:
        raise click.ClickException("give --family or skeleton IDs")
    click.echo(f"approved {_set_status(family, skeleton_ids, 'approved')} skeleton(s)")


@skeleton.command("reject")
@click.option("--family", type=click.Choice(FAMILIES), default=None)
@click.argument("skeleton_ids", nargs=-1)
def skeleton_reject(family, skeleton_ids) -> None:
    """Reject current content when QA is bound to the same content_fingerprint."""
    if not family and not skeleton_ids:
        raise click.ClickException("give --family or skeleton IDs")
    click.echo(f"rejected {_set_status(family, skeleton_ids, 'rejected')} skeleton(s)")
