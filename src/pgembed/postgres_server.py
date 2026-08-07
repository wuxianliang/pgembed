from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Dict, Union
import atexit
import logging
import os
import platform
import shutil
import subprocess
import threading
import time

import psutil

from . import _commands
from ._bundle_metadata import require_bundle_metadata, validate_bundled_binaries
from ._commands import POSTGRES_BIN_PATH
from .errors import (
    PostgresDataDirectoryInspectionError,
    PostgresDataDirectoryVersionError,
    PostgresStartupError,
    PostgresStartupTimeoutError,
)
from .utils import DiskList, PostmasterInfo, find_suitable_port, find_suitable_socket_dir

if platform.system() != "Windows":
    from .utils import ensure_folder_permissions, ensure_prefix_permissions, ensure_user_exists

_logger = logging.getLogger("pgembed")

PG_CTL_START_TIMEOUT_SECONDS = 10
POSTMASTER_READY_TIMEOUT_SECONDS = 30
PG_CTL_STOP_TIMEOUT_SECONDS = 10
FAILED_START_REAP_TIMEOUT_SECONDS = 2
LOG_TAIL_MAX_BYTES = 64 * 1024
LOG_TAIL_MAX_LINES = 200
MIGRATION_DOCUMENTATION = "docs/migrations/postgresql-17-to-18.md"


def _get_command(name: str):
    cmd = getattr(_commands, name, None)
    if cmd is None:
        raise RuntimeError(
            "PostgreSQL binaries not available. pgembed was installed without "
            "PostgreSQL binaries or they were not built. Run 'make build'."
        )
    return cmd


def _parse_pg_version_major(text: str, pgdata: Path) -> int:
    value = text.strip()
    if not value:
        raise PostgresDataDirectoryInspectionError(
            pgdata, f"PG_VERSION in {pgdata} is empty; refusing to modify the directory"
        )
    parts = value.split(".")
    if not all(part.isdigit() for part in parts) or len(parts) > 2:
        raise PostgresDataDirectoryInspectionError(
            pgdata,
            f"PG_VERSION in {pgdata} is malformed ({value!r}); refusing to modify the directory",
        )
    return int(parts[0])


def inspect_pgdata(pgdata: Path, expected_major: int) -> bool:
    """Classify PGDATA without mutation; return True when initdb is required."""
    pgdata = Path(pgdata)
    version_path = pgdata / "PG_VERSION"
    try:
        version_exists = version_path.exists()
    except OSError as exc:
        raise PostgresDataDirectoryInspectionError(
            pgdata, f"cannot inspect {version_path}: {exc}"
        ) from exc

    if version_exists:
        try:
            text = version_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PostgresDataDirectoryInspectionError(
                pgdata, f"cannot read {version_path}: {exc}"
            ) from exc
        found_major = _parse_pg_version_major(text, pgdata)
        if found_major != expected_major:
            raise PostgresDataDirectoryVersionError(
                pgdata,
                found_major=found_major,
                expected_major=expected_major,
                pg_version_text=text.strip(),
                migration_documentation=MIGRATION_DOCUMENTATION,
            )
        return False

    if not pgdata.exists():
        return True
    if not pgdata.is_dir():
        raise PostgresDataDirectoryInspectionError(
            pgdata, f"PGDATA exists but is not a directory: {pgdata}"
        )
    try:
        nonempty = next(pgdata.iterdir(), None)
    except OSError as exc:
        raise PostgresDataDirectoryInspectionError(
            pgdata, f"cannot list PGDATA {pgdata}: {exc}"
        ) from exc
    if nonempty is not None:
        raise PostgresDataDirectoryInspectionError(
            pgdata,
            f"PGDATA {pgdata} is non-empty but has no PG_VERSION; refusing to modify it",
        )
    return True


def _read_log_tail(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL_MAX_BYTES))
            data = handle.read(LOG_TAIL_MAX_BYTES)
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-LOG_TAIL_MAX_LINES:])


class PostgresServer:
    """A process-safe handle for one embedded PostgreSQL data directory."""

    import fasteners
    import platformdirs

    _instances: Dict[Path, "PostgresServer"] = {}
    _instance_lock = threading.RLock()
    runtime_path: Path = platformdirs.user_runtime_path("python_PostgresServer")
    lock_path = runtime_path / ".lockfile"
    _lock = fasteners.InterProcessLock(lock_path)

    def __init__(
        self,
        pgdata: Path,
        *,
        cleanup_mode: Optional[str] = "stop",
        shared_preload_libraries: Optional[Union[str, Iterable[str]]] = None,
    ):
        if cleanup_mode not in (None, "stop", "delete"):
            raise ValueError("cleanup_mode must be None, 'stop', or 'delete'")

        metadata = validate_bundled_binaries(require_bundle_metadata())
        fresh = inspect_pgdata(pgdata, metadata.postgres_major)

        self.pgdata = Path(pgdata)
        self.log = self.pgdata / "log"
        self._postgres_major = metadata.postgres_major
        self.system_user: Optional[str] = None
        self.postgres_user = "postgres"
        self.cleanup_mode = cleanup_mode
        self.shared_preload_libraries = self._normalize_preload_libraries(shared_preload_libraries)
        self._postmaster_info: Optional[PostmasterInfo] = None
        self._count = 0
        self._cleanup_complete = False
        self._pid_registered = False
        self._atexit_registered = False
        self._instance_registered = False
        self._started_by_this_attempt = False
        self.global_process_id_list: Optional[DiskList] = None

        with self._instance_lock:
            try:
                existing = self._instances.get(self.pgdata)
                if existing is not None and existing is not self:
                    raise RuntimeError(
                        f"A PostgreSQL handle already exists for {self.pgdata}; use get_server()"
                    )
                with self._lock:
                    # The pre-lock classification is the fail-fast boundary. Repeat it while
                    # holding the process lock so another process cannot initialize PGDATA
                    # between classification and the first mutation.
                    fresh = inspect_pgdata(self.pgdata, self._postgres_major)
                    self._prepare_mutable_runtime(fresh=fresh)
                    self.ensure_pgdata_inited(fresh=fresh)
                    self.ensure_shared_preload_libraries()
                    self.ensure_postgres_running()
                    self.global_process_id_list = DiskList(self.pgdata / ".handle_pids.json")
                    self.global_process_id_list.get_and_add(os.getpid())
                    self._pid_registered = True
                    self._instances[self.pgdata] = self
                    self._instance_registered = True
                    atexit.register(self._cleanup)
                    self._atexit_registered = True
            except BaseException:
                self._rollback_failed_construction()
                raise

    def _prepare_mutable_runtime(self, *, fresh: bool) -> None:
        if fresh and not self.pgdata.exists():
            self.pgdata.mkdir(parents=False, exist_ok=False)
        if platform.system() != "Windows" and os.geteuid() == 0:
            import pwd

            self.system_user = "pgembed"
            ensure_user_exists(self.system_user)
            ensure_prefix_permissions(self.pgdata)
            ensure_prefix_permissions(POSTGRES_BIN_PATH)
            import stat

            read_perm = stat.S_IRGRP | stat.S_IROTH
            execute_perm = stat.S_IXGRP | stat.S_IXOTH
            ensure_folder_permissions(POSTGRES_BIN_PATH, execute_perm | read_perm)
            ensure_folder_permissions(POSTGRES_BIN_PATH.parent / "lib", read_perm)
            entry = pwd.getpwnam(self.system_user)
            os.chown(self.pgdata, entry.pw_uid, entry.pw_gid)

    def _rollback_failed_construction(self) -> None:
        try:
            with self._lock:
                if self._pid_registered and self.global_process_id_list is not None:
                    try:
                        self.global_process_id_list.get_and_remove(os.getpid())
                    except Exception:
                        _logger.exception("failed to roll back PostgreSQL handle PID")
                    self._pid_registered = False
                if self._instance_registered:
                    if self._instances.get(self.pgdata) is self:
                        self._instances.pop(self.pgdata, None)
                    self._instance_registered = False
                if self._atexit_registered:
                    atexit.unregister(self._cleanup)
                    self._atexit_registered = False
                if self._started_by_this_attempt:
                    self._reap_started_postmaster()
        except Exception:
            _logger.exception("failed to fully roll back PostgreSQL construction")

    def get_postmaster_info(self) -> PostmasterInfo:
        if self._postmaster_info is None:
            raise RuntimeError("PostgreSQL server is not running")
        return self._postmaster_info

    def get_pid(self) -> Optional[int]:
        return self.get_postmaster_info().pid

    def get_uri(self, database: Optional[str] = None) -> str:
        return self.get_postmaster_info().get_uri(database=database)

    @staticmethod
    def _normalize_preload_libraries(
        libraries: Optional[Union[str, Iterable[str]]],
    ) -> tuple[str, ...]:
        if libraries is None:
            return ()
        if isinstance(libraries, str):
            libraries = libraries.split(",")
        return tuple(lib.strip() for lib in libraries if lib.strip())

    def ensure_shared_preload_libraries(self) -> None:
        if not self.shared_preload_libraries:
            return
        conf_file = self.pgdata / "postgresql.conf"
        conf_text = conf_file.read_text(encoding="utf-8")
        existing: list[str] = []
        lines = conf_text.splitlines()
        setting_idx: Optional[int] = None
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith("shared_preload_libraries"):
                continue
            setting_idx = idx
            _, value = stripped.split("=", 1)
            existing = [
                lib.strip().strip("'\"")
                for lib in value.split("#", 1)[0].split(",")
                if lib.strip().strip("'\"")
            ]
            break
        preload_libraries = list(dict.fromkeys([*existing, *self.shared_preload_libraries]))
        preload_line = "shared_preload_libraries = '" + ",".join(preload_libraries) + "'"
        if setting_idx is None:
            lines.append(preload_line)
        else:
            lines[setting_idx] = preload_line
        conf_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def ensure_pgdata_inited(self, *, fresh: bool) -> None:
        if not fresh:
            _logger.info("PG_VERSION file found and matches bundled PostgreSQL")
            return
        _logger.info("Initializing fresh PGDATA at %s", self.pgdata)
        for proc in psutil.process_iter(attrs=["name", "cmdline"]):
            try:
                if proc.info["name"] == "postgres" and proc.info["cmdline"] is not None:
                    if str(self.pgdata) in proc.info["cmdline"]:
                        proc.terminate()
                        try:
                            proc.wait(FAILED_START_REAP_TIMEOUT_SECONDS)
                        except psutil.TimeoutExpired:
                            proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        initdb = _get_command("initdb")
        initdb(
            ["--auth=trust", "--auth-local=trust", "--encoding=utf8", "-U", self.postgres_user],
            pgdata=self.pgdata,
            user=self.system_user,
        )
        if inspect_pgdata(self.pgdata, self._postgres_major):
            raise PostgresDataDirectoryInspectionError(
                self.pgdata,
                "initdb completed without creating a valid PG_VERSION; refusing to continue",
            )

    def _startup_error(self, message: str, *, timeout: Optional[float] = None) -> PostgresStartupError:
        pinfo = None
        try:
            pinfo = PostmasterInfo.read_from_pgdata(self.pgdata)
        except Exception:
            pass
        kwargs = dict(
            pgdata=self.pgdata,
            log_path=self.log,
            log_tail=_read_log_tail(self.log),
            postmaster_status=getattr(pinfo, "status", None),
        )
        if timeout is not None:
            return PostgresStartupTimeoutError(message, timeout_seconds=timeout, **kwargs)
        return PostgresStartupError(message, **kwargs)

    def ensure_postgres_running(self) -> None:
        try:
            postmaster_info = PostmasterInfo.read_from_pgdata(self.pgdata)
        except (OSError, ValueError, AssertionError):
            postmaster_info = None
        if postmaster_info is not None and postmaster_info.is_running():
            self._postmaster_info = postmaster_info
        else:
            if platform.system() != "Windows":
                socket_dir = find_suitable_socket_dir(self.pgdata, self.runtime_path)
                if self.system_user is not None and socket_dir != self.pgdata:
                    ensure_prefix_permissions(socket_dir)
                    socket_dir.chmod(0o777)
                pg_ctl_args = ["-w", "-o", '-h ""', "-o", f"-k {socket_dir}", "-l", str(self.log), "start"]
            else:
                socket_dir = None
                host = "127.0.0.1"
                port = find_suitable_port(host)
                pg_ctl_args = ["-w", "-o", f'-h "{host}"', "-o", f"-p {port}", "-l", str(self.log), "start"]

            pg_ctl = _get_command("pg_ctl")
            self._started_by_this_attempt = True
            try:
                pg_ctl(
                    pg_ctl_args,
                    pgdata=self.pgdata,
                    user=self.system_user,
                    timeout=PG_CTL_START_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                raise self._startup_error(
                    "pg_ctl timed out while starting PostgreSQL",
                    timeout=PG_CTL_START_TIMEOUT_SECONDS,
                ) from exc
            except subprocess.CalledProcessError as exc:
                raise self._startup_error("pg_ctl failed while starting PostgreSQL") from exc
            except OSError as exc:
                raise self._startup_error("could not execute pg_ctl while starting PostgreSQL") from exc

            deadline = time.monotonic() + POSTMASTER_READY_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                try:
                    pinfo = PostmasterInfo.read_from_pgdata(self.pgdata)
                except (OSError, ValueError, AssertionError):
                    pinfo = None
                if pinfo is not None:
                    if not pinfo.is_running():
                        raise self._startup_error(
                            "PostgreSQL postmaster exited before reaching ready state"
                        )
                    if pinfo.status == "ready":
                        self._postmaster_info = pinfo
                        break
                time.sleep(0.1)
            else:
                raise self._startup_error(
                    "PostgreSQL did not reach ready state before the deadline",
                    timeout=POSTMASTER_READY_TIMEOUT_SECONDS,
                )

        if (
            self._postmaster_info is None
            or not self._postmaster_info.is_running()
            or self._postmaster_info.status != "ready"
        ):
            raise self._startup_error("PostgreSQL did not reach ready state")

    def _reap_started_postmaster(self) -> None:
        # pg_ctl can stop a postmaster by PGDATA even while postmaster.pid is
        # absent or only partially written, so always try it before PID parsing.
        try:
            pg_ctl = _get_command("pg_ctl")
            pg_ctl(
                ["-w", "stop"],
                pgdata=self.pgdata,
                user=self.system_user,
                timeout=PG_CTL_STOP_TIMEOUT_SECONDS,
            )
            self._postmaster_info = None
            self._started_by_this_attempt = False
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            pass

        pinfo = self._postmaster_info
        if pinfo is None:
            try:
                pinfo = PostmasterInfo.read_from_pgdata(self.pgdata)
            except Exception:
                pinfo = None
        if pinfo is None or not pinfo.is_running():
            return
        process = pinfo.process
        if process is None:
            return
        try:
            process.terminate()
            process.wait(FAILED_START_REAP_TIMEOUT_SECONDS)
        except psutil.TimeoutExpired:
            try:
                process.kill()
                process.wait(FAILED_START_REAP_TIMEOUT_SECONDS)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass
        except psutil.NoSuchProcess:
            pass
        finally:
            self._postmaster_info = None
            self._started_by_this_attempt = False

    def _cleanup(self) -> None:
        with self._lock:
            if self._cleanup_complete:
                return

            is_last_process_handle = False
            if self._pid_registered and self.global_process_id_list is not None:
                current_pid = os.getpid()
                previous = self.global_process_id_list.get_and_remove(current_pid)
                self._pid_registered = False
                remaining = [
                    pid for pid in previous if pid != current_pid and psutil.pid_exists(pid)
                ]
                recorded_remaining = [pid for pid in previous if pid != current_pid]
                if remaining != recorded_remaining:
                    self.global_process_id_list.put(remaining)
                is_last_process_handle = current_pid in previous and not remaining

            if self._instances.get(self.pgdata) is self:
                self._instances.pop(self.pgdata, None)
            self._instance_registered = False
            if self._atexit_registered:
                atexit.unregister(self._cleanup)
                self._atexit_registered = False

            if is_last_process_handle and self.cleanup_mode in ("stop", "delete"):
                self._reap_started_postmaster()
            if is_last_process_handle and self.cleanup_mode == "delete":
                shutil.rmtree(str(self.pgdata), ignore_errors=True)
            self._cleanup_complete = True

    def psql(self, command: str) -> str:
        executable = POSTGRES_BIN_PATH / "psql"
        return subprocess.check_output(
            [
                str(executable),
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                self.get_uri(),
            ],
            input=command.encode(),
        ).decode("utf-8")

    def create_extension(self, extension_name: str) -> str:
        import pgembed
        from pgembed import AVAILABLE_EXTENSIONS, get_extension_create_name

        extension_map = {
            "vector": "pgvector",
            "pg_duckdb": "pg_duckdb",
            "vchord": "vectorchord",
            "age": "age",
            "psql_bm25s": "psql_bm25s",
            "timescaledb": "timescaledb",
            "pg_cron": "pg_cron",
            "pg_net": "pg_net",
        }
        package_name = extension_map.get(extension_name, extension_name)
        if not pgembed.has_extension(package_name):
            available = [key for key, value in AVAILABLE_EXTENSIONS.items() if value]
            raise RuntimeError(
                f"Extension {extension_name!r} is not available. Available extensions: {available}"
            )
        for predecessor in pgembed.EXTENSION_PRECEDENCE.get(package_name, ()):
            if pgembed.has_extension(predecessor):
                self.psql(
                    f"CREATE EXTENSION IF NOT EXISTS {get_extension_create_name(predecessor)};"
                )
        return self.psql(
            f"CREATE EXTENSION IF NOT EXISTS {get_extension_create_name(package_name)};"
        )

    def age_setup(self, conn=None):
        import psycopg2

        close_conn = conn is None
        if conn is None:
            conn = psycopg2.connect(self.get_uri())
        try:
            with conn.cursor() as cursor:
                cursor.execute("LOAD 'age';")
                cursor.execute('SET search_path = ag_catalog, "$user", public;')
                try:
                    cursor.execute("SELECT * FROM ag_catalog.create_graph('my_graph');")
                except Exception:
                    conn.rollback()
            conn.commit()
        finally:
            if close_conn:
                conn.close()

    def age_query(self, query: str, graph_name: str = "my_graph") -> list:
        import psycopg2

        conn = psycopg2.connect(self.get_uri())
        try:
            self.age_setup(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM ag_catalog.cypher(%s, %s) AS (result agtype);",
                    (graph_name, query),
                )
                return cursor.fetchall()
        finally:
            conn.close()

    def __enter__(self):
        self._count += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._count -= 1
        if self._count <= 0:
            self._cleanup()

    def cleanup(self) -> None:
        self._cleanup()


def get_server(
    pgdata: Union[Path, str],
    cleanup_mode: Optional[str] = "stop",
    shared_preload_libraries: Optional[Union[str, Iterable[str]]] = None,
) -> PostgresServer:
    """Return a handle after attesting the bundle and read-only inspecting PGDATA."""
    metadata = validate_bundled_binaries(require_bundle_metadata())
    path = Path(pgdata).expanduser().resolve()
    if not path.parent.exists():
        raise FileNotFoundError(f"Parent directory of pgdata does not exist: {path.parent}")
    inspect_pgdata(path, metadata.postgres_major)
    with PostgresServer._instance_lock:
        existing = PostgresServer._instances.get(path)
        if existing is not None:
            return existing
        return PostgresServer(
            path,
            cleanup_mode=cleanup_mode,
            shared_preload_libraries=shared_preload_libraries,
        )
