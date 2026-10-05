from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.release_evidence import generate, verify

from tests.test_bundle_metadata import (
    EXPECTED_FIREBIRD_SOURCE_SUBMODULES,
    _require_firebird_pins,
)


def _metadata() -> dict:
    return {
        "schema_version": 1,
        "bundle_recipe": "pgembed-postgresql-18-bundle-v2",
        "postgres": {
            "version": "18.4",
            "source_ref": "REL_18_4",
            "source_commit": "f5cc81719e6da4cbdb1f797c48b693e91018153a",
        },
        "build": {
            "rust_toolchain": "1.95.0",
            "cargo_pgrx_version": "0.17.0",
        },
        "extensions": {
            "pgvector": {
                "requested": True,
                "version": "0.8.2",
                "source_ref": "v0.8.2",
                "source_commit": None,
                "source_sha256": "a" * 64,
                "source_submodules": {},
            },
            "firebird_fdw": {
                "requested": True,
                "version": "1.4.2",
                "source_ref": "1.4.2",
                "source_commit": None,
                "source_sha256": "0e76750b4b6ef1ebc125d0ed3d2b204e6e545bc7c1b1a8c6b3f712497138518b",
                "source_submodules": EXPECTED_FIREBIRD_SOURCE_SUBMODULES,
            },
            "stannum": {
                "requested": True,
                "version": "0.5.0",
                "source_ref": "3227d7afecd2b66e669c083266043c7cb6f9ada5",
                "source_commit": "3227d7afecd2b66e669c083266043c7cb6f9ada5",
                "source_sha256": None,
                "source_submodules": {},
            },
            "not-selected": {
                "requested": False,
                "version": None,
                "source_ref": "ignored",
                "source_commit": None,
                "source_sha256": None,
            },
        },
        "tigerfs": {
            "requested": True,
            "version": "v0.7.0",
            "sha256": "b" * 64,
        },
    }


def test_generate_and_verify_release_evidence(tmp_path: Path) -> None:
    _require_firebird_pins()
    metadata_path = tmp_path / "build-metadata.json"
    metadata_path.write_text(json.dumps(_metadata()), encoding="utf-8")
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    wheels = {
        "pgembed-0.3.0rc2-cp312-test.whl": b"cp312 wheel",
        "pgembed-0.3.0rc2-cp313-test.whl": b"cp313 wheel",
    }
    for name, content in wheels.items():
        (wheel_dir / name).write_bytes(content)

    evidence_dir = tmp_path / "evidence" / "linux-x86_64"
    generate(metadata_path, wheel_dir, evidence_dir)

    hash_lines = (evidence_dir / "wheel-sha256s.txt").read_text().splitlines()
    assert hash_lines == [
        f"{hashlib.sha256(wheels[name]).hexdigest()}  {name}" for name in sorted(wheels)
    ]
    source_locks = json.loads((evidence_dir / "source-locks.json").read_text())
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert source_locks["postgres"]["version"] == "18.4"
    expected_extensions = {
        name: {
            "source_commit": record.get("source_commit"),
            "source_ref": record["source_ref"],
            "source_sha256": record.get("source_sha256"),
            "source_submodules": record.get("source_submodules", {}),
            "version": record.get("version"),
        }
        for name, record in metadata["extensions"].items()
        if record.get("requested")
    }
    assert source_locks["extensions"] == expected_extensions
    assert source_locks["extensions"]["firebird_fdw"]["source_submodules"] == (
        metadata["extensions"]["firebird_fdw"]["source_submodules"]
    )
    assert source_locks["extensions"]["firebird_fdw"]["source_submodules"] == (
        EXPECTED_FIREBIRD_SOURCE_SUBMODULES
    )
    assert source_locks["tigerfs"]["source_sha256"] == "b" * 64
    verify(wheel_dir, tmp_path / "evidence")


def test_verify_rejects_tampered_or_unrecorded_wheel(tmp_path: Path) -> None:
    metadata_path = tmp_path / "build-metadata.json"
    metadata_path.write_text(json.dumps(_metadata()), encoding="utf-8")
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    wheel = wheel_dir / "pgembed.whl"
    wheel.write_bytes(b"original")
    evidence_dir = tmp_path / "evidence"
    generate(metadata_path, wheel_dir, evidence_dir)

    wheel.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify(wheel_dir, evidence_dir)

    wheel.write_bytes(b"original")
    (wheel_dir / "unrecorded.whl").write_bytes(b"extra")
    with pytest.raises(ValueError, match="unexpected=.*unrecorded.whl"):
        verify(wheel_dir, evidence_dir)


def test_generate_rejects_requested_extension_without_immutable_identity(
    tmp_path: Path,
) -> None:
    metadata = _metadata()
    metadata["extensions"]["pgvector"]["source_sha256"] = None
    metadata_path = tmp_path / "build-metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    (wheel_dir / "pgembed.whl").write_bytes(b"wheel")

    with pytest.raises(ValueError, match="no immutable source commit or SHA-256"):
        generate(metadata_path, wheel_dir, tmp_path / "evidence")
