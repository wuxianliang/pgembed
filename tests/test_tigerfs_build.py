from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PGBUILD = REPO_ROOT / "pgbuild"
BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-and-test.yml"


FAKE_CURL = r'''#!/bin/bash
set -eu
output=""
url=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o)
            output="$2"
            shift 2
            ;;
        *)
            url="$1"
            shift
            ;;
    esac
done
[ -n "$output" ]
archive="${url##*/}"
source="${FAKE_CURL_FIXTURES}/${archive}"
[ -f "$source" ]
cp "$source" "$output"
count=$(cat "$FAKE_CURL_COUNT")
printf '%s\n' "$((count + 1))" > "$FAKE_CURL_COUNT"
'''


class TigerFSBuild:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.prefix = tmp_path / "pginstall"
        self.bin_dir = self.prefix / "bin"
        self.bin_dir.mkdir(parents=True)
        for name in ("postgres", "pg_config", "pg_ctl", "psql"):
            executable = self.bin_dir / name
            executable.write_bytes(b"dummy postgres tool")
            executable.chmod(0o755)
        self.fixtures = tmp_path / "fixtures"
        self.fixtures.mkdir()
        self.fake_bin = tmp_path / "fake-bin"
        self.fake_bin.mkdir()
        (self.fake_bin / "curl").write_text(FAKE_CURL)
        (self.fake_bin / "curl").chmod(0o755)
        self.curl_count = tmp_path / "curl-count"
        self.curl_count.write_text("0\n")
        self.stamp = tmp_path / ".tigerfs-config.stamp"
        self.bundle_stamp = tmp_path / ".postgres-bundle-config.stamp"

    def archive(self, os_name: str, arch: str, payload: bytes) -> str:
        archive_name = f"tigerfs_{os_name}_{arch}.tar.gz"
        archive_path = self.fixtures / archive_name
        source = self.root / "fixture-tigerfs"
        source.write_bytes(payload)
        source.chmod(0o755)
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(source, arcname="tigerfs")
        return hashlib.sha256(archive_path.read_bytes()).hexdigest()

    def count(self) -> int:
        return int(self.curl_count.read_text().strip())

    def run(
        self,
        *,
        tag: str = "fixture-a",
        os_name: str = "Darwin",
        arch: str = "arm64",
        expected_sha: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if expected_sha is None:
            archive_path = self.fixtures / f"tigerfs_{os_name}_{arch}.tar.gz"
            expected_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        sha_variable = f"TIGERFS_SHA256_{os_name}_{arch}={expected_sha}"
        make = shutil.which("make") or "make"
        variables = [
            f"INSTALL_PREFIX={self.prefix}",
            f"POSTGRES_BUNDLE_CONFIG_STAMP={self.bundle_stamp}",
            f"TIGERFS_CONFIG_STAMP={self.stamp}",
            f"TIGERFS_TAG={tag}",
            f"TIGERFS_OS={os_name}",
            f"TIGERFS_ARCH={arch}",
            f"TIGERFS_BASE=https://fixture.invalid/{tag}",
            "HOST_OS=Darwin",
            "HOST_ARCH=arm64",
            "EXTENSIONS=tigerfs",
            sha_variable,
        ]
        stamp_command = [make, "-f", str(PGBUILD / "Makefile"), str(self.bundle_stamp), *variables]
        command = [make, "-f", str(PGBUILD / "Makefile"), "tigerfs", *variables]
        env = os.environ.copy()
        env["PATH"] = f"{self.fake_bin}{os.pathsep}{env['PATH']}"
        env["FAKE_CURL_FIXTURES"] = str(self.fixtures)
        env["FAKE_CURL_COUNT"] = str(self.curl_count)
        stamp_result = subprocess.run(
            stamp_command,
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=check,
        )
        if stamp_result.returncode != 0:
            return stamp_result
        # Satisfy the PostgreSQL target graph with fixture files newer than the
        # just-generated bundle stamp; TigerFS tests must never clone/build PG.
        source_marker = self.root / "postgres-REL_18_4" / ".pgembed-source-verified"
        source_marker.parent.mkdir(parents=True, exist_ok=True)
        source_marker.write_text("fixture")
        source_configure = source_marker.parent / "configure"
        source_configure.write_text("fixture")
        config_status = self.root / "postgres-build-REL_18_4" / "config.status"
        config_status.parent.mkdir(parents=True, exist_ok=True)
        config_status.write_text("fixture")
        initdb = self.root / "postgres-build-REL_18_4" / "src" / "bin" / "initdb" / "initdb"
        initdb.parent.mkdir(parents=True, exist_ok=True)
        initdb.write_text("fixture")
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        for name in ("postgres", "pg_config", "pg_ctl", "psql"):
            executable = self.bin_dir / name
            executable.write_bytes(b"dummy postgres tool")
            executable.chmod(0o755)
        return subprocess.run(
            command,
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=check,
        )

    @property
    def tigerfs(self) -> Path:
        return self.bin_dir / "tigerfs"


def test_existing_untracked_binary_is_invalidated_and_replaced(tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    build.archive("Darwin", "arm64", b"fixture-a")
    build.tigerfs.write_bytes(b"stale binary")
    build.tigerfs.chmod(0o755)

    build.run()

    assert build.tigerfs.read_bytes() == b"fixture-a"
    assert build.stamp.exists()
    assert build.count() == 1


def test_initial_install_and_identical_second_run_preserve_stamp_and_skip_fetch(
    tmp_path: Path,
) -> None:
    build = TigerFSBuild(tmp_path)
    build.archive("Darwin", "arm64", b"fixture-a")

    build.run()
    stamp_mtime = build.stamp.stat().st_mtime_ns
    binary_mtime = build.tigerfs.stat().st_mtime_ns
    build.run()

    assert build.tigerfs.read_bytes() == b"fixture-a"
    assert build.count() == 1
    assert build.stamp.stat().st_mtime_ns == stamp_mtime
    assert build.tigerfs.stat().st_mtime_ns == binary_mtime


def test_tag_change_replaces_binary(tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    build.archive("Darwin", "arm64", b"fixture-a")
    build.run(tag="fixture-a")
    build.archive("Darwin", "arm64", b"fixture-b")
    build.run(tag="fixture-b")

    assert build.tigerfs.read_bytes() == b"fixture-b"
    assert build.count() == 2


def test_os_change_replaces_binary(tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    build.archive("Darwin", "arm64", b"darwin")
    build.archive("Linux", "arm64", b"linux")
    build.run(os_name="Darwin", arch="arm64")
    build.run(os_name="Linux", arch="arm64")

    assert build.tigerfs.read_bytes() == b"linux"
    assert build.count() == 2


def test_arch_change_replaces_binary(tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    build.archive("Darwin", "arm64", b"arm64")
    build.archive("Darwin", "x86_64", b"x86_64")
    build.run(arch="arm64")
    build.run(arch="x86_64")

    assert build.tigerfs.read_bytes() == b"x86_64"
    assert build.count() == 2


def test_sha_only_change_replaces_and_reverifies_binary(tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    build.archive("Darwin", "arm64", b"fixture-a")
    first_sha = hashlib.sha256(
        (build.fixtures / "tigerfs_Darwin_arm64.tar.gz").read_bytes()
    ).hexdigest()
    build.run(expected_sha=first_sha)

    build.archive("Darwin", "arm64", b"fixture-b")
    second_sha = hashlib.sha256(
        (build.fixtures / "tigerfs_Darwin_arm64.tar.gz").read_bytes()
    ).hexdigest()
    build.run(expected_sha=second_sha)

    assert build.tigerfs.read_bytes() == b"fixture-b"
    assert build.count() == 2


def test_incorrect_sha_removes_stale_binary_and_fails(tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    build.archive("Darwin", "arm64", b"fixture-a")
    build.run()

    result = build.run(expected_sha="0" * 64, check=False)

    assert result.returncode != 0
    assert not build.tigerfs.exists()
    assert "expected_sha256=" + "0" * 64 in build.stamp.read_text()
    assert build.count() == 2


def test_a_to_b_to_a_rebuilds_a_from_the_stable_stamp(tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    archive_path = build.fixtures / "tigerfs_Darwin_arm64.tar.gz"

    build.archive("Darwin", "arm64", b"fixture-a")
    build.run(tag="fixture-a")
    build.archive("Darwin", "arm64", b"fixture-b")
    build.run(tag="fixture-b")
    build.archive("Darwin", "arm64", b"fixture-a")
    build.run(tag="fixture-a")

    assert build.tigerfs.read_bytes() == b"fixture-a"
    assert build.count() == 3
    assert archive_path.exists()


def test_deleting_binary_rebuilds_without_changing_stamp(tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    build.archive("Darwin", "arm64", b"fixture-a")
    build.run()
    stamp_mtime = build.stamp.stat().st_mtime_ns
    build.tigerfs.unlink()

    build.run()

    assert build.tigerfs.read_bytes() == b"fixture-a"
    assert build.count() == 2
    assert build.stamp.stat().st_mtime_ns == stamp_mtime


def test_touching_postgres_does_not_reinstall_tigerfs(tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    build.archive("Darwin", "arm64", b"fixture-a")
    build.run()
    postgres = build.bin_dir / "postgres"
    os.utime(postgres, (postgres.stat().st_atime + 1000, postgres.stat().st_mtime + 1000))

    build.run()

    assert build.count() == 1
    assert build.tigerfs.read_bytes() == b"fixture-a"


def test_mismatched_content_wins_over_adversarial_mtimes(tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    build.archive("Darwin", "arm64", b"fixture-a")
    build.run(tag="fixture-a")
    future = build.stamp.stat().st_mtime + 10_000
    os.utime(build.stamp, (future, future))
    os.utime(build.tigerfs, (future, future))

    build.archive("Darwin", "arm64", b"fixture-b")
    build.run(tag="fixture-b")

    assert build.tigerfs.read_bytes() == b"fixture-b"
    assert build.count() == 2


@pytest.mark.parametrize("os_name", ["Darwin", "Linux"])
def test_unknown_supported_host_architecture_fails(os_name: str, tmp_path: Path) -> None:
    build = TigerFSBuild(tmp_path)
    result = build.run(os_name=os_name, arch="mips64", expected_sha="", check=False)

    assert result.returncode != 0
    assert "no pinned SHA-256" in result.stderr


def test_unsupported_build_host_fails_at_parse_time(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            shutil.which("make") or "make",
            "-C",
            str(PGBUILD),
            "-n",
            "all",
            "HOST_OS=FreeBSD",
            "EXTENSIONS=",
            f"INSTALL_PREFIX={tmp_path / 'pginstall'}",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "supported hosts are Darwin and Linux" in result.stderr


def test_release_matrix_is_darwin_arm64_and_linux_only() -> None:
    workflow = BUILD_WORKFLOW.read_text()
    matrix = workflow.split("  build_wheels:", maxsplit=1)[1].split(
        "    steps:", maxsplit=1
    )[0]
    rows = re.findall(
        r"^\s+- runner: (\S+)\n\s+os: (\S+)\n\s+arch: (\S+)$",
        matrix,
        flags=re.MULTILINE,
    )

    assert rows == [
        ("macos-latest", "macos-latest", "arm64"),
        ("ubuntu-latest", "ubuntu-latest", "x86_64"),
        ("ubuntu-24.04-arm", "ubuntu-latest", "aarch64"),
    ]
    assert "universal2" not in workflow.lower()


def test_macos_ci_uses_one_current_system_deployment_target() -> None:
    workflow = BUILD_WORKFLOW.read_text()

    assert workflow.count("deployment-target: '26.0'") == 1
    assert "minos 26.0. This does not claim compatibility with older macOS." in workflow
    assert workflow.count(
        "MACOSX_DEPLOYMENT_TARGET: ${{ matrix.deployment-target }}"
    ) == 2
    assert workflow.count(
        "MACOSX_DEPLOYMENT_TARGET=${{ matrix.deployment-target }}"
    ) == 1
    assert "deployment-target: '11.0'" not in workflow
    assert "MACOSX_DEPLOYMENT_TARGET=15.0" not in workflow


@pytest.mark.skipif(platform.system() != "Darwin", reason="release runner assertion is macOS-specific")
def test_release_runner_uses_system_gnu_make_381() -> None:
    result = subprocess.run(
        [shutil.which("make") or "make", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.splitlines()[0] == "GNU Make 3.81"
