"""Binding profile contracts shared by catalog compilation and recipe generation."""

from .profiles import (
    CONNECTOR_INTERFACE_ID,
    CONNECTOR_INTERFACE_VERSION,
    BindingProfileError,
    canonical_profile_hash,
    index_profiles,
    profile_identity,
    resolve_profile,
)

__all__ = [
    "CONNECTOR_INTERFACE_ID",
    "CONNECTOR_INTERFACE_VERSION",
    "BindingProfileError",
    "canonical_profile_hash",
    "index_profiles",
    "profile_identity",
    "resolve_profile",
]
