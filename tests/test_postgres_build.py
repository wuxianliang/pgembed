from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "pgbuild" / "Makefile"
PG_DUCKDB_PATCH = REPO_ROOT / "pgbuild" / "patches" / "pg_duckdb-v1.1.1-planner-hook-chain.patch"
PG_DUCKDB_PATCH_SHA256 = "d7d452530d7fb537ae5f415d6980543f9bcf99a6f2a3f4868a4779be83b472db"


class BundleStamp:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.prefix = root / "prefix"
        self.stamp = root / "bundle.stamp"

    def run(self, **variables: str) -> subprocess.CompletedProcess[str]:
        command = [
            shutil.which("make") or "make",
            "-f",
            str(MAKEFILE),
            str(self.stamp),
            f"INSTALL_PREFIX={self.prefix}",
            f"POSTGRES_BUNDLE_CONFIG_STAMP={self.stamp}",
            "EXTENSIONS=pgvector tigerfs",
            "HOST_OS=Darwin",
            "HOST_ARCH=arm64",
            "TIGERFS_OS=Darwin",
            "TIGERFS_ARCH=arm64",
        ]
        command.extend(f"{key}={value}" for key, value in variables.items())
        return subprocess.run(command, cwd=self.root, text=True, capture_output=True, check=True)

    def complete_prefix(self, marker: str = "payload") -> Path:
        bin_dir = self.prefix / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in ("postgres", "pg_config", "pg_ctl", "psql"):
            executable = bin_dir / name
            executable.write_text(marker)
            executable.chmod(0o755)
        return bin_dir / "postgres"


def test_initial_stamp_invalidates_unattested_prefix(tmp_path: Path) -> None:
    build = BundleStamp(tmp_path)
    stale = build.complete_prefix("pg17")
    build.run()
    assert build.stamp.is_file()
    assert not stale.exists()
    assert "postgres_major=18" in build.stamp.read_text()


def test_identical_stamp_preserves_mtime_and_complete_prefix(tmp_path: Path) -> None:
    build = BundleStamp(tmp_path)
    build.run()
    postgres = build.complete_prefix()
    first_mtime = build.stamp.stat().st_mtime_ns
    time.sleep(0.01)
    build.run()
    assert build.stamp.stat().st_mtime_ns == first_mtime
    assert postgres.read_text() == "payload"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("POSTGRES_VERSION", "18.5"),
        ("POSTGRES_SRC_REF", "REL_18_5"),
        ("POSTGRES_CONFIGURE_FLAGS", "--without-readline --with-icu"),
        ("EXTENSIONS", "pgvector"),
        ("PG_DUCKDB_GEN", "fixture-generator"),
        ("PG_DUCKDB_LZ4_PREFIX", "/fixture/lz4"),
        ("PG_DUCKDB_OPENSSL_PREFIX", "/fixture/openssl"),
        ("PSQL_BM25S_ICU_PREFIX", "/fixture/icu"),
        ("PG_NET_CURL_PREFIX", "/fixture/curl"),
    ],
)
def test_identity_changes_invalidate_prefix(
    tmp_path: Path, variable: str, value: str
) -> None:
    build = BundleStamp(tmp_path)
    build.run()
    postgres = build.complete_prefix()
    build.run(**{variable: value})
    assert not postgres.exists()


def test_a_to_b_to_a_uses_one_stable_stamp(tmp_path: Path) -> None:
    build = BundleStamp(tmp_path)
    build.run(POSTGRES_VERSION="18.4")
    a = build.stamp.read_text()
    build.complete_prefix("a")
    build.run(POSTGRES_VERSION="18.5")
    b = build.stamp.read_text()
    assert a != b
    build.complete_prefix("b")
    build.run(POSTGRES_VERSION="18.4")
    assert build.stamp.read_text() == a
    assert not (build.prefix / "bin" / "postgres").exists()


def test_future_timestamps_do_not_defeat_content_invalidation(tmp_path: Path) -> None:
    build = BundleStamp(tmp_path)
    build.run()
    postgres = build.complete_prefix()
    future = time.time() + 86400
    os.utime(build.stamp, (future, future))
    os.utime(postgres, (future, future))
    build.run(POSTGRES_VERSION="18.5")
    assert not postgres.exists()


def test_partial_prefix_is_removed_even_when_stamp_matches(tmp_path: Path) -> None:
    build = BundleStamp(tmp_path)
    build.run()
    partial = build.prefix / "share" / "partial"
    partial.parent.mkdir(parents=True)
    partial.write_text("partial")
    build.run()
    assert not build.prefix.exists()


def test_missing_completion_marker_invalidates_release_bundle(tmp_path: Path) -> None:
    build = BundleStamp(tmp_path)
    build.run()
    build.complete_prefix()
    metadata = build.prefix / "share" / "pgembed" / "build-metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}")

    build.run(BUNDLE_COMPLETION_REQUIRED="1")

    assert not build.prefix.exists()


def test_source_lock_and_toolchain_are_recorded(tmp_path: Path) -> None:
    build = BundleStamp(tmp_path)
    build.run()
    text = build.stamp.read_text()
    assert "postgres_commit=f5cc81719e6da4cbdb1f797c48b693e91018153a" in text
    assert "rust_toolchain=1.95.0" in text
    assert "cargo_pgrx_version=0.17.0" in text
    assert "pg_duckdb_gen=" in text
    assert f"pg_duckdb=v1.1.1:7b0db3737a1ab2dfb182b742322426e3c4b500af:duckdb=d1dc88f950d456d72493df452dabdcd13aa413dd:patch={PG_DUCKDB_PATCH_SHA256}" in text
    assert "psql_bm25s_icu_prefix=" in text
    assert "pg_net_curl_prefix=" in text


def test_pg_duckdb_planner_patch_is_pinned_and_applied() -> None:
    makefile = MAKEFILE.read_text()
    patch = PG_DUCKDB_PATCH.read_text()

    assert PG_DUCKDB_PATCH.is_file()
    assert "PG_DUCKDB_PATCH := patches/pg_duckdb-v1.1.1-planner-hook-chain.patch" in makefile
    assert f"PG_DUCKDB_PATCH_SHA256 := {PG_DUCKDB_PATCH_SHA256}" in makefile
    assert "$(PG_DUCKDB_SOURCE_VERIFIED): $(POSTGRES_BUNDLE_CONFIG_STAMP) $(PG_DUCKDB_PATCH)" in makefile
    assert "printf '%s  %s\\n' '$(PG_DUCKDB_PATCH_SHA256)' '$(PG_DUCKDB_PATCH)' | $(SHA256_CMD) -c -" in makefile
    assert 'patch -d "$(PG_DUCKDB_DIR)" -p1 < "$(PG_DUCKDB_PATCH)"' in makefile
    assert "PlanPostgresQuery" in patch
    assert "postgres_planner_nest_level" in patch
    assert "PG_FINALLY" in patch
    assert "-\tPlannedStmt *planned_stmt = standard_planner(query, table_scan_query, 0, nullptr);" in patch
    assert "+\tPlannedStmt *planned_stmt = PlanPostgresQuery(query, table_scan_query, 0, nullptr);" in patch


def test_all_git_sources_use_verification_markers() -> None:
    makefile = MAKEFILE.read_text()
    markers = (
        "POSTGRES_SOURCE_VERIFIED",
        "PG_DUCKDB_SOURCE_VERIFIED",
        "AGE_SOURCE_VERIFIED",
        "PSQL_BM25S_SOURCE_VERIFIED",
        "PG_CRON_SOURCE_VERIFIED",
        "PG_NET_SOURCE_VERIFIED",
    )
    for marker in markers:
        assert f"$({marker}): $(POSTGRES_BUNDLE_CONFIG_STAMP)" in makefile
    assert "$(POSTGRES_SRC)/configure: $(POSTGRES_BUNDLE_CONFIG_STAMP)" not in makefile
    assert "$(PG_DUCKDB_DIR)/Makefile: $(POSTGRES_BUNDLE_CONFIG_STAMP)" not in makefile
    assert "AGE_BISON ?=" in makefile
    assert 'BISON="$(AGE_BISON)"' in makefile
    assert 'BISONFLAGS="$(AGE_BISONFLAGS)"' in makefile
    assert "-Wno-error=deprecated -Wno-error=other" in makefile


def test_failed_source_verification_is_retried(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    clone_count = tmp_path / "clone-count"
    clone_count.write_text("0\n")
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "if [ \"$1\" = clone ]; then\n"
        "  for destination; do :; done\n"
        "  mkdir -p \"$destination\"\n"
        "  printf '%s\\n' '#!/bin/sh' > \"$destination/configure\"\n"
        "  count=$(cat \"$FAKE_GIT_COUNT\")\n"
        "  printf '%s\\n' \"$((count + 1))\" > \"$FAKE_GIT_COUNT\"\n"
        "elif [ \"$1\" = -C ] && [ \"$3\" = rev-parse ]; then\n"
        "  printf '%s\\n' \"$FAKE_GIT_COMMIT\"\n"
        "else\n"
        "  exit 2\n"
        "fi\n"
    )
    fake_git.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_GIT_COUNT"] = str(clone_count)
    env["FAKE_GIT_COMMIT"] = "wrong-commit"
    marker = Path("postgres-source") / ".pgembed-source-verified"
    command = [
        shutil.which("make") or "make",
        "-f", str(MAKEFILE), str(marker),
        f"INSTALL_PREFIX={tmp_path / 'prefix'}",
        f"POSTGRES_BUNDLE_CONFIG_STAMP={tmp_path / 'bundle.stamp'}",
        "POSTGRES_SRC=postgres-source",
        "POSTGRES_SOURCE_COMMIT=expected-commit",
        "EXTENSIONS=",
        f"HOST_OS={'Darwin' if platform.system() == 'Darwin' else 'Linux'}",
    ]

    first = subprocess.run(command, cwd=tmp_path, env=env, text=True, capture_output=True)
    second = subprocess.run(command, cwd=tmp_path, env=env, text=True, capture_output=True)

    assert first.returncode != 0
    assert second.returncode != 0
    assert clone_count.read_text().strip() == "2"
    assert not (tmp_path / marker).exists()


def test_failed_archive_checksum_never_publishes_final_archive(tmp_path: Path) -> None:
    checksum_command = "shasum" if platform.system() == "Darwin" else "sha256sum"
    if shutil.which(checksum_command) is None:
        pytest.skip(f"{checksum_command} is unavailable")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "output=\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = -o ]; then output=$2; shift 2; else shift; fi\n"
        "done\n"
        "printf '%s\\n' corrupt > \"$output\"\n"
    )
    fake_curl.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    archive = Path("pgvector-v0.8.2.tar.gz")
    command = [
        shutil.which("make") or "make",
        "-f", str(MAKEFILE), str(archive),
        "PGVECTOR_SHA256=" + "0" * 64,
        "EXTENSIONS=",
        f"HOST_OS={'Darwin' if platform.system() == 'Darwin' else 'Linux'}",
    ]

    result = subprocess.run(command, cwd=tmp_path, env=env, text=True, capture_output=True)

    assert result.returncode != 0
    assert not (tmp_path / archive).exists()
    assert not list(tmp_path.glob(f"{archive}.tmp.*"))


@pytest.mark.skipif(platform.system() != "Darwin", reason="system GNU Make 3.81 gate is macOS-specific")
def test_bundle_stamp_works_with_system_gnu_make_381(tmp_path: Path) -> None:
    make = Path("/usr/bin/make")
    if not make.exists():
        pytest.skip("/usr/bin/make is unavailable")
    version = subprocess.run([str(make), "--version"], capture_output=True, text=True, check=True)
    assert "GNU Make 3.81" in version.stdout
    build = BundleStamp(tmp_path)
    command = [
        str(make), "-f", str(MAKEFILE), str(build.stamp),
        f"INSTALL_PREFIX={build.prefix}",
        f"POSTGRES_BUNDLE_CONFIG_STAMP={build.stamp}",
        "EXTENSIONS=pgvector", "HOST_OS=Darwin", "HOST_ARCH=arm64",
    ]
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True)
