from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

import pgembed


@contextmanager
def _fresh_server():
    with tempfile.TemporaryDirectory(prefix="pgembed-pg18-") as root:
        with pgembed.get_server(Path(root) / "data", cleanup_mode="delete") as server:
            yield server


@pytest.mark.integration
def test_pg18_native_uuidv7_and_no_public_shim_on_fresh_cluster() -> None:
    with _fresh_server() as server:
        server.psql(
            """
            DO $$
            DECLARE generated uuid;
            BEGIN
                generated := uuidv7();
                IF substr(generated::text, 15, 1) <> '7' THEN
                    RAISE EXCEPTION 'uuidv7() returned non-v7 UUID: %', generated;
                END IF;
                IF to_regprocedure('pg_catalog.uuidv7()') IS NULL THEN
                    RAISE EXCEPTION 'pg_catalog.uuidv7() is unavailable';
                END IF;
                IF to_regprocedure('public.uuidv7()') IS NOT NULL THEN
                    RAISE EXCEPTION 'fresh cluster unexpectedly has public.uuidv7()';
                END IF;
            END
            $$;
            """
        )


@pytest.mark.integration
def test_legacy_public_uuidv7_shim_can_be_inventoried_without_overwriting_it() -> None:
    with _fresh_server() as server:
        server.psql(
            "CREATE FUNCTION public.uuidv7() RETURNS uuid "
            "LANGUAGE sql AS 'SELECT pg_catalog.uuidv7()';"
        )
        inventory = server.psql(
            "SELECT n.nspname || '.' || p.proname "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname IN ('uuidv7', 'generate_uuidv7');"
        )
        assert "public.uuidv7" in inventory
        server.psql(
            """
            DO $$
            BEGIN
                IF substr(public.uuidv7()::text, 15, 1) <> '7' THEN
                    RAISE EXCEPTION 'legacy public.uuidv7() wrapper did not return UUIDv7';
                END IF;
            END
            $$;
            """
        )


def _wait_for_mount(path: Path, process: subprocess.Popen[bytes], timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"TigerFS exited before mount readiness: {process.returncode}")
        if os.path.ismount(path):
            return
        time.sleep(0.1)
    raise TimeoutError(f"TigerFS mount did not become ready: {path}")


def _wait_for_path(path: Path, timeout: float = 10) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return path
        time.sleep(0.05)
    raise TimeoutError(f"TigerFS path did not become ready: {path}")


def _wait_for_content(path: Path, expected: str, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if path.read_text() == expected:
                return
        except OSError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"TigerFS file did not reach expected content: {path}")


@pytest.mark.tigerfs_mount
def test_tigerfs_file_first_history_savepoint_and_undo_on_pg18(tmp_path: Path) -> None:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir()
    tigerfs = pgembed.POSTGRES_BIN_PATH / "tigerfs"
    with pgembed.get_server(
        tmp_path / "data",
        cleanup_mode="delete",
        shared_preload_libraries="timescaledb",
    ) as server:
        server.create_extension("timescaledb")
        process = subprocess.Popen(
            [str(tigerfs), "mount", "--foreground", server.get_uri("postgres"), str(mount_dir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            _wait_for_mount(mount_dir, process)

            workspace_name = "pg18_history_gate"
            workspace = mount_dir / workspace_name
            build_request = _wait_for_path(mount_dir / ".build") / workspace_name
            build_request.write_text("markdown,history")
            _wait_for_path(workspace)

            document = workspace / "result.md"
            document.write_text("# original\n")
            _wait_for_content(document, "# original\n")

            savepoint_name = "pg18-rc-savepoint"
            savepoint_root = _wait_for_path(workspace / ".savepoint")
            (savepoint_root / f"{savepoint_name}.json").write_text(
                '{"description":"PostgreSQL 18 release gate"}'
            )
            savepoint_id_path = _wait_for_path(
                savepoint_root / savepoint_name / "savepoint_id"
            )
            savepoint_id = savepoint_id_path.read_text().strip()
            assert savepoint_id
            assert uuid.UUID(savepoint_id).version == 7

            document.write_text("# provisional change\n")
            _wait_for_content(document, "# provisional change\n")

            undo_root = _wait_for_path(
                workspace / ".undo" / "to-savepoint" / savepoint_name
            )
            summary = _wait_for_path(undo_root / ".info" / "summary")
            assert summary.read_text().strip()
            (undo_root / ".apply").touch()
            _wait_for_content(document, "# original\n")
        finally:
            try:
                subprocess.run(
                    [str(tigerfs), "unmount", "--timeout", "5", str(mount_dir)],
                    check=False,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            if os.path.ismount(mount_dir):
                fallback = (
                    ["diskutil", "unmount", "force", str(mount_dir)]
                    if sys.platform == "darwin"
                    else ["fusermount", "-u", str(mount_dir)]
                )
                subprocess.run(fallback, check=False, timeout=10)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            assert not os.path.ismount(mount_dir)
