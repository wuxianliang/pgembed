from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pgembed._bundle_metadata import (
    BUNDLE_METADATA_PATH,
    BundledPostgresMetadataError,
    clear_bundle_metadata_cache,
    load_bundle_metadata,
    validate_bundled_binaries,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "tools" / "generate_bundle_metadata.py"


def _executable(path: Path, output: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
    path.chmod(0o755)


def _prefix(tmp_path: Path, *, major: int = 18) -> Path:
    prefix = tmp_path / "prefix"
    _executable(prefix / "bin" / "postgres", f"postgres (PostgreSQL) {major}.4")
    pg_config = prefix / "bin" / "pg_config"
    pg_config.parent.mkdir(parents=True, exist_ok=True)
    pg_config.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--configure\" ]; then\n"
        "  printf '%s\\n' \"'--without-readline' '--without-icu'\"\n"
        "else\n"
        f"  printf '%s\\n' 'PostgreSQL {major}.4'\n"
        "fi\n"
    )
    pg_config.chmod(0o755)
    extension = prefix / "share" / "postgresql" / "extension"
    extension.mkdir(parents=True)
    (extension / "vector.control").write_text("default_version = '0.8.2'\n")
    (extension / "vector--0.8.2.sql").write_text("-- fixture\n")
    library = prefix / "lib" / "postgresql" / "vector.dylib"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"fixture")
    return prefix


def _generate(
    prefix: Path,
    output: Path,
    *,
    requested: str = "pgvector",
    built: str = "pgvector",
    skipped: str = "",
    tigerfs_sha256: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--install-prefix", str(prefix),
            "--output", str(output),
            "--postgres-version", "18.4",
            "--postgres-ref", "REL_18_4",
            "--postgres-commit", "f5cc81719e6da4cbdb1f797c48b693e91018153a",
            "--configure-flags", "--without-readline --without-icu",
            "--requested", requested,
            "--built", built,
            "--skipped", skipped,
            "--host-os", "Darwin",
            "--arch", "arm64",
            "--libc", "system",
            "--rust-toolchain", "1.95.0",
            "--cargo-pgrx-version", "0.17.0",
            "--tigerfs-sha256", tigerfs_sha256,
        ],
        capture_output=True,
        text=True,
    )


def test_schema_v1_metadata_loads(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    result = _generate(prefix, output)
    assert result.returncode == 0, result.stderr
    metadata = load_bundle_metadata(output)
    assert metadata is not None
    assert metadata.postgres_major == 18
    assert metadata.postgres_version == "18.4"
    assert metadata.extensions["pgvector"].built is True
    assert metadata.extensions["pgvector"].built_for_postgres_major == 18
    assert metadata.extensions["pgvector"].update_sql == ()
    assert BUNDLE_METADATA_PATH.parts[-4:] == (
        "pginstall", "share", "pgembed", "build-metadata.json"
    )


def test_generator_records_base_install_and_update_chain(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    extension_dir = prefix / "share" / "postgresql" / "extension"
    (prefix / "lib" / "postgresql" / "vector.dylib").unlink()
    (extension_dir / "vector.control").unlink()
    (extension_dir / "vector--0.8.2.sql").unlink()

    library = prefix / "lib" / "postgresql" / "timescaledb.dylib"
    library.write_bytes(b"fixture")
    (extension_dir / "timescaledb.control").write_text("default_version = '2.27.1'\n")
    (extension_dir / "timescaledb--2.27.0.sql").write_text("-- base fixture\n")
    (extension_dir / "timescaledb--2.27.0--2.27.1.sql").write_text("-- update fixture\n")

    output = prefix / "bundle-metadata.json"
    result = _generate(
        prefix,
        output,
        requested="timescaledb",
        built="timescaledb",
    )
    assert result.returncode == 0, result.stderr
    metadata = load_bundle_metadata(output)
    assert metadata is not None
    extension = metadata.extensions["timescaledb"]
    assert extension.install_sql == (
        "share/postgresql/extension/timescaledb--2.27.0.sql"
    )
    assert extension.update_sql == (
        "share/postgresql/extension/timescaledb--2.27.0--2.27.1.sql",
    )


def test_generator_accepts_tigerfs_version_output_with_go_version(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    _executable(
        prefix / "bin" / "tigerfs",
        "TigerFS 0.7.0\nBuild time: fixture\nGo version: go1.25.1\nPlatform: darwin/arm64",
    )
    output = prefix / "bundle-metadata.json"
    result = _generate(
        prefix,
        output,
        requested="pgvector tigerfs",
        built="pgvector tigerfs",
        tigerfs_sha256="0" * 64,
    )
    assert result.returncode == 0, result.stderr
    metadata = load_bundle_metadata(output)
    assert metadata is not None
    assert metadata.tigerfs["binary_version"].startswith("TigerFS 0.7.0")
    assert "Go version: go1.25.1" in metadata.tigerfs["binary_version"]


def test_missing_metadata_is_optional(tmp_path: Path) -> None:
    assert load_bundle_metadata(tmp_path / "missing.json") is None


@pytest.mark.parametrize("content", ["{", "[]", '{"schema_version": 99}'])
def test_malformed_or_unsupported_metadata_fails_closed(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bundle-metadata.json"
    path.write_text(content)
    with pytest.raises(BundledPostgresMetadataError):
        load_bundle_metadata(path)


def test_binary_major_mismatch_fails_validation(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    assert _generate(prefix, output).returncode == 0
    metadata = load_bundle_metadata(output)
    assert metadata is not None
    _executable(prefix / "bin" / "postgres", "postgres (PostgreSQL) 17.10")
    with pytest.raises(BundledPostgresMetadataError, match="major 17"):
        validate_bundled_binaries(metadata, bin_path=prefix / "bin")


def test_binary_exact_version_mismatch_fails_validation(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    assert _generate(prefix, output).returncode == 0
    metadata = load_bundle_metadata(output)
    assert metadata is not None
    _executable(prefix / "bin" / "postgres", "postgres (PostgreSQL) 18.5")
    with pytest.raises(BundledPostgresMetadataError, match="exact version 18.4"):
        validate_bundled_binaries(metadata, bin_path=prefix / "bin")


def test_extension_major_mismatch_fails_load(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    assert _generate(prefix, output).returncode == 0
    payload = json.loads(output.read_text())
    payload["extensions"]["pgvector"]["built_for_postgres_major"] = 17
    output.write_text(json.dumps(payload))
    with pytest.raises(BundledPostgresMetadataError, match="targets PostgreSQL 17"):
        load_bundle_metadata(output)


def test_built_extension_requires_immutable_source_identity(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    assert _generate(prefix, output).returncode == 0
    payload = json.loads(output.read_text())
    extension = payload["extensions"]["pgvector"]
    extension["source_commit"] = None
    extension["source_sha256"] = None
    output.write_text(json.dumps(payload))

    with pytest.raises(BundledPostgresMetadataError, match="immutable source"):
        load_bundle_metadata(output)


@pytest.mark.parametrize("artifact", ["/tmp/vector.dylib", "../vector.dylib", "lib\\vector.dylib"])
def test_artifact_paths_must_stay_inside_bundle(tmp_path: Path, artifact: str) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    assert _generate(prefix, output).returncode == 0
    payload = json.loads(output.read_text())
    payload["extensions"]["pgvector"]["library"] = artifact
    output.write_text(json.dumps(payload))

    with pytest.raises(BundledPostgresMetadataError, match="normalized relative"):
        load_bundle_metadata(output)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"requested": True}, "built or skipped"),
        ({"sha256": "0" * 64}, "not built"),
        ({"skip_reason": "not actually skipped"}, "not skipped"),
    ],
)
def test_tigerfs_metadata_state_must_be_coherent(
    tmp_path: Path, updates: dict[str, object], message: str
) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    assert _generate(prefix, output).returncode == 0
    payload = json.loads(output.read_text())
    payload["tigerfs"].update(updates)
    output.write_text(json.dumps(payload))

    with pytest.raises(BundledPostgresMetadataError, match=message):
        load_bundle_metadata(output)


def test_binary_validation_cache_includes_metadata_identity(tmp_path: Path) -> None:
    clear_bundle_metadata_cache()
    prefix = _prefix(tmp_path)
    first_output = prefix / "bundle-metadata.json"
    assert _generate(prefix, first_output).returncode == 0
    first = load_bundle_metadata(first_output)
    assert first is not None
    validate_bundled_binaries(first, bin_path=prefix / "bin")

    second_output = prefix / "changed-bundle-metadata.json"
    payload = json.loads(first_output.read_text())
    payload["postgres"]["version"] = "18.5"
    payload["postgres"]["binary_version"] = "postgres (PostgreSQL) 18.5"
    payload["postgres"]["pg_config_version"] = "PostgreSQL 18.5"
    second_output.write_text(json.dumps(payload))
    second = load_bundle_metadata(second_output)
    assert second is not None

    with pytest.raises(BundledPostgresMetadataError, match="exact version 18.5"):
        validate_bundled_binaries(second, bin_path=prefix / "bin")


def test_generator_rejects_skipped_stale_artifacts(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    result = _generate(prefix, output, built="", skipped="pgvector")
    assert result.returncode != 0
    assert not output.exists()
    assert "stale" in result.stderr


def test_generator_rejects_stale_sql_without_library_or_control(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    (prefix / "lib" / "postgresql" / "vector.dylib").unlink()
    (prefix / "share" / "postgresql" / "extension" / "vector.control").unlink()
    output = prefix / "bundle-metadata.json"
    result = _generate(prefix, output, built="", skipped="pgvector")
    assert result.returncode != 0
    assert "stale" in result.stderr
    assert not output.exists()


def test_generator_failure_leaves_no_completion_json(tmp_path: Path) -> None:
    prefix = tmp_path / "missing-prefix"
    output = tmp_path / "bundle-metadata.json"
    output.write_text("stale")
    result = _generate(prefix, output)
    assert result.returncode != 0
    assert not output.exists()
