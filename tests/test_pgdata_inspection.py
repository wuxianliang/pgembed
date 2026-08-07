from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import pgembed.postgres_server as server_module
from pgembed.errors import (
    PostgresDataDirectoryInspectionError,
    PostgresDataDirectoryVersionError,
    PostgresStartupError,
    PostgresStartupTimeoutError,
)
from pgembed.postgres_server import PostgresServer, get_server, inspect_pgdata


def test_wrong_major_is_read_only(tmp_path: Path) -> None:
    pgdata = tmp_path / "data"
    pgdata.mkdir()
    version = pgdata / "PG_VERSION"
    version.write_text("17\n")
    before = version.stat()
    with pytest.raises(PostgresDataDirectoryVersionError) as caught:
        inspect_pgdata(pgdata, 18)
    assert caught.value.found_major == 17
    assert caught.value.expected_major == 18
    assert caught.value.pgdata == pgdata
    assert caught.value.migration_documentation.endswith("postgresql-17-to-18.md")
    assert version.read_text() == "17\n"
    assert version.stat().st_mtime_ns == before.st_mtime_ns
    assert list(pgdata.iterdir()) == [version]


@pytest.mark.parametrize("value", ["", "not-a-version", "18.4.1", "18.x"])
def test_invalid_pg_version_fails_closed(tmp_path: Path, value: str) -> None:
    pgdata = tmp_path / "data"
    pgdata.mkdir()
    (pgdata / "PG_VERSION").write_text(value)
    with pytest.raises(PostgresDataDirectoryInspectionError):
        inspect_pgdata(pgdata, 18)


def test_nonempty_directory_without_pg_version_fails_closed(tmp_path: Path) -> None:
    pgdata = tmp_path / "data"
    pgdata.mkdir()
    marker = pgdata / "user-file"
    marker.write_text("keep")
    with pytest.raises(PostgresDataDirectoryInspectionError, match="non-empty"):
        inspect_pgdata(pgdata, 18)
    assert marker.read_text() == "keep"


def test_nonexistent_and_empty_directories_are_fresh(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert inspect_pgdata(missing, 18) is True
    missing.mkdir()
    assert inspect_pgdata(missing, 18) is True


def test_get_server_rejects_pg17_before_any_mutation(monkeypatch, tmp_path: Path) -> None:
    pgdata = tmp_path / "data"
    pgdata.mkdir()
    (pgdata / "PG_VERSION").write_text("17\n")
    metadata = SimpleNamespace(postgres_major=18)
    monkeypatch.setattr(server_module, "require_bundle_metadata", lambda: metadata)
    monkeypatch.setattr(server_module, "validate_bundled_binaries", lambda value: value)

    def forbidden(*args, **kwargs):
        raise AssertionError("mutation/process helper was called before PGDATA validation")

    monkeypatch.setattr(server_module, "_get_command", forbidden)
    monkeypatch.setattr(server_module, "DiskList", forbidden)
    monkeypatch.setattr(server_module.atexit, "register", forbidden)
    if hasattr(server_module, "ensure_user_exists"):
        monkeypatch.setattr(server_module, "ensure_user_exists", forbidden)
        monkeypatch.setattr(server_module, "ensure_prefix_permissions", forbidden)
        monkeypatch.setattr(server_module, "ensure_folder_permissions", forbidden)
    with pytest.raises(PostgresDataDirectoryVersionError):
        get_server(pgdata)
    assert set(path.name for path in pgdata.iterdir()) == {"PG_VERSION"}
    assert pgdata not in PostgresServer._instances


def test_readiness_wait_has_bounded_timeout(monkeypatch, tmp_path: Path) -> None:
    pgdata = tmp_path / "data"
    pgdata.mkdir()
    instance = object.__new__(PostgresServer)
    instance.pgdata = pgdata
    instance.log = pgdata / "log"
    instance.system_user = None
    instance.runtime_path = tmp_path / "runtime"
    instance._postmaster_info = None
    instance._started_by_this_attempt = False
    monkeypatch.setattr(server_module, "POSTMASTER_READY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(server_module, "_get_command", lambda name: lambda *a, **k: "")
    monkeypatch.setattr(server_module.PostmasterInfo, "read_from_pgdata", lambda path: None)
    monkeypatch.setattr(server_module, "find_suitable_socket_dir", lambda *a: pgdata)
    monkeypatch.setattr(server_module.time, "sleep", lambda value: None)
    with pytest.raises(PostgresStartupTimeoutError) as caught:
        instance.ensure_postgres_running()
    assert caught.value.timeout_seconds == 0.01
    assert isinstance(caught.value, TimeoutError)
    assert caught.value.log_path == pgdata / "log"


def test_initdb_postcondition_rejects_wrong_major(monkeypatch, tmp_path: Path) -> None:
    pgdata = tmp_path / "data"
    pgdata.mkdir()
    instance = object.__new__(PostgresServer)
    instance.pgdata = pgdata
    instance.postgres_user = "postgres"
    instance.system_user = None
    instance._postgres_major = 18
    monkeypatch.setattr(server_module.psutil, "process_iter", lambda attrs: ())

    def fake_initdb(*args, **kwargs):
        (pgdata / "PG_VERSION").write_text("17\n")

    monkeypatch.setattr(server_module, "_get_command", lambda name: fake_initdb)
    with pytest.raises(PostgresDataDirectoryVersionError) as caught:
        instance.ensure_pgdata_inited(fresh=True)
    assert caught.value.found_major == 17
    assert caught.value.expected_major == 18


def test_readiness_fails_immediately_when_known_postmaster_exits(
    monkeypatch, tmp_path: Path
) -> None:
    pgdata = tmp_path / "data"
    pgdata.mkdir()
    instance = object.__new__(PostgresServer)
    instance.pgdata = pgdata
    instance.log = pgdata / "log"
    instance.system_user = None
    instance.runtime_path = tmp_path / "runtime"
    instance._postmaster_info = None
    instance._started_by_this_attempt = False
    dead_postmaster = SimpleNamespace(status="starting", is_running=lambda: False)
    reads = iter((None, dead_postmaster, dead_postmaster))
    monkeypatch.setattr(
        server_module.PostmasterInfo,
        "read_from_pgdata",
        lambda path: next(reads, dead_postmaster),
    )
    monkeypatch.setattr(server_module, "_get_command", lambda name: lambda *a, **k: "")
    monkeypatch.setattr(server_module, "find_suitable_socket_dir", lambda *a: pgdata)
    monkeypatch.setattr(
        server_module.time,
        "sleep",
        lambda value: (_ for _ in ()).throw(AssertionError("readiness should fail before sleeping")),
    )
    with pytest.raises(PostgresStartupError, match="exited before reaching ready"):
        instance.ensure_postgres_running()


def test_cleanup_with_other_process_handle_deregisters_local_state(
    monkeypatch, tmp_path: Path
) -> None:
    pgdata = tmp_path / "data"
    pgdata.mkdir()
    instance = object.__new__(PostgresServer)
    instance.pgdata = pgdata
    instance.cleanup_mode = "delete"
    instance._cleanup_complete = False
    instance._pid_registered = True
    instance._atexit_registered = True
    instance._instance_registered = True
    instance.global_process_id_list = SimpleNamespace(
        get_and_remove=lambda pid: [pid, pid + 1],
        put=lambda values: None,
    )
    monkeypatch.setattr(server_module.psutil, "pid_exists", lambda pid: True)
    PostgresServer._instances[pgdata] = instance
    monkeypatch.setattr(PostgresServer, "_lock", nullcontext())
    reaped: list[Path] = []
    unregistered: list[object] = []
    monkeypatch.setattr(
        PostgresServer,
        "_reap_started_postmaster",
        lambda self: reaped.append(self.pgdata),
    )
    monkeypatch.setattr(server_module.atexit, "unregister", unregistered.append)

    instance._cleanup()

    assert instance._cleanup_complete is True
    assert instance._pid_registered is False
    assert instance._atexit_registered is False
    assert pgdata not in PostgresServer._instances
    assert unregistered == [instance._cleanup]
    assert reaped == []
    assert pgdata.exists()


def test_cleanup_prunes_dead_process_handle_and_stops_last_live_handle(
    monkeypatch, tmp_path: Path
) -> None:
    pgdata = tmp_path / "data"
    pgdata.mkdir()
    instance = object.__new__(PostgresServer)
    instance.pgdata = pgdata
    instance.cleanup_mode = "delete"
    instance._cleanup_complete = False
    instance._pid_registered = True
    instance._atexit_registered = False
    instance._instance_registered = True
    persisted: list[list[int]] = []
    current_pid = server_module.os.getpid()
    instance.global_process_id_list = SimpleNamespace(
        get_and_remove=lambda pid: [999_999_999, current_pid],
        put=lambda values: persisted.append(values),
    )
    PostgresServer._instances[pgdata] = instance
    monkeypatch.setattr(PostgresServer, "_lock", nullcontext())
    monkeypatch.setattr(server_module.psutil, "pid_exists", lambda pid: False)
    reaped: list[Path] = []
    monkeypatch.setattr(
        PostgresServer,
        "_reap_started_postmaster",
        lambda self: reaped.append(self.pgdata),
    )

    instance._cleanup()

    assert persisted == [[]]
    assert reaped == [pgdata]
    assert not pgdata.exists()
    assert pgdata not in PostgresServer._instances


def test_constructor_reclassifies_pgdata_inside_process_lock(
    monkeypatch, tmp_path: Path
) -> None:
    pgdata = tmp_path / "data"
    metadata = SimpleNamespace(postgres_major=18)
    monkeypatch.setattr(server_module, "require_bundle_metadata", lambda: metadata)
    monkeypatch.setattr(server_module, "validate_bundled_binaries", lambda value: value)
    classifications = iter((True, True, False))
    monkeypatch.setattr(server_module, "inspect_pgdata", lambda *a: next(classifications))
    monkeypatch.setattr(PostgresServer, "_lock", nullcontext())
    monkeypatch.setattr(PostgresServer, "_prepare_mutable_runtime", lambda self, fresh: None)
    init_fresh: list[bool] = []
    monkeypatch.setattr(
        PostgresServer,
        "ensure_pgdata_inited",
        lambda self, fresh: init_fresh.append(fresh),
    )
    monkeypatch.setattr(PostgresServer, "ensure_shared_preload_libraries", lambda self: None)
    monkeypatch.setattr(PostgresServer, "ensure_postgres_running", lambda self: None)

    class FakeDiskList:
        def __init__(self, path):
            self.values: list[int] = []

        def get_and_add(self, value):
            old = self.values.copy()
            self.values.append(value)
            return old

        def get_and_remove(self, value):
            old = self.values.copy()
            self.values.remove(value)
            return old

        def put(self, values):
            self.values = list(values)

    monkeypatch.setattr(server_module, "DiskList", FakeDiskList)
    monkeypatch.setattr(server_module.atexit, "register", lambda callback: None)
    monkeypatch.setattr(server_module.atexit, "unregister", lambda callback: None)
    monkeypatch.setattr(PostgresServer, "_reap_started_postmaster", lambda self: None)

    server = get_server(pgdata, cleanup_mode=None)
    assert init_fresh == [False]
    server.cleanup()


def test_constructor_failure_allows_clean_retry_without_atexit_residue(
    monkeypatch, tmp_path: Path
) -> None:
    pgdata = tmp_path / "data"
    metadata = SimpleNamespace(postgres_major=18)
    monkeypatch.setattr(server_module, "require_bundle_metadata", lambda: metadata)
    monkeypatch.setattr(server_module, "validate_bundled_binaries", lambda value: value)
    monkeypatch.setattr(server_module, "inspect_pgdata", lambda *a: True)
    monkeypatch.setattr(PostgresServer, "_lock", nullcontext())
    monkeypatch.setattr(PostgresServer, "_prepare_mutable_runtime", lambda self, fresh: None)
    monkeypatch.setattr(PostgresServer, "ensure_pgdata_inited", lambda self, fresh: None)
    monkeypatch.setattr(PostgresServer, "ensure_shared_preload_libraries", lambda self: None)

    attempts = 0

    def start(self):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            self._started_by_this_attempt = True
            raise RuntimeError("synthetic startup failure")

    class FakeDiskList:
        def __init__(self, path):
            self.values: list[int] = []

        def get_and_add(self, value):
            previous = self.values.copy()
            if value not in self.values:
                self.values.append(value)
            return previous

        def get_and_remove(self, value):
            previous = self.values.copy()
            if value in self.values:
                self.values.remove(value)
            return previous

    registered: list[object] = []
    unregistered: list[object] = []
    reaped: list[Path] = []
    monkeypatch.setattr(PostgresServer, "ensure_postgres_running", start)
    monkeypatch.setattr(PostgresServer, "_reap_started_postmaster", lambda self: reaped.append(self.pgdata))
    monkeypatch.setattr(server_module, "DiskList", FakeDiskList)
    monkeypatch.setattr(server_module.atexit, "register", registered.append)
    monkeypatch.setattr(server_module.atexit, "unregister", unregistered.append)

    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        get_server(pgdata, cleanup_mode="delete")
    assert pgdata not in PostgresServer._instances
    assert registered == []
    assert unregistered == []
    assert reaped == [pgdata]

    server = get_server(pgdata, cleanup_mode="delete")
    assert PostgresServer._instances[pgdata] is server
    assert registered == [server._cleanup]
    server.cleanup()
    assert pgdata not in PostgresServer._instances
    assert unregistered == [server._cleanup]


def test_keyboard_interrupt_rolls_back_and_reaps(monkeypatch, tmp_path: Path) -> None:
    pgdata = tmp_path / "data"
    metadata = SimpleNamespace(postgres_major=18)
    monkeypatch.setattr(server_module, "require_bundle_metadata", lambda: metadata)
    monkeypatch.setattr(server_module, "validate_bundled_binaries", lambda value: value)
    monkeypatch.setattr(server_module, "inspect_pgdata", lambda *a: True)
    monkeypatch.setattr(PostgresServer, "_lock", nullcontext())
    monkeypatch.setattr(PostgresServer, "_prepare_mutable_runtime", lambda self, fresh: None)
    monkeypatch.setattr(PostgresServer, "ensure_pgdata_inited", lambda self, fresh: None)
    monkeypatch.setattr(PostgresServer, "ensure_shared_preload_libraries", lambda self: None)

    def interrupted(self):
        self._started_by_this_attempt = True
        raise KeyboardInterrupt

    reaped: list[Path] = []
    monkeypatch.setattr(PostgresServer, "ensure_postgres_running", interrupted)
    monkeypatch.setattr(PostgresServer, "_reap_started_postmaster", lambda self: reaped.append(self.pgdata))
    with pytest.raises(KeyboardInterrupt):
        PostgresServer(pgdata)
    assert reaped == [pgdata]
    assert pgdata not in PostgresServer._instances
