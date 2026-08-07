#!/usr/bin/env python3
"""Generate and verify release evidence for PostgreSQL bundle wheels."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

SCHEMA_VERSION = 1
WHEEL_HASH_MANIFEST = "wheel-sha256s.txt"
SOURCE_LOCK_MANIFEST = "source-locks.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _required_text(record: dict[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} is missing non-empty {key}")
    return value


def _source_identity(record: dict[str, Any], context: str) -> dict[str, Any]:
    source_ref = _required_text(record, "source_ref", context)
    source_commit = record.get("source_commit")
    source_sha256 = record.get("source_sha256")
    if not source_commit and not source_sha256:
        raise ValueError(f"{context} has no immutable source commit or SHA-256")
    return {
        "version": record.get("version"),
        "source_ref": source_ref,
        "source_commit": source_commit,
        "source_sha256": source_sha256,
        "source_submodules": record.get("source_submodules", {}),
    }


def build_source_lock_manifest(metadata: dict[str, Any], metadata_sha256: str) -> dict[str, Any]:
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported bundle metadata schema: {metadata.get('schema_version')!r}")

    postgres = metadata.get("postgres")
    extensions = metadata.get("extensions")
    tigerfs = metadata.get("tigerfs")
    build = metadata.get("build")
    if not all(isinstance(item, dict) for item in (postgres, extensions, tigerfs, build)):
        raise ValueError("bundle metadata is missing postgres/extensions/tigerfs/build records")

    postgres_lock = {
        "version": _required_text(postgres, "version", "postgres"),
        "source_ref": _required_text(postgres, "source_ref", "postgres"),
        "source_commit": _required_text(postgres, "source_commit", "postgres"),
    }
    extension_locks = {
        name: _source_identity(record, f"extension {name}")
        for name, record in sorted(extensions.items())
        if record.get("requested")
    }

    tigerfs_lock: dict[str, Any] | None = None
    if tigerfs.get("requested"):
        tigerfs_lock = {
            "version": _required_text(tigerfs, "version", "tigerfs"),
            "source_sha256": _required_text(tigerfs, "sha256", "tigerfs"),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_recipe": _required_text(metadata, "bundle_recipe", "bundle metadata"),
        "bundle_metadata_sha256": metadata_sha256,
        "postgres": postgres_lock,
        "extensions": extension_locks,
        "tigerfs": tigerfs_lock,
        "toolchain": {
            "rust_toolchain": _required_text(build, "rust_toolchain", "build"),
            "cargo_pgrx_version": _required_text(build, "cargo_pgrx_version", "build"),
        },
    }


def generate(metadata_path: Path, wheel_dir: Path, output_dir: Path) -> None:
    wheels = sorted(wheel_dir.glob("*.whl"), key=lambda path: path.name)
    if not wheels:
        raise FileNotFoundError(f"no wheels found in {wheel_dir}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata_sha256 = _sha256(metadata_path)
    source_locks = build_source_lock_manifest(metadata, metadata_sha256)

    wheel_hashes = "".join(f"{_sha256(wheel)}  {wheel.name}\n" for wheel in wheels)
    _atomic_write(output_dir / WHEEL_HASH_MANIFEST, wheel_hashes)
    _atomic_write(
        output_dir / SOURCE_LOCK_MANIFEST,
        json.dumps(source_locks, indent=2, sort_keys=True) + "\n",
    )


def _read_hash_manifests(paths: Iterable[Path]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            digest, separator, filename = line.partition("  ")
            if not separator or len(digest) != 64 or not filename:
                raise ValueError(f"invalid wheel hash entry at {path}:{line_number}")
            previous = expected.setdefault(filename, digest)
            if previous != digest:
                raise ValueError(f"conflicting hashes recorded for {filename}")
    return expected


def verify(wheel_dir: Path, evidence_dir: Path) -> None:
    manifests = sorted(evidence_dir.rglob(WHEEL_HASH_MANIFEST))
    if not manifests:
        raise FileNotFoundError(f"no {WHEEL_HASH_MANIFEST} files found in {evidence_dir}")
    expected = _read_hash_manifests(manifests)
    actual_paths = {path.name: path for path in wheel_dir.glob("*.whl")}
    if set(actual_paths) != set(expected):
        missing = sorted(set(expected) - set(actual_paths))
        unexpected = sorted(set(actual_paths) - set(expected))
        raise ValueError(f"wheel evidence mismatch: missing={missing}, unexpected={unexpected}")
    for filename, digest in expected.items():
        actual = _sha256(actual_paths[filename])
        if actual != digest:
            raise ValueError(f"wheel SHA-256 mismatch for {filename}: {actual} != {digest}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--metadata", type=Path, required=True)
    generate_parser.add_argument("--wheel-dir", type=Path, required=True)
    generate_parser.add_argument("--output-dir", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--wheel-dir", type=Path, required=True)
    verify_parser.add_argument("--evidence-dir", type=Path, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "generate":
        generate(args.metadata, args.wheel_dir, args.output_dir)
    else:
        verify(args.wheel_dir, args.evidence_dir)


if __name__ == "__main__":
    main()
