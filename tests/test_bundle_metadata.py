from __future__ import annotations

import json
import os
import platform
from pathlib import Path
import stat
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


def _makefile_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in (REPO_ROOT / "pgbuild" / "Makefile").read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if ":=" not in stripped or stripped.startswith("\t"):
            continue
        key, _, value = stripped.partition(":=")
        key, value = key.strip(), value.strip()
        if not key or " " in key or "$" in value or not value:
            continue
        pins[key] = value
    return pins


_MAKEFILE_PINS = _makefile_pins()


def _firebird_client_pin() -> str | None:
    """The Firebird client archive digest for *this* host, as the Makefile pins it.

    `pgbuild/Makefile` selects ``FIREBIRD_CLIENT_SHA256_<HOST_OS>_<arch>``
    with ``arm64`` for arm64/aarch64 and ``x86_64`` otherwise. Hardcoding one
    platform's pin would let these tests pass on every host while pinning an
    identity no other platform would ever build, so the pin follows the host.
    Returns ``None`` where the Makefile has no pin for this host at all.
    """
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
    host_os = "Darwin" if sys.platform == "darwin" else "Linux"
    return _MAKEFILE_PINS.get(f"FIREBIRD_CLIENT_SHA256_{host_os}_{arch}")


def _require_firebird_pins() -> None:
    """Skip the firebird-client tests where this host has no Makefile pin."""
    if FIREBIRD_CLIENT_SHA256 is None:
        pytest.skip("pgbuild/Makefile pins no Firebird client digest for this host")


FIREBIRD_CLIENT_SHA256 = _firebird_client_pin()
LIBFQ_SHA256 = _MAKEFILE_PINS["LIBFQ_SHA256"]
LIBTOMMATH_SHA256 = _MAKEFILE_PINS["LIBTOMMATH_SHA256"]
EXPECTED_FIREBIRD_SOURCE_SUBMODULES = {
    "libfq": f"{_MAKEFILE_PINS['LIBFQ_TAG']}:{LIBFQ_SHA256}",
    "libtommath": f"{_MAKEFILE_PINS['LIBTOMMATH_TAG']}:{LIBTOMMATH_SHA256}",
    "firebird-client": f"{_MAKEFILE_PINS['FIREBIRD_CLIENT_VERSION']}:{FIREBIRD_CLIENT_SHA256}",
}

DEFAULT_SOURCE_LOCKS = {
    "pgvector": "pgvector=sha256:69f4019389af05dc1c9548deb8628e62878e6e207c03907f2b8af2016472cdaa:v0.8.2",
    "vectorchord": "vectorchord=sha256:d70b5595bfc852f1f24c05c0a40272e7deecbb0ddf8ffdddec5afa42c2392b1e:1.1.1",
    "age": "age=commit:e43dc1a12b78fba4acef9835b2b10379b8d243b4",
    "psql_bm25s": "psql_bm25s=commit:d1c1db7e6c2a92c2a909e97c51cf2f45c0da808b",
    "timescaledb": "timescaledb=sha256:f0a940720bb5b0b635dae4d8aeceb13e83b196b8aab8717876af0f45efa47ab6:2.27.1",
    "pg_cron": "pg_cron=commit:465b38c737f584d520229f5a1d69d1d44649e4e5:v1.6.7",
    "pg_net": "pg_net=commit:a8299b11182ea5c974f5e89ae83e70e9e44e9e8f:v0.20.5",
    "pgsql_http": "pgsql_http=sha256:d0330cbf32b37be3bd7ce52919439c903a6f4e88e99e359de2db4050bc3ef726:v1.7.0",
    "plsh": "plsh=commit:8bcfab5a0f483fc7eda2ae93b6ef64d10565785c",
    "firebird_fdw": "firebird_fdw=sha256:0e76750b4b6ef1ebc125d0ed3d2b204e6e545bc7c1b1a8c6b3f712497138518b:1.4.2",
    "libfq": f"libfq=sha256:{LIBFQ_SHA256}:{_MAKEFILE_PINS['LIBFQ_TAG']}",
    "libtommath": f"libtommath=sha256:{LIBTOMMATH_SHA256}:{_MAKEFILE_PINS['LIBTOMMATH_TAG']}",
    "firebird-client": f"firebird-client=sha256:{FIREBIRD_CLIENT_SHA256}:{_MAKEFILE_PINS['FIREBIRD_CLIENT_VERSION']}",
    "pgmq": "pgmq=sha256:e6bdbb2311a3bbf34439871a99ee1e5e87c79fdca2b1e6784411a51b079314d1:v1.12.0",
    "pg_partman": "pg_partman=sha256:a3f100ae871677f0012579f58542c174900297f664a4ad2be0256e7ee5e33502:v5.5.0",
    "pgtap": "pgtap=sha256:d2c951afb296a001d21785611a8e966e3f8fa3f5bfbd929396a5130c0152f314:v1.3.4",
    "pg_jsonschema": "pg_jsonschema=commit:d08e4dea14549858b54791d6da4f606dc58a512e",
    "pg_typesafe": "pg_typesafe=commit:93a5acbb43154aea757a96adb680fb3d45a00a9a",
    # Derived, not literal: a pin recorded here in duplicate once drifted from the
    # Makefile's own (ad4d3b74 recorded while the Makefile said 3227d7af), which
    # is the failure mode this dict exists to catch.
    "stannum": f"stannum=commit:{_MAKEFILE_PINS['STANNUM_COMMIT']}",
    "tigerfs": "tigerfs=sha256:0000000000000000000000000000000000000000000000000000000000000000:v0.7.0",
}
FIREBIRD_SUBMODULE_LOCKS = ("libfq", "libtommath", "firebird-client")


def _locks_for(built: str) -> list[str]:
    specs: list[str] = []
    seen: set[str] = set()
    for name in built.split():
        names = [name]
        if name == "firebird_fdw":
            names.extend(FIREBIRD_SUBMODULE_LOCKS)
        for lock_name in names:
            spec = DEFAULT_SOURCE_LOCKS.get(lock_name)
            if spec and spec not in seen:
                specs.append(spec)
                seen.add(spec)
    return specs


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
    source_locks: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
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
    ]
    locks = source_locks if source_locks is not None else _locks_for(built)
    for spec in locks:
        command.extend(["--source-lock", spec])
    return subprocess.run(command, capture_output=True, text=True, env=env)


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
    assert metadata.extensions["pgvector"].has_library is True
    assert metadata.extensions["pgmq"].has_library is False
    assert metadata.extensions["pgmq"].built is False
    assert metadata.extensions["pg_partman"].has_library is False
    assert metadata.extensions["pg_partman"].built is False
    assert metadata.extensions["pgtap"].has_library is False
    assert metadata.extensions["pgtap"].built is False
    assert metadata.extensions["pg_jsonschema"].has_library is True
    assert metadata.extensions["pg_jsonschema"].built is False
    assert metadata.extensions["pg_typesafe"].has_library is True
    assert metadata.extensions["pg_typesafe"].built is False
    assert metadata.extensions["stannum"].has_library is True
    assert metadata.extensions["stannum"].built is False
    assert metadata.extensions["stannum"].version is None
    assert metadata.extensions["stannum"].source_commit is None
    assert metadata.extensions["stannum"].source_sha256 is None
    assert "pg_textsearch" not in metadata.extensions
    assert BUNDLE_METADATA_PATH.parts[-4:] == (
        "pginstall", "share", "pgembed", "build-metadata.json"
    )


def test_generator_records_firebird_fdw_client_dependencies(tmp_path: Path) -> None:
    _require_firebird_pins()
    prefix = _prefix(tmp_path)
    extension_dir = prefix / "share" / "postgresql" / "extension"
    library = prefix / "lib" / "postgresql" / "firebird_fdw.dylib"
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(b"fixture")
    (extension_dir / "firebird_fdw.control").write_text("default_version = '1.4.2'\n")
    (extension_dir / "firebird_fdw--1.4.2.sql").write_text("-- fixture\n")
    output = prefix / "bundle-metadata.json"
    result = _generate(
        prefix,
        output,
        requested="pgvector firebird_fdw",
        built="pgvector firebird_fdw",
    )
    assert result.returncode == 0, result.stderr
    metadata = load_bundle_metadata(output)
    assert metadata is not None
    extension = metadata.extensions["firebird_fdw"]
    assert extension.built is True
    assert extension.requires_preload is False
    assert extension.preload_name is None
    assert extension.create_name == "firebird_fdw"
    assert extension.version == "1.4.2"
    assert extension.source_submodules == EXPECTED_FIREBIRD_SOURCE_SUBMODULES


def test_generator_records_sql_only_pgmq(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    extension_dir = prefix / "share" / "postgresql" / "extension"
    (extension_dir / "pgmq.control").write_text("default_version = '1.12.0'\n")
    (extension_dir / "pgmq--1.12.0.sql").write_text("-- fixture\n")
    output = prefix / "bundle-metadata.json"
    result = _generate(
        prefix,
        output,
        requested="pgvector pgmq",
        built="pgvector pgmq",
    )
    assert result.returncode == 0, result.stderr
    metadata = load_bundle_metadata(output)
    assert metadata is not None
    extension = metadata.extensions["pgmq"]
    assert extension.built is True
    assert extension.library is None
    assert extension.control == "share/postgresql/extension/pgmq.control"
    assert extension.install_sql == "share/postgresql/extension/pgmq--1.12.0.sql"
    assert extension.requires_preload is False
    assert extension.preload_name is None
    assert extension.create_name == "pgmq"
    assert extension.version == "1.12.0"
    assert extension.has_library is False
    assert extension.source_sha256 == (
        "e6bdbb2311a3bbf34439871a99ee1e5e87c79fdca2b1e6784411a51b079314d1"
    )


def test_native_built_extension_cannot_omit_library(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    assert _generate(prefix, output).returncode == 0
    payload = json.loads(output.read_text())
    payload["extensions"]["pgvector"]["library"] = None
    output.write_text(json.dumps(payload))
    with pytest.raises(BundledPostgresMetadataError, match="library is not recorded"):
        load_bundle_metadata(output)

    payload["extensions"]["pgvector"]["has_library"] = False
    output.write_text(json.dumps(payload))
    with pytest.raises(BundledPostgresMetadataError, match="cannot be SQL-only"):
        load_bundle_metadata(output)


def test_generator_records_sql_only_pg_partman_and_pgtap(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    extension_dir = prefix / "share" / "postgresql" / "extension"
    (extension_dir / "pg_partman.control").write_text("default_version = '5.5.0'\n")
    (extension_dir / "pg_partman--5.5.0.sql").write_text("-- fixture\n")
    (extension_dir / "pgtap.control").write_text("default_version = '1.3.4'\n")
    (extension_dir / "pgtap--1.3.4.sql").write_text("-- fixture\n")
    output = prefix / "bundle-metadata.json"
    result = _generate(
        prefix,
        output,
        requested="pgvector pg_partman pgtap",
        built="pgvector pg_partman pgtap",
    )
    assert result.returncode == 0, result.stderr
    metadata = load_bundle_metadata(output)
    assert metadata is not None
    partman = metadata.extensions["pg_partman"]
    assert partman.built is True
    assert partman.library is None
    assert partman.has_library is False
    assert partman.create_name == "pg_partman"
    assert partman.version == "5.5.0"
    pgtap = metadata.extensions["pgtap"]
    assert pgtap.built is True
    assert pgtap.library is None
    assert pgtap.has_library is False
    assert pgtap.create_name == "pgtap"
    assert pgtap.version == "1.3.4"


def test_generator_rejects_sql_only_pgmq_with_native_library(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    extension_dir = prefix / "share" / "postgresql" / "extension"
    (extension_dir / "pgmq.control").write_text("default_version = '1.12.0'\n")
    (extension_dir / "pgmq--1.12.0.sql").write_text("-- fixture\n")
    unexpected = prefix / "lib" / "postgresql" / "pgmq.dylib"
    unexpected.write_bytes(b"stale")
    output = prefix / "bundle-metadata.json"
    result = _generate(
        prefix,
        output,
        requested="pgvector pgmq",
        built="pgvector pgmq",
    )
    assert result.returncode != 0
    assert "SQL-only" in result.stderr
    assert not output.exists()


def test_generator_rejects_built_pgmq_without_control(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    result = _generate(
        prefix,
        output,
        requested="pgvector pgmq",
        built="pgvector pgmq",
    )
    assert result.returncode != 0
    assert "pgmq.control" in result.stderr
    assert not output.exists()


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


def test_generator_failure_leaves_existing_metadata_intact(tmp_path: Path) -> None:
    prefix = tmp_path / "missing-prefix"
    output = tmp_path / "bundle-metadata.json"
    output.write_text("stale")
    result = _generate(prefix, output)
    assert result.returncode != 0
    assert output.read_text() == "stale"


def test_generator_version_comes_from_control_file(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    extension = prefix / "share" / "postgresql" / "extension"
    (extension / "vector.control").write_text("default_version = '9.9.9'\n")
    (extension / "vector--0.8.2.sql").unlink()
    (extension / "vector--9.9.9.sql").write_text("-- fixture\n")
    output = prefix / "bundle-metadata.json"
    result = _generate(prefix, output)
    assert result.returncode == 0, result.stderr
    metadata = load_bundle_metadata(output)
    assert metadata is not None
    assert metadata.extensions["pgvector"].version == "9.9.9"
    assert metadata.extensions["pgvector"].source_sha256 == (
        "69f4019389af05dc1c9548deb8628e62878e6e207c03907f2b8af2016472cdaa"
    )
    assert metadata.extensions["pgvector"].source_ref == "v0.8.2"
    assert metadata.extensions["pgvector"].source_commit is None


def test_generator_fails_when_built_control_has_no_default_version(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    (prefix / "share" / "postgresql" / "extension" / "vector.control").write_text(
        "comment = 'no version'\n"
    )
    output = prefix / "bundle-metadata.json"
    result = _generate(prefix, output)
    assert result.returncode != 0
    assert "default_version" in result.stderr


def test_generator_fails_when_built_extension_has_no_source_lock(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    result = _generate(prefix, output, source_locks=[])
    assert result.returncode != 0
    assert "no --source-lock" in result.stderr
    assert not output.exists()


def test_generator_fails_when_source_locks_disagree(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    result = _generate(
        prefix,
        output,
        source_locks=[
            "pgvector=sha256:69f4019389af05dc1c9548deb8628e62878e6e207c03907f2b8af2016472cdaa:v0.8.2",
            "pgvector=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:v0.8.2",
        ],
    )
    assert result.returncode != 0
    assert "conflicting --source-lock" in result.stderr
    assert not output.exists()


def test_generator_records_stannum_lock_commit(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    extension = prefix / "share" / "postgresql" / "extension"
    library = prefix / "lib" / "postgresql" / "stannum.dylib"
    library.write_bytes(b"fixture")
    (extension / "stannum.control").write_text("default_version = '0.5.0'\n")
    (extension / "stannum--0.5.0.sql").write_text("-- fixture\n")
    output = prefix / "bundle-metadata.json"
    result = _generate(
        prefix,
        output,
        requested="pgvector stannum",
        built="pgvector stannum",
    )
    assert result.returncode == 0, result.stderr
    metadata = load_bundle_metadata(output)
    assert metadata is not None
    stannum = metadata.extensions["stannum"]
    assert stannum.version == "0.5.0"
    assert stannum.source_commit == _MAKEFILE_PINS["STANNUM_COMMIT"]
    assert stannum.source_ref == _MAKEFILE_PINS["STANNUM_COMMIT"]
    assert stannum.source_sha256 is None


def test_generator_refuses_symlinked_output(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    real = tmp_path / "real.json"
    real.write_text("old\n")
    output = tmp_path / "link.json"
    output.symlink_to(real)
    result = _generate(prefix, output)
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert real.read_text() == "old\n"
    assert output.is_symlink()


def test_generator_refuses_dangling_symlinked_output(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = tmp_path / "dangling.json"
    output.symlink_to(tmp_path / "missing.json")
    result = _generate(prefix, output)
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert output.is_symlink()


def test_generator_refuses_symlinked_parent(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    parent = tmp_path / "parent"
    parent.symlink_to(target_dir)
    output = parent / "bundle-metadata.json"
    result = _generate(prefix, output)
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert not (target_dir / "bundle-metadata.json").exists()


def test_crash_before_rename_leaves_old_metadata_intact(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    output.write_text('{"schema_version": 1, "preserved": true}\n')
    old = output.read_text()
    env = dict(os.environ)
    env["JEV_OVERLAY_CRASH_BEFORE_RENAME"] = "1"
    result = _generate(prefix, output, env=env)
    assert result.returncode == 91
    assert output.read_text() == old


def test_written_metadata_is_0644_and_world_readable(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    output = prefix / "bundle-metadata.json"
    result = _generate(prefix, output)
    assert result.returncode == 0, result.stderr
    mode = output.stat().st_mode
    assert stat.S_IMODE(mode) == 0o644
    assert mode & stat.S_IROTH


def test_generator_refuses_symlinked_grandparent_below_prefix(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    target = prefix / "share"
    link = prefix / "share-link"
    link.symlink_to(target)
    output = link / "pgembed" / "build-metadata.json"
    result = _generate(prefix, output)
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert not (target / "pgembed" / "build-metadata.json").exists()
    assert not (target / "pgembed").exists()


def test_generator_accepts_symlinked_install_prefix(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path)
    linked = tmp_path / "linked-prefix"
    linked.symlink_to(prefix)
    output = linked / "share" / "pgembed" / "build-metadata.json"
    result = _generate(linked, output)
    assert result.returncode == 0, result.stderr
    assert (prefix / "share" / "pgembed" / "build-metadata.json").is_file()


def test_generator_accepts_equivalent_prefix_spelling(tmp_path: Path) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias-root"
    alias.symlink_to(physical)
    prefix = _prefix(physical)
    aliased_prefix = alias / "prefix"
    assert os.path.realpath(aliased_prefix) == os.path.realpath(prefix)
    assert os.fspath(aliased_prefix) != os.path.realpath(prefix)
    output = aliased_prefix / "bundle-metadata.json"
    result = _generate(aliased_prefix, output)
    assert result.returncode == 0, result.stderr
    assert (prefix / "bundle-metadata.json").is_file()
