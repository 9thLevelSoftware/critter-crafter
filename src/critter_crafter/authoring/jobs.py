"""Portable, model-independent authoring manifests (also imported by Python 3.8 workers)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_artifact(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("CC_AUTHORING_PATH: artifact requires a relative POSIX path")
    path = PurePosixPath(relative)
    if path.is_absolute() or PureWindowsPath(relative).drive or ".." in path.parts:
        raise ValueError("CC_AUTHORING_PATH: artifact escapes root")
    base = Path(root).resolve()
    resolved = (base / relative).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError("CC_AUTHORING_PATH: artifact escapes root") from None
    return resolved


def artifact(root, path):
    base = Path(root).resolve()
    path = Path(path)
    resolved = path.resolve() if path.is_absolute() else resolve_artifact(base, path.as_posix())
    relative = resolved.relative_to(base).as_posix()
    return {"path": relative, "sha256": sha256_file(resolved)}


def _hash(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _verify_artifacts(root, value):
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            path = resolve_artifact(root, value["path"])
            if not path.is_file() or sha256_file(path) != value["sha256"]:
                raise ValueError("CC_AUTHORING_HASH: missing or changed artifact " + value["path"])
        for child in value.values():
            _verify_artifacts(root, child)
    elif isinstance(value, list):
        for child in value:
            _verify_artifacts(root, child)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value) + b"\n")


def write_job(root, kind, inputs, options):
    if kind not in ("skin", "motion"):
        raise ValueError("CC_AUTHORING_KIND: unknown job kind")
    identity = {"kind": kind, "inputs": inputs, "options": options}
    _verify_artifacts(root, inputs)
    job = dict(identity, schema_version="cc-authoring-job-1", job_id=_hash(identity))
    write_json(Path(root) / "job.json", job)
    return job


def load_job(root, kind=None):
    job = json.loads((Path(root) / "job.json").read_text(encoding="utf-8"))
    if job.get("schema_version") != "cc-authoring-job-1" or job.get("kind") not in ("skin", "motion"):
        raise ValueError("CC_AUTHORING_JOB: unsupported job")
    if kind is not None and job["kind"] != kind:
        raise ValueError("CC_AUTHORING_KIND: wrong job kind")
    identity = {key: job[key] for key in ("kind", "inputs", "options")}
    if job.get("job_id") != _hash(identity):
        raise ValueError("CC_AUTHORING_HASH: changed job identity")
    _verify_artifacts(root, job["inputs"])
    return job


def write_result(root, job, producer_revision, model_sha256, settings_sha256, outputs, **metadata):
    reserved = {"schema_version", "job_id", "producer_revision", "model_sha256", "settings_sha256", "outputs"}
    if reserved.intersection(metadata):
        raise ValueError("CC_AUTHORING_RESULT: reserved metadata field")
    result = dict(metadata, schema_version="cc-authoring-result-1", job_id=job["job_id"],
                  producer_revision=producer_revision, model_sha256=model_sha256,
                  settings_sha256=settings_sha256, outputs=outputs)
    _verify_artifacts(root, outputs)
    write_json(Path(root) / "result.json", result)
    return result


def load_result(root, job=None):
    result = json.loads((Path(root) / "result.json").read_text(encoding="utf-8"))
    if result.get("schema_version") != "cc-authoring-result-1" or not result.get("producer_revision"):
        raise ValueError("CC_AUTHORING_RESULT: unsupported result or missing producer")
    for key in ("job_id", "model_sha256", "settings_sha256"):
        if not re.fullmatch("[0-9a-f]{64}", str(result.get(key, ""))):
            raise ValueError("CC_AUTHORING_RESULT: invalid " + key)
    if job is not None and result["job_id"] != job["job_id"]:
        raise ValueError("CC_AUTHORING_HASH: result belongs to another job")
    _verify_artifacts(root, result["outputs"])
    return result
