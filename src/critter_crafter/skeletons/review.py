"""Content-addressed review receipts and non-destructive authored polish overrides."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


class ReviewError(ValueError):
    """A review cannot be trusted or an authored override is incompatible."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def file_hash(path: Path) -> str:
    if not path.is_file():
        raise ReviewError(f"CC_REVIEW_ASSET_MISSING: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(source: dict, profiles: Iterable[dict], settings: dict) -> str:
    # Status records a human decision; changing it does not change the creature.
    anatomy = {k: v for k, v in source.items() if k not in (
        "status", "review", "asset", "source_fingerprint", "content_fingerprint")}
    ordered = sorted(profiles, key=lambda p: (p["binding_profile_id"], p["binding_profile_version"]))
    return hashlib.sha256(_canonical({"source": anatomy, "profiles": ordered, "settings": settings})).hexdigest()


def content_fingerprint(source: dict, profiles: Iterable[dict], settings: dict,
                        artifacts: Iterable[Path]) -> str:
    assets = [{"name": path.name, "sha256": file_hash(path)} for path in sorted(map(Path, artifacts))]
    if not assets:
        raise ReviewError("CC_REVIEW_ASSET_MISSING: no built artifacts")
    return hashlib.sha256(_canonical({"source": source_fingerprint(source, profiles, settings),
                                      "artifacts": assets})).hexdigest()


def make_receipt(skeleton_id: str, fingerprint: str, qa: dict,
                 outputs: Iterable[Path], **metadata: Any) -> dict:
    _verify_qa(qa, skeleton_id, fingerprint)
    files = [{"path": str(Path(path).resolve()), "sha256": file_hash(Path(path))} for path in outputs]
    if not files:
        raise ReviewError(f"CC_REVIEW_ASSET_MISSING: {skeleton_id} has no review output")
    return {"schema_version": "3.0.0", "skeleton_id": skeleton_id,
            "content_fingerprint": fingerprint, "qa": qa, "outputs": files, **metadata}


def _verify_qa(qa: dict, skeleton_id: str, fingerprint: str) -> None:
    from .qa import verification_version
    if (not isinstance(qa.get("passed"), bool) or qa.get("skeleton_id") != skeleton_id
            or qa.get("content_fingerprint") != fingerprint
            or qa.get("qa_version") != verification_version()
            or qa.get("sample_source") != "evaluated_blender"):
        raise ReviewError(f"CC_REVIEW_QA: {skeleton_id} has no current bound QA result")
    if qa["passed"] and (qa.get("clips_checked") != 8
            or any(qa.get(key, 0) <= 0 for key in ("samples_checked", "contacts_checked", "joint_limits_checked"))):
        raise ReviewError(f"CC_REVIEW_QA: {skeleton_id} lacks complete evaluated evidence")


def verify_qa(qa: dict, skeleton_id: str, fingerprint: str, require_passing: bool = True) -> None:
    """Approve from passing skeleton QA bound to content_fingerprint; reject may use a failing result."""
    recorded = qa.get("content_fingerprint") if isinstance(qa, dict) else None
    if recorded and recorded != fingerprint:
        raise ReviewError(f"CC_REVIEW_STALE: {skeleton_id}; rebuild and run skeleton qa again")
    bound = qa if isinstance(qa, dict) else {}
    _verify_qa(bound, skeleton_id, fingerprint)
    if require_passing and bound.get("passed") is not True:
        raise ReviewError(f"CC_REVIEW_QA: {skeleton_id}")


def verify_receipt(receipt: dict, skeleton_id: str, fingerprint: str, require_passing: bool = True) -> None:
    if (receipt.get("schema_version") != "3.0.0" or receipt.get("skeleton_id") != skeleton_id
            or receipt.get("content_fingerprint") != fingerprint):
        raise ReviewError(f"CC_REVIEW_STALE: {skeleton_id}; rebuild, run QA and review again")
    verify_qa(receipt.get("qa", {}), skeleton_id, fingerprint, require_passing=require_passing)
    if require_passing and (set(receipt.get("modes", [])) != {"bones", "mannequin", "assembled"}
            or set(receipt.get("views", [])) != {"front", "side", "top", "three_quarter"}):
        raise ReviewError(f"CC_REVIEW_COVERAGE: {skeleton_id} requires all three modes and four views")
    if not receipt.get("outputs"):
        raise ReviewError(f"CC_REVIEW_ASSET_MISSING: {skeleton_id}")
    for item in receipt["outputs"]:
        if file_hash(Path(item["path"])) != item["sha256"]:
            raise ReviewError(f"CC_REVIEW_OUTPUT_CHANGED: {item['path']}")


def resolve_polish(directory: Path, skeleton_id: str, fingerprint: str) -> Path | None:
    """Resolve an opt-in override without modifying either authored or generated files."""
    manifest = directory / "override.json"
    if not manifest.exists():
        return None
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    if doc.get("skeleton_id") != skeleton_id or doc.get("source_fingerprint") != fingerprint:
        raise ReviewError(f"CC_POLISH_STALE: {skeleton_id}; authored override targets different anatomy")
    path = (directory / doc["blend"]).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ReviewError(f"CC_POLISH_PATH: {skeleton_id}: {path}")
    return path
