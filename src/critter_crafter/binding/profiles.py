"""Immutable versioned binding-profile identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

CONNECTOR_INTERFACE_ID = "skinned_parent_child"
CONNECTOR_INTERFACE_VERSION = "1.0.0"


class BindingProfileError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def canonical_profile_hash(profile: Mapping[str, Any]) -> str:
    """Return the stable identity hash, excluding an already compiled hash field."""
    body = {key: value for key, value in profile.items() if key != "binding_profile_hash"}
    encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def profile_identity(profile: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(profile["binding_profile_id"]),
        str(profile["binding_profile_version"]),
        str(profile.get("binding_profile_hash") or canonical_profile_hash(profile)),
    )


def index_profiles(
    profiles: Iterable[Mapping[str, Any]],
    locked_hashes: Mapping[tuple[str, str], str] | None = None,
    released_hashes: Mapping[tuple[str, str], str] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for source in profiles:
        profile = dict(source)
        key = (str(profile["binding_profile_id"]), str(profile["binding_profile_version"]))
        if key in result:
            raise BindingProfileError("CC_DUP_BINDING_PROFILE", f"{key[0]}@{key[1]}")
        actual_hash = canonical_profile_hash(profile)
        if locked_hashes is not None and locked_hashes.get(key) != actual_hash:
            raise BindingProfileError("CC_BINDING_PROFILE_IMMUTABLE", f"{key[0]}@{key[1]}")
        if released_hashes is not None and key in released_hashes and released_hashes[key] != actual_hash:
            raise BindingProfileError("CC_BINDING_PROFILE_IMMUTABLE", f"{key[0]}@{key[1]}")
        supplied_hash = profile.get("binding_profile_hash")
        if supplied_hash and supplied_hash != actual_hash:
            raise BindingProfileError("CC_BINDING_PROFILE_HASH", f"{key[0]}@{key[1]}")
        profile["binding_profile_hash"] = actual_hash
        result[key] = profile
    if locked_hashes is not None and set(locked_hashes) != set(result):
        missing = sorted(set(locked_hashes) ^ set(result))
        identity = missing[0] if missing else ("<unknown>", "<unknown>")
        raise BindingProfileError("CC_BINDING_PROFILE_REGISTRY", f"{identity[0]}@{identity[1]}")
    if released_hashes is not None:
        missing_released = set(released_hashes) - set(result)
        if missing_released:
            profile_id, version = sorted(missing_released)[0]
            raise BindingProfileError("CC_BINDING_PROFILE_BASELINE", f"{profile_id}@{version}")
        for profile_id, version in result:
            if (profile_id, version) in released_hashes:
                continue
            older = [item_version for item_id, item_version in released_hashes if item_id == profile_id]
            if older and _semver(version) <= max(_semver(item) for item in older):
                raise BindingProfileError("CC_BINDING_PROFILE_VERSION", f"{profile_id}@{version}")
    return result


def _semver(version: str) -> tuple[int, int, int]:
    try:
        values = tuple(int(value) for value in version.split("."))
    except ValueError as exc:
        raise BindingProfileError("CC_BINDING_PROFILE_VERSION", version) from exc
    if len(values) != 3:
        raise BindingProfileError("CC_BINDING_PROFILE_VERSION", version)
    return values


def resolve_profile(
    index: Mapping[tuple[str, str], dict[str, Any]], profile_id: str, version: str
) -> dict[str, Any]:
    try:
        return index[(profile_id, version)]
    except KeyError as exc:
        raise BindingProfileError("CC_UNKNOWN_BINDING_PROFILE", f"{profile_id}@{version}") from exc
