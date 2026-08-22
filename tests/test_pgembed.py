import pytest
import pgembed
import subprocess
import tempfile
from typing import Optional, Union
import multiprocessing as mp
import shutil
from pathlib import Path
import pgembed.utils
import socket
from pgembed.utils import find_suitable_port, process_is_running
import psutil
import platform
import sqlalchemy as sa
import datetime
from sqlalchemy_utils import database_exists, create_database
import logging
import os
import time

from pgembed._bundle_metadata import require_bundle_metadata

pytestmark = pytest.mark.integration


def _check_sqlalchemy_works(srv : pgembed.PostgresServer):
    database_name = 'testdb'
    uri = srv.get_uri(database_name)

    if not database_exists(uri):
        create_database(uri)

    engine = sa.create_engine(uri)
    conn = engine.connect()

    table_name = 'table_foo'
    with conn.begin():
        # if table exists already, drop it
        if engine.dialect.has_table(conn, table_name):
            conn.execute(sa.text(f"drop table {table_name};"))
        conn.execute(sa.text(f"create table {table_name} (id int);"))
        conn.execute(sa.text(f"insert into {table_name} values (1);"))
        cur = conn.execute(sa.text(f"select * from {table_name};"))
        result = cur.fetchone()
        assert result
        assert result[0] == 1

def _check_postmaster_info(pgdata : Path, postmaster_info : pgembed.utils.PostmasterInfo):
    assert postmaster_info is not None
    assert postmaster_info.pgdata is not None
    assert postmaster_info.pgdata == pgdata

    assert postmaster_info.is_running()

    if postmaster_info.socket_dir is not None:
        assert postmaster_info.socket_dir.exists()
        assert postmaster_info.socket_path is not None
        assert postmaster_info.socket_path.exists()
        assert postmaster_info.socket_path.is_socket()


def _check_no_default_age_preload(pg : pgembed.PostgresServer) -> None:
    conf = pg.pgdata / 'postgresql.conf'
    assert "shared_preload_libraries = 'age'" not in conf.read_text()

    ret = pg.psql("show shared_preload_libraries;")
    # parse second row (first two are headers)
    assert ret.splitlines()[2].strip() == ''


def _require_extension(name: str) -> None:
    if not pgembed.has_extension(name):
        pytest.skip(f"{name} is not installed in this build")


VECTORCHORD_VARIANTS = (
    ("vectorchord", "vchord"),
)


def _assert_vectorchord_artifacts_packaged(extension_key: str, sql_name: str) -> None:
    """Check documented/built/packaged artifacts before creatable smoke tests."""
    library_path = pgembed.get_extension_path(extension_key)
    if library_path is None:
        pytest.skip(f"{extension_key} is not installed in this build")

    assert library_path.exists(), f"{extension_key} library is not packaged: {library_path}"
    assert library_path.name.startswith(f"{sql_name}."), (
        f"{extension_key} library should use SQL/library stem {sql_name}: {library_path}"
    )

    control_path = pgembed.EXTENSION_SHARE_PATH / f"{sql_name}.control"
    assert control_path.exists(), (
        f"{extension_key} is detected as built, but control file is not packaged: {control_path}"
    )

    default_version = pgembed._read_extension_default_version(sql_name)
    assert default_version, f"{extension_key} control file has no default_version"
    install_sql_path = pgembed.EXTENSION_SHARE_PATH / f"{sql_name}--{default_version}.sql"
    assert pgembed.get_extension_install_sql_path(extension_key) == install_sql_path
    assert install_sql_path.exists(), (
        f"{extension_key} is detected as built, but default-version install SQL script "
        f"is not packaged for creatable extension {sql_name}: {install_sql_path}"
    )
    assert pgembed.has_extension(extension_key), (
        f"{extension_key} has library/control/SQL artifacts, but pgembed discovery "
        "does not report it as creatable"
    )


@pytest.mark.parametrize("extension_key,sql_name", VECTORCHORD_VARIANTS)
def test_vectorchord_variant_discovery_and_create_names(extension_key: str, sql_name: str):
    assert pgembed.get_extension_create_name(extension_key) == sql_name
    if pgembed.has_extension(extension_key):
        path = pgembed.get_extension_path(extension_key)
        assert path is not None
        assert path.name.startswith(f"{sql_name}.")


@pytest.mark.parametrize("extension_key,sql_name", VECTORCHORD_VARIANTS)
def test_vectorchord_variant_artifacts_packaged_for_creatable_extensions(
    extension_key: str, sql_name: str
):
    _assert_vectorchord_artifacts_packaged(extension_key, sql_name)


@pytest.mark.parametrize(
    "extension_name,expected_sql",
    (
        ("vchord", "CREATE EXTENSION IF NOT EXISTS vchord;"),
        ("vectorchord", "CREATE EXTENSION IF NOT EXISTS vchord;"),
    ),
)
def test_vectorchord_create_extension_names(monkeypatch, extension_name: str, expected_sql: str):
    monkeypatch.setitem(pgembed.AVAILABLE_EXTENSIONS, "vectorchord", True)
    pg = pgembed.PostgresServer.__new__(pgembed.PostgresServer)
    commands = []

    def fake_psql(command: str) -> str:
        commands.append(command)
        return "CREATE EXTENSION\n"

    monkeypatch.setattr(pg, "psql", fake_psql)
    assert pg.create_extension(extension_name).strip() == "CREATE EXTENSION"
    assert commands == [expected_sql]


@pytest.fixture
def tmp_postgres_vchord():
    _require_extension("vectorchord")
    tmp_pg_data = tempfile.mkdtemp()
    with pgembed.get_server(
        tmp_pg_data,
        cleanup_mode='delete',
        shared_preload_libraries='vchord',
    ) as pg:
        yield pg


@pytest.fixture
def tmp_postgres_timescaledb():
    _require_extension("timescaledb")
    tmp_pg_data = tempfile.mkdtemp()
    with pgembed.get_server(
        tmp_pg_data,
        cleanup_mode='delete',
        shared_preload_libraries='timescaledb',
    ) as pg:
        yield pg


def _check_server(
    pg: pgembed.PostgresServer,
    expected_preload_libraries: Optional[tuple[str, ...]] = None,
) -> int:
    assert pg.pgdata.exists()
    postmaster_info = pgembed.utils.PostmasterInfo.read_from_pgdata(pg.pgdata)
    assert postmaster_info is not None
    assert postmaster_info.pid is not None
    _check_postmaster_info(pg.pgdata, postmaster_info)

    ret = pg.psql("show data_directory;")
    # parse second row (first two are headers)
    ret_path = Path(ret.splitlines()[2].strip())
    assert pg.pgdata == ret_path
    if expected_preload_libraries is None:
        _check_no_default_age_preload(pg)
    else:
        configured = pg.psql("show shared_preload_libraries;").splitlines()[2].strip()
        actual = tuple(item.strip() for item in configured.split(",") if item.strip())
        assert actual == expected_preload_libraries
    _check_sqlalchemy_works(pg)
    return postmaster_info.pid

def _kill_server(pid : Union[int,psutil.Process,None]) -> None:
    if pid is None:
        return
    elif isinstance(pid, psutil.Process):
        proc = pid
    else:
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return

    if proc.is_running():
        proc.terminate() # attempt cleaner shutdown
        try:
            proc.wait(3) # wait at most a few seconds
        except psutil.TimeoutExpired:
            pass

        if proc.is_running():
            proc.kill()

def test_get_port():
    address = '127.0.0.1'
    port = find_suitable_port(address)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        sock.bind((address, port))
    except OSError as err:
        if 'Address already in use' in str(err):
            raise RuntimeError(f"Port {port} is already in use.")
        raise err
    finally:
        sock.close()

def test_get_server():
    with tempfile.TemporaryDirectory() as tmpdir:
        pid = None
        try:
            # check case when initializing the pgdata dir
            with pgembed.get_server(tmpdir) as pg:
                pid = _check_server(pg)

            assert not process_is_running(pid)
            assert pg.pgdata.exists()

            # check case when pgdata dir is already initialized
            with pgembed.get_server(tmpdir) as pg:
                pid = _check_server(pg)

            assert not process_is_running(pid)
            assert pg.pgdata.exists()
        finally:
            _kill_server(pid)

def test_reentrant():
    with tempfile.TemporaryDirectory() as tmpdir:
        pid = None
        try:
            with pgembed.get_server(tmpdir) as pg:
                pid = _check_server(pg)
                with pgembed.get_server(tmpdir) as pg2:
                    assert pg2 is pg
                    _check_server(pg)

                _check_server(pg)

            assert not process_is_running(pid)
            assert pg.pgdata.exists()
        finally:
            _kill_server(pid)

def _start_server_in_separate_process(pgdata, queue_in : Optional[mp.Queue], queue_out : mp.Queue, cleanup_mode : Optional[str]):
    with pgembed.get_server(pgdata, cleanup_mode=cleanup_mode) as pg:
        pid = _check_server(pg)
        queue_out.put(pid)

        if queue_in is not None:
            _ = queue_in.get() # wait for signal
            return

def test_unix_domain_socket():
    if platform.system() == 'Windows':
        pytest.skip("This test is for unix domain sockets, which are not available on Windows.")

    long_prefix = '_'.join(['long'] + ['1234567890']*12)
    assert len(long_prefix) > 120
    prefixes = ['short', long_prefix]

    for prefix in prefixes:
        with tempfile.TemporaryDirectory(dir='/tmp/', prefix=prefix) as tmpdir:
            pid = None
            try:
                with pgembed.get_server(tmpdir) as pg:
                    pid = _check_server(pg)
                    uri = pg.get_uri()

                assert not process_is_running(pid)
                assert pg.pgdata.exists()
                if len(prefix) > 120:
                    assert str(tmpdir) not in uri
                else:
                    assert str(tmpdir) in uri
            finally:
                _kill_server(pid)

def test_pg_ctl():
    if platform.system() != 'Windows' and os.geteuid() == 0:
        # on Linux root, this test would fail.
        # we'd need to create a user etc to run the command, which is not worth it
        # pgembed does this internally, but not worth it for this test
        pytest.skip("This test is not run as root on Linux.")

    with tempfile.TemporaryDirectory() as tmpdir:
        pid = None
        try:
            with pgembed.get_server(tmpdir) as pg:
                output = pgembed.pg_ctl(['status'], str(pg.pgdata))
                assert 'server is running' in output.splitlines()[0]

        finally:
            _kill_server(pid)

def test_stale_postmaster():
    """  To simulate a stale postmaster.pid file, we create a postmaster.pid file by starting a server,
        back the file up, then restore the backup to the original location after killing the server.
        ( our method to kill the server is graceful to avoid running out of shmem, but this seems to also
            remove the postmaster.pid file, so we need to go to these lengths to simulate a stale postmaster.pid file )
    """
    if platform.system() != 'Windows' and os.geteuid() == 0:
        # on Linux as root, this test fails bc of permissions for the postmaster.pid file
        # we simply skip it in this case, as in practice, the permissions issue would not occur
        pytest.skip("This test is not run as root on Linux.")

    with tempfile.TemporaryDirectory() as tmpdir:
        pid = None
        pid2 = None

        try:
            with pgembed.get_server(tmpdir, cleanup_mode='stop') as pg:
                pid = _check_server(pg)
                pgdata = pg.pgdata
                postmaster_pid = pgdata / 'postmaster.pid'

                ## make a backup of the postmaster.pid file
                shutil.copy2(str(postmaster_pid), str(postmaster_pid) + '.bak')

            # restore the backup to gurantee a stale postmaster.pid file
            shutil.copy2(str(postmaster_pid) + '.bak', str(postmaster_pid))
            with pgembed.get_server(tmpdir) as pg:
                pid2 = _check_server(pg)
        finally:
            _kill_server(pid)
            _kill_server(pid2)


def test_cleanup_delete():
    with tempfile.TemporaryDirectory() as tmpdir:
        pid = None
        try:
            with pgembed.get_server(tmpdir, cleanup_mode='delete') as pg:
                pid = _check_server(pg)

            assert not process_is_running(pid)
            assert not pg.pgdata.exists()
        finally:
            _kill_server(pid)

def test_cleanup_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        pid = None
        try:
            with pgembed.get_server(tmpdir, cleanup_mode=None) as pg:
                pid = _check_server(pg)

            assert process_is_running(pid)
            assert pg.pgdata.exists()
        finally:
            _kill_server(pid)

@pytest.fixture
def tmp_postgres():
    tmp_pg_data = tempfile.mkdtemp()
    with pgembed.get_server(tmp_pg_data, cleanup_mode='delete') as pg:
        yield pg


def test_pgvector(tmp_postgres):
    _require_extension("pgvector")
    ret = tmp_postgres.psql("CREATE EXTENSION vector;")
    assert ret.strip() == "CREATE EXTENSION"


def test_age(tmp_postgres):
    _require_extension("age")
    assert tmp_postgres.psql("CREATE EXTENSION age;").strip() == "CREATE EXTENSION"
    assert 't' in tmp_postgres.psql(
        "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'agtype');"
    )
    assert 't' in tmp_postgres.psql(
        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'ag_catalog');"
    )
    graph_output = tmp_postgres.psql(
        "LOAD 'age'; SET search_path = ag_catalog, \"$user\", public; SELECT create_graph('my_graph');"
    )
    assert 'create_graph' in graph_output or 'NOTICE' in graph_output
    assert 't' in tmp_postgres.psql(
        "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'my_graph');"
    )


def test_psql_bm25s(tmp_postgres):
    _require_extension("psql_bm25s")
    assert tmp_postgres.psql("CREATE EXTENSION psql_bm25s;").strip() == "CREATE EXTENSION"
    assert 't' in tmp_postgres.psql(
        "SELECT EXISTS (SELECT 1 FROM pg_am WHERE amname = 'psql_bm25s');"
    )
    assert 't' in tmp_postgres.psql(
        "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'psql_bm25s_result_hit');"
    )
    bm25_output = tmp_postgres.psql(
        """
        DROP TABLE IF EXISTS bm25_smoke;
        CREATE TABLE bm25_smoke (id integer, body text);
        INSERT INTO bm25_smoke VALUES
          (1, 'hello vector search'),
          (2, 'postgres bm25 search'),
          (3, 'graph query age');
        CREATE INDEX bm25_smoke_idx ON bm25_smoke USING psql_bm25s (body);
        SELECT doc_id, score FROM psql_bm25s_query('bm25_smoke_idx', 'search', 2);
        """
    )
    assert 'CREATE INDEX' in bm25_output
    assert 'doc_id' in bm25_output
    assert '(2 rows)' in bm25_output


def _psql_tuples(pg: pgembed.PostgresServer, query: str) -> list[tuple]:
    """Run SQL through psycopg2 for structured assertions."""
    import psycopg2

    conn = psycopg2.connect(pg.get_uri())
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _start_vectorchord_variant(extension_key: str, preload_name: str):
    _require_extension(extension_key)
    tmp_pg_data = tempfile.mkdtemp()
    return pgembed.get_server(
        tmp_pg_data,
        cleanup_mode='delete',
        shared_preload_libraries=preload_name,
    )


def _run_vectorchord_variant_smoke(extension_key: str, sql_name: str) -> dict:
    _assert_vectorchord_artifacts_packaged(extension_key, sql_name)
    results = {
        "extension_key": extension_key,
        "extension_name": sql_name,
        "preload_libraries": sql_name,
    }

    with _start_vectorchord_variant(extension_key, sql_name) as pg:
        assert pg.psql("CREATE EXTENSION vector;").strip() == "CREATE EXTENSION"
        extension_output = pg.create_extension(extension_key)
        assert "CREATE EXTENSION" in extension_output or extension_output.strip() == ""

        preload_rows = _psql_tuples(pg, "SHOW shared_preload_libraries;")
        results["preload_libraries"] = preload_rows[0][0]
        assert results["preload_libraries"] == sql_name

        access_methods = [
            row[0]
            for row in _psql_tuples(
                pg,
                "SELECT amname FROM pg_am WHERE amname IN ('vchordg', 'vchordrq') ORDER BY 1;",
            )
        ]
        assert access_methods == ["vchordg", "vchordrq"]
        results["access_methods"] = access_methods

        assert (
            pg.psql(
                """
                DROP TABLE IF EXISTS vchord_smoke;
                CREATE TABLE vchord_smoke (id integer PRIMARY KEY, emb vector(3));
                INSERT INTO vchord_smoke VALUES
                  (1, '[1,0,0]'),
                  (2, '[0,1,0]'),
                  (3, '[0,0,1]'),
                  (4, '[0.9,0.1,0]'),
                  (5, '[0.1,0.9,0]'),
                  (6, '[0,0.1,0.9]');
                """
            ).strip()
            != ""
        )
        expected_order = [1, 4, 5]
        index_results = {}
        for access_method in ("vchordg", "vchordrq"):
            index_name = f"vchord_smoke_{access_method}_idx"
            index_output = pg.psql(
                f"CREATE INDEX {index_name} ON vchord_smoke USING {access_method} (emb vector_l2_ops);"
            )
            assert "CREATE INDEX" in index_output

            plan = pg.psql(
                """
                SET enable_seqscan TO off;
                EXPLAIN (COSTS OFF)
                SELECT id FROM vchord_smoke ORDER BY emb <-> '[1,0,0]' LIMIT 3;
                """
            )
            assert f"Index Scan using {index_name}" in plan

            order = [
                row[0]
                for row in _psql_tuples(
                    pg,
                    "SELECT id FROM vchord_smoke ORDER BY emb <-> '[1,0,0]' LIMIT 3;",
                )
            ]
            assert order == expected_order
            index_results[access_method] = {
                "order": order,
                "uses_index": f"Index Scan using {index_name}" in plan,
            }
            pg.psql(f"DROP INDEX {index_name};")
        results["index_results"] = index_results

    return results


@pytest.mark.parametrize("extension_key,sql_name", VECTORCHORD_VARIANTS)
def test_vectorchord_variant_preload_sql_api_and_indexes(extension_key: str, sql_name: str):
    _run_vectorchord_variant_smoke(extension_key, sql_name)


def test_timescaledb_preload(tmp_postgres_timescaledb):
    _require_extension("timescaledb")
    assert (
        tmp_postgres_timescaledb.create_extension("timescaledb").strip()
        == "CREATE EXTENSION"
    )
    hypertable_output = tmp_postgres_timescaledb.psql(
        """
        DROP TABLE IF EXISTS timescale_smoke;
        CREATE TABLE timescale_smoke (time timestamptz NOT NULL, value double precision);
        SELECT create_hypertable('timescale_smoke', 'time');
        SELECT hypertable_name
          FROM timescaledb_information.hypertables
         WHERE hypertable_name = 'timescale_smoke';
        """
    )
    assert 'timescale_smoke' in hypertable_output


def test_start_failure_includes_log_tail():
    """Structured startup failures include the PostgreSQL log tail."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with pgembed.get_server(tmpdir) as _:
            pass

        ## now delete some files to make it fail
        for f in Path(tmpdir).glob('**/postgresql.conf'):
            f.unlink()

        with pytest.raises(pgembed.PostgresStartupError) as exc_info:
            with pgembed.get_server(tmpdir) as _:
                pass

        assert 'postgres: could not access the server configuration file' in str(exc_info.value)


def test_no_conflict():
    """ test we can start pgembeds on two different datadirs with no conflict (eg port conflict)
    """
    pid1 = None
    pid2 = None
    try:
        with tempfile.TemporaryDirectory() as tmpdir1, tempfile.TemporaryDirectory() as tmpdir2:
            with pgembed.get_server(tmpdir1) as pg1, pgembed.get_server(tmpdir2) as pg2:
                pid1 = _check_server(pg1)
                pid2 = _check_server(pg2)
    finally:
        _kill_server(pid1)
        _kill_server(pid2)


def _reuse_deleted_datadir(prefix: str):
    """ test common scenario where we repeatedly delete the datadir and start a new server on it """
    """ NB: currently this test is not reproducing the problem """
    # one can reproduce the problem by running the following in a loop:
    # python -c 'import pixeltable as pxt; pxt.Client()'; rm -rf ~/.pixeltable/; python -c 'import pixeltable as pxt; pxt.Client()'
    # which creates a database with more contents etc
    tmpdir = tempfile.mkdtemp(prefix=prefix)
    pgdata = Path(tmpdir) / 'pgdata'
    server_processes = []
    shmem_ids = []

    num_tries = 3
    try:
        for _ in range(num_tries):
            assert not pgdata.exists()

            queue_from_child = mp.Queue()
            child = mp.Process(target=_start_server_in_separate_process, args=(pgdata, None, queue_from_child, None))
            child.start()
            # wait for child to start server
            curr_pid = queue_from_child.get()
            child.join()
            server_proc = psutil.Process(curr_pid)
            assert server_proc.is_running()
            server_processes.append(server_proc)
            postmaster = pgembed.utils.PostmasterInfo.read_from_pgdata(pgdata)

            if postmaster.shmget_id is not None:
                shmem_ids.append(postmaster.shmget_id)

            if platform.system() == 'Windows':
                # windows will not allow deletion of the directory while the server is running
                _kill_server(server_proc)

            shutil.rmtree(pgdata)
    finally:
        if platform.system() != 'Windows':
            # if sysv_ipc is installed (eg locally), remove the shared memory segment
            # done this way because of CI/CD issues with sysv_ipc
            # this avoids having to restart the machine to clear the shared memory
            try:
                import sysv_ipc
                do_shmem_cleanup = True
            except ImportError:
                do_shmem_cleanup = False
                logging.warning("sysv_ipc not installed, skipping shared memory cleanup...")

            if do_shmem_cleanup:
                for shmid in shmem_ids:
                    try:
                        sysv_ipc.remove_shared_memory(shmid)
                    except sysv_ipc.ExistentialError as e:
                        logging.info(f"shared memory already removed: {e}")

        for proc in server_processes:
            _kill_server(proc)

    shutil.rmtree(tmpdir)

def test_reuse_deleted_datadir_short():
    """ test that new server starts normally on same datadir after datadir is deleted
    """
    _reuse_deleted_datadir('short_prefix')

def test_reuse_deleted_datadir_long():
    """ test that new server starts normally on same datadir after datadir is deleted
    """
    long_prefix = '_'.join(['long_prefix'] + ['1234567890']*12)
    assert len(long_prefix) > 120
    _reuse_deleted_datadir(long_prefix)

def test_multiprocess_shared():
    """ Test that multiple processes can share the same server.

        1. get server in a child process,
        2. then, get server in the parent process
        3. then, exiting the child process
        4. checking the parent can still use the server.
    """
    pid = None
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_to_child = mp.Queue()
            queue_from_child = mp.Queue()
            child = mp.Process(target=_start_server_in_separate_process, args=(tmpdir,queue_to_child,queue_from_child, 'stop'))
            child.start()
            # wait for child to start server
            server_pid_child = queue_from_child.get()

            with pgembed.get_server(tmpdir) as pg:
                server_pid_parent = _check_server(pg)
                assert server_pid_child == server_pid_parent

                # tell child to continue
                queue_to_child.put(None)
                child.join()

                # check server still works
                _check_server(pg)

            assert not process_is_running(server_pid_parent)
    finally:
        _kill_server(pid)


RELEASE_EXTENSION_ORDER = (
    "pgvector",
    "vectorchord",
    "age",
    "psql_bm25s",
    "timescaledb",
    "pg_cron",
    "pg_net",
    "pgsql_http",
    "plsh",
)
RELEASE_PRELOAD_PACKAGES = (
    "vectorchord",
    "timescaledb",
    "pg_cron",
    "pg_net",
)
RELEASE_PRELOAD_NAMES = (
    "vchord",
    "timescaledb",
    "pg_cron",
    "pg_net",
)


def _require_full_release_metadata():
    metadata = require_bundle_metadata()
    assert metadata.postgres_major == 18
    assert set(metadata.extensions) == set(RELEASE_EXTENSION_ORDER)
    for name in RELEASE_EXTENSION_ORDER:
        extension = metadata.extensions[name]
        assert extension.requested, f"release extension was not requested: {name}"
        assert extension.built, f"release extension was not built: {name}"
        assert not extension.skipped, f"release extension was skipped: {name}"
        assert extension.built_for_postgres_major == 18
        assert pgembed.has_extension(name), f"runtime did not attest release extension: {name}"
    preload_names = tuple(
        metadata.extensions[name].preload_name for name in RELEASE_PRELOAD_PACKAGES
    )
    assert preload_names == RELEASE_PRELOAD_NAMES
    assert all(metadata.extensions[name].requires_preload for name in RELEASE_PRELOAD_PACKAGES)
    return metadata


def _create_release_extension_catalog(pg: pgembed.PostgresServer) -> set[str]:
    metadata = _require_full_release_metadata()
    for name in RELEASE_EXTENSION_ORDER:
        pg.create_extension(name)
    expected = {metadata.extensions[name].create_name for name in RELEASE_EXTENSION_ORDER}
    catalog_rows = _psql_tuples(
        pg,
        "SELECT e.extname, e.extversion, a.default_version "
        "FROM pg_extension e "
        "JOIN pg_available_extensions a ON a.name = e.extname "
        "WHERE e.extname <> 'plpgsql' ORDER BY e.extname;",
    )
    actual = {row[0] for row in catalog_rows}
    assert actual == expected
    assert all(installed_version == default_version for _, installed_version, default_version in catalog_rows)
    access_methods = {
        row[0]
        for row in _psql_tuples(
            pg,
            "SELECT amname FROM pg_am "
            "WHERE amname IN ('hnsw', 'ivfflat', 'vchordg', 'vchordrq', 'psql_bm25s');",
        )
    }
    assert access_methods == {"hnsw", "ivfflat", "vchordg", "vchordrq", "psql_bm25s"}
    return expected


def _assert_preload_order(pg: pgembed.PostgresServer) -> None:
    configured = _psql_tuples(pg, "SHOW shared_preload_libraries;")[0][0]
    assert tuple(item.strip() for item in configured.split(",") if item.strip()) == RELEASE_PRELOAD_NAMES


def test_missing_preload_library_returns_structured_startup_error(tmp_path: Path):
    started = time.monotonic()
    with pytest.raises(pgembed.PostgresStartupError) as exc_info:
        pgembed.get_server(
            tmp_path / "invalid-preload",
            cleanup_mode="delete",
            shared_preload_libraries="pgembed_library_that_does_not_exist",
        )
    assert time.monotonic() - started < 45
    assert "pgembed_library_that_does_not_exist" in str(exc_info.value)
    postmaster = pgembed.utils.PostmasterInfo.read_from_pgdata(tmp_path / "invalid-preload")
    assert postmaster is None or not postmaster.is_running()


def test_all_preload_extensions_restart(tmp_path: Path):
    _require_full_release_metadata()
    pgdata = tmp_path / "full-release-restart"

    with pgembed.get_server(
        pgdata,
        cleanup_mode="stop",
        shared_preload_libraries=RELEASE_PRELOAD_NAMES,
    ) as pg:
        first_pid = _check_server(pg, RELEASE_PRELOAD_NAMES)
        _assert_preload_order(pg)
        expected_catalog = _create_release_extension_catalog(pg)
        server_version_num = int(_psql_tuples(pg, "SHOW server_version_num;")[0][0])
        assert 180000 <= server_version_num < 190000

        pg.psql(
            """
            DROP TABLE IF EXISTS pg18_vector_restart;
            CREATE TABLE pg18_vector_restart (id integer PRIMARY KEY, emb vector(3));
            INSERT INTO pg18_vector_restart VALUES
              (1, '[1,0,0]'), (2, '[0,1,0]'), (3, '[0,0,1]'),
              (4, '[0.9,0.1,0]'), (5, '[0.1,0.9,0]'), (6, '[0,0.1,0.9]');
            CREATE INDEX pg18_vector_hnsw_idx
              ON pg18_vector_restart USING hnsw (emb vector_l2_ops);
            """
        )
        hnsw_plan = pg.psql(
            "SET enable_seqscan TO off; EXPLAIN (COSTS OFF) "
            "SELECT id FROM pg18_vector_restart ORDER BY emb <-> '[1,0,0]' LIMIT 3;"
        )
        assert "Index Scan using pg18_vector_hnsw_idx" in hnsw_plan
        pg.psql(
            """
            DROP INDEX pg18_vector_hnsw_idx;
            CREATE INDEX pg18_vector_ivfflat_idx
              ON pg18_vector_restart USING ivfflat (emb vector_l2_ops) WITH (lists = 1);
            ANALYZE pg18_vector_restart;
            """
        )
        ivfflat_plan = pg.psql(
            "SET enable_seqscan TO off; EXPLAIN (COSTS OFF) "
            "SELECT id FROM pg18_vector_restart ORDER BY emb <-> '[1,0,0]' LIMIT 3;"
        )
        assert "Index Scan using pg18_vector_ivfflat_idx" in ivfflat_plan

        pg.psql(
            """
            DROP TABLE IF EXISTS pg18_bm25_restart;
            CREATE TABLE pg18_bm25_restart (id integer, body text);
            INSERT INTO pg18_bm25_restart VALUES
              (1, 'hello vector search'),
              (2, 'postgres bm25 search'),
              (3, 'graph query age');
            CREATE INDEX pg18_bm25_restart_idx
              ON pg18_bm25_restart USING psql_bm25s (body);
            """
        )
        pg.psql(
            """
            DROP TABLE IF EXISTS pg18_timescale_restart;
            CREATE TABLE pg18_timescale_restart (
              observed_at timestamptz NOT NULL,
              value double precision
            );
            SELECT create_hypertable('pg18_timescale_restart', 'observed_at');
            INSERT INTO pg18_timescale_restart VALUES
              ('2026-08-07 00:00:00+00', 1.0),
              ('2026-08-07 00:01:00+00', 2.0);
            """
        )
        pg.psql(
            "LOAD 'age'; SET search_path = ag_catalog, \"$user\", public; "
            "SELECT create_graph('pg18_restart_graph');"
        )
        assert _psql_tuples(pg, "SELECT to_regnamespace('net') IS NOT NULL;")[0][0]
        cron_job_id = _psql_tuples(
            pg,
            "SELECT cron.schedule('pgembed_pg18_restart', '* * * * *', 'SELECT 1');",
        )[0][0]
        assert isinstance(cron_job_id, int)

    assert not process_is_running(first_pid)

    with pgembed.get_server(
        pgdata,
        cleanup_mode="delete",
        shared_preload_libraries=RELEASE_PRELOAD_NAMES,
    ) as pg:
        second_pid = _check_server(pg, RELEASE_PRELOAD_NAMES)
        assert second_pid != first_pid
        _assert_preload_order(pg)
        actual_catalog = {
            row[0]
            for row in _psql_tuples(
                pg,
                "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY extname;",
            )
        }
        assert actual_catalog == expected_catalog
        assert _psql_tuples(
            pg,
            "SELECT id FROM pg18_vector_restart ORDER BY emb <-> '[1,0,0]' LIMIT 3;",
        ) == [(1,), (4,), (5,)]
        assert len(
            _psql_tuples(
                pg,
                "SELECT doc_id, score FROM "
                "psql_bm25s_query('pg18_bm25_restart_idx', 'search', 2);",
            )
        ) == 2
        assert _psql_tuples(
            pg,
            "SELECT count(*), sum(value) FROM pg18_timescale_restart "
            "WHERE time_bucket(INTERVAL '1 minute', observed_at) IS NOT NULL;",
        ) == [(2, 3.0)]
        assert _psql_tuples(
            pg,
            "SELECT count(*) > 0 FROM pg_indexes "
            "WHERE tablename = 'pg18_timescale_restart';",
        ) == [(True,)]
        assert _psql_tuples(
            pg,
            "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph "
            "WHERE name = 'pg18_restart_graph');",
        ) == [(True,)]
        assert _psql_tuples(
            pg,
            "SELECT count(*) FROM cron.job WHERE jobname = 'pgembed_pg18_restart';",
        ) == [(1,)]
        assert _psql_tuples(pg, f"SELECT cron.unschedule({cron_job_id});") == [(True,)]
        assert _psql_tuples(pg, "SELECT to_regnamespace('net') IS NOT NULL;") == [(True,)]
        assert _psql_tuples(pg, "SELECT 1;") == [(1,)]

        preload_lines = [
            line
            for line in (pgdata / "postgresql.conf").read_text().splitlines()
            if line.strip().startswith("shared_preload_libraries")
        ]
        assert len(preload_lines) == 1
        for library in RELEASE_PRELOAD_NAMES:
            assert preload_lines[0].split("=", 1)[1].count(library) == 1
