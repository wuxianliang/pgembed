from __future__ import annotations

import os
import platform
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import pgembed
import pgembed._commands as commands
from pgembed._bundle_metadata import require_bundle_metadata


TIGERFS_VERSION = "0.7.0"


def tigerfs_path() -> Path:
    return Path(pgembed.POSTGRES_BIN_PATH) / "tigerfs"


def test_release_platform_and_architecture() -> None:
    machine = platform.machine().lower()

    if sys.platform == "darwin":
        assert machine == "arm64"
    elif sys.platform.startswith("linux"):
        assert machine in {"x86_64", "amd64", "aarch64", "arm64"}
    else:
        raise AssertionError(f"unsupported release platform: {sys.platform}/{machine}")


def test_bundled_postgres_and_pg_config_match_pg18_metadata() -> None:
    metadata = require_bundle_metadata()
    postgres = subprocess.run(
        [str(pgembed.POSTGRES_BIN_PATH / "postgres"), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    pg_config = subprocess.run(
        [str(pgembed.POSTGRES_BIN_PATH / "pg_config"), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    assert metadata.postgres_major == pgembed.BUNDLED_PG_MAJOR == 18
    assert metadata.postgres_version == pgembed.BUNDLED_POSTGRES_VERSION == "18.4"
    assert re.search(r"PostgreSQL\)?\s+18(?:\.|\b)", postgres)
    assert re.search(r"PostgreSQL\s+18(?:\.|\b)", pg_config)


def test_release_bundle_contains_complete_attested_extension_set() -> None:
    metadata = require_bundle_metadata()
    expected = {
        "pgvector",
        "vectorchord",
        "age",
        "psql_bm25s",
        "timescaledb",
        "pg_cron",
        "pg_net",
        "pgsql_http",
        "plsh",
        "firebird_fdw",
        "pgmq",
    }
    assert set(metadata.extensions) == expected

    bundle_root = Path(pgembed.POSTGRES_BIN_PATH).parent
    for name in sorted(expected):
        extension = metadata.extensions[name]
        assert extension.requested and extension.built and not extension.skipped
        assert extension.built_for_postgres_major == 18
        assert extension.source_commit or extension.source_sha256
        assert pgembed.has_extension(name)
        assert extension.control is not None
        assert extension.install_sql is not None
        if name == "pgmq":
            assert extension.library is None
        else:
            assert extension.library is not None
        for relative in (
            extension.library,
            extension.control,
            extension.install_sql,
            *extension.update_sql,
        ):
            if relative is None:
                continue
            artifact = bundle_root / relative
            assert artifact.is_file(), f"attested artifact is missing for {name}: {artifact}"

    pgmq = metadata.extensions["pgmq"]
    assert pgmq.requires_preload is False
    assert pgmq.preload_name is None
    assert pgmq.create_name == "pgmq"
    assert pgmq.version == "1.12.0"
    assert pgmq.has_library is False
    assert pgembed.get_extension_path("pgmq") is None

    firebird = metadata.extensions["firebird_fdw"]
    assert firebird.requires_preload is False
    assert firebird.preload_name is None
    assert firebird.create_name == "firebird_fdw"
    assert firebird.source_submodules.get("libfq")
    assert firebird.source_submodules.get("firebird-client")
    lib_dir = bundle_root / "lib"
    assert any(lib_dir.glob("libfbclient*")), "bundled libfbclient is missing"
    assert any(lib_dir.glob("libfq*")), "bundled libfq is missing"
    assert (bundle_root / "share" / "firebird" / "firebird.msg").is_file()


def test_installed_wheel_rejects_pg17_pgdata_without_mutation(tmp_path: Path) -> None:
    pgdata = tmp_path / "pg17-data"
    pgdata.mkdir()
    (pgdata / "PG_VERSION").write_text("17\n")
    (pgdata / "sentinel").write_bytes(b"must remain unchanged")
    before = {
        path.relative_to(pgdata): path.read_bytes()
        for path in pgdata.rglob("*")
        if path.is_file()
    }

    with pytest.raises(
        pgembed.PostgresDataDirectoryVersionError,
        match=r"major 17.*requires major 18",
    ):
        pgembed.get_server(pgdata)

    after = {
        path.relative_to(pgdata): path.read_bytes()
        for path in pgdata.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (pgdata / "postgresql.conf").exists()
    assert not (pgdata / "postmaster.pid").exists()


def test_tigerfs_is_bundled_and_executable() -> None:
    binary = tigerfs_path()

    assert binary.is_file(), f"bundled TigerFS executable is missing: {binary}"
    assert os.access(binary, os.X_OK), f"bundled TigerFS is not executable: {binary}"
    assert binary.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_tigerfs_version_completes_with_timeout() -> None:
    result = subprocess.run(
        [str(tigerfs_path()), "version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    output = result.stdout + result.stderr

    assert re.search(rf"\b(?:v)?{re.escape(TIGERFS_VERSION)}\b", output), output


def test_tigerfs_has_no_top_level_command_wrapper() -> None:
    assert pgembed.POSTGRES_BIN_PATH == commands.POSTGRES_BIN_PATH
    assert not hasattr(pgembed, "tigerfs")
    assert not hasattr(commands, "tigerfs")
    assert "tigerfs" not in commands.__all__


# These installed-package checks intentionally do not inspect /dev/fuse or mount a
# filesystem. Linux wheels must remain testable in ordinary manylinux containers.
