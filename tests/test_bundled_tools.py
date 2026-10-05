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

# The extension versions this tree expects come from pgbuild/Makefile, next to
# the commits they ship: STANNUM_VERSION and PG_TYPESAFE_VERSION. They live there
# because the other half of each identity — the commit — is already there, and a
# release then moves one line in one file. Both were literals in this test and
# in the gate job's own check, and all three went stale when 0.5.1 shipped.

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "pgbuild" / "Makefile"


def makefile_pin(name: str) -> str:
    """A `NAME := value` source pin read from pgbuild/Makefile.

    The installed bundle must attest the commit the Makefile actually pins, so
    read the pin instead of copying it: a copied literal drifts silently and the
    wheel would then be measured against a source it never built.
    """
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if ":=" not in stripped or stripped.startswith("\t"):
            continue
        key, _, value = stripped.partition(":=")
        if key.strip() == name:
            pinned = value.strip()
            assert pinned, f"{name} is pinned to an empty value"
            return pinned
    raise AssertionError(f"{name} is not pinned in {MAKEFILE}")


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
        "pg_partman",
        "pgtap",
        "pg_jsonschema",
        "pg_typesafe",
        "stannum",
    }
    assert set(metadata.extensions) == expected
    assert "pg_textsearch" not in metadata.extensions

    bundle_root = Path(pgembed.POSTGRES_BIN_PATH).parent
    leftovers = [
        *sorted((bundle_root / "lib" / "postgresql").glob("pg_textsearch*")),
        *sorted((bundle_root / "share" / "postgresql" / "extension").glob("pg_textsearch*")),
    ]
    assert leftovers == [], f"pg_textsearch artifacts in bundle prefix: {leftovers}"
    for name in sorted(expected):
        extension = metadata.extensions[name]
        assert extension.requested and extension.built and not extension.skipped
        assert extension.built_for_postgres_major == 18
        assert extension.source_commit or extension.source_sha256
        assert pgembed.has_extension(name)
        assert extension.control is not None
        assert extension.install_sql is not None
        if name in {"pgmq", "pg_partman", "pgtap"}:
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

    partman = metadata.extensions["pg_partman"]
    assert partman.requires_preload is False
    assert partman.preload_name is None
    assert partman.create_name == "pg_partman"
    assert partman.version == "5.5.0"
    assert partman.has_library is False
    assert pgembed.get_extension_path("pg_partman") is None

    pgtap = metadata.extensions["pgtap"]
    assert pgtap.requires_preload is False
    assert pgtap.create_name == "pgtap"
    assert pgtap.version == "1.3.4"
    assert pgtap.has_library is False
    assert pgembed.get_extension_path("pgtap") is None

    jsonschema = metadata.extensions["pg_jsonschema"]
    assert jsonschema.requires_preload is False
    assert jsonschema.create_name == "pg_jsonschema"
    assert jsonschema.version == "0.3.4"
    assert jsonschema.has_library is True
    assert jsonschema.source_commit == "d08e4dea14549858b54791d6da4f606dc58a512e"

    typesafe = metadata.extensions["pg_typesafe"]
    assert typesafe.requires_preload is False
    assert typesafe.preload_name is None
    assert typesafe.create_name == "typesafe"
    assert typesafe.has_library is True
    assert typesafe.library is not None
    if typesafe.source_ref == "local-overlay:typesafe":
        assert typesafe.version == "0.1.0"
        assert typesafe.source_commit is None
        assert typesafe.source_sha256 == (
            "9bfb234189f764fbd100b7d638f5f16d8096e4fb49e0cbecf44e2d7733718a73"
        )
    else:
        # CI builds the pin, which is the fork's 0.1.0 release commit. 0.0.1
        # was the old upstream pin; seeing it again means the pin moved back.
        expected = makefile_pin("PG_TYPESAFE_VERSION")
        assert typesafe.version == expected, (
            f"pg_typesafe ships {typesafe.version}, the Makefile declares {expected}: "
            "update PG_TYPESAFE_VERSION when the pinned repository ships a release"
        )
        assert typesafe.source_commit == makefile_pin("PG_TYPESAFE_COMMIT")
    library_path = bundle_root / typesafe.library
    assert library_path.is_file(), f"typesafe library is missing: {library_path}"

    stannum = metadata.extensions["stannum"]
    assert stannum.requires_preload is False
    assert stannum.preload_name is None
    assert stannum.create_name == "stannum"
    expected = makefile_pin("STANNUM_VERSION")
    assert stannum.version == expected, (
        f"stannum ships {stannum.version}, the Makefile declares {expected}: "
        "update STANNUM_VERSION when the pinned repository ships a release"
    )
    assert stannum.has_library is True
    assert stannum.library is not None
    assert stannum.source_commit == makefile_pin("STANNUM_COMMIT")
    stannum_library_path = bundle_root / stannum.library
    assert stannum_library_path.is_file(), f"stannum library is missing: {stannum_library_path}"

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
