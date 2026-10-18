"""Canonical manifest bytes for the attestation subject."""

from pathlib    import Path
from hg.lint    import challenge_root, load_manifest
import hashlib
import json


def canonical_manifest(data: dict) -> bytes:
    """UTF-8 JSON with sorted keys and compact separators."""
    return json.dumps(
        data,
        sort_keys    = True,
        separators   = (",", ":"),
        ensure_ascii = False,
    ).encode("utf-8")


def manifest_digest(data: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_manifest(data)).hexdigest()


def digest_challenge(path: Path | None) -> str:
    root = challenge_root(path)
    return manifest_digest(load_manifest(root))
