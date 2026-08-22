![Python Version](https://img.shields.io/badge/python-3.12%2C%203.13%2C%203.14-blue)
![Postgres Version](https://img.shields.io/badge/PostgreSQL-18.4-blue)

> **PostgreSQL 18 release candidate:** `0.3.0rc1` is the first PG18 channel. Test migrations before production use. Wheels are published for CPython **3.12, 3.13, and 3.14** on macOS arm64 (deployment target **26.0**) and Linux x86_64/aarch64. Python 3.10/3.11 artifacts stop because the project requires Python >=3.12.
>
> **PG17 data directories do not start in the PG18 bundle.** pgembed reads `PG_VERSION` before creating files, changing permissions, writing configuration, or starting a process. A PG17 directory raises `PostgresDataDirectoryVersionError` and is left untouched. Follow [the PostgreSQL 17 to 18 migration guide](docs/migrations/postgresql-17-to-18.md); do not point the PG18 wheel at your only copy of PG17 data.

[![License](https://img.shields.io/badge/License-Apache%202.0-darkblue.svg)](https://opensource.org/licenses/Apache-2.0)
[![PyPI Package](https://img.shields.io/pypi/v/pgembed?color=darkorange)](https://pypi.org/project/pgembed)
![PyPI - Downloads](https://img.shields.io/pypi/dm/pgembed)


<p align="center">
  <img src="https://raw.githubusercontent.com/Ladybug-Memory/pgembed/main/pgembed_square_small.png"/>
</p>

# pgembed: Embedded PostgreSQL for Agents

pgembed makes it easy to add a full-featured PostgreSQL database to your Python application—no server setup required. Your users simply run `pip install yourapp`, and PostgreSQL comes bundled automatically.

Think of it like SQLite, but with the power of PostgreSQL. Just `pip install pgembed`, call `pgembed.get_server(...)`, and you're ready to go.

<a target="_blank" href="https://colab.research.google.com/github/anomalyco/pgembed/blob/master/pgembed-example.ipynb"> <img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/> </a>

> 🐯 **New — mount your database as a filesystem.** pgembed now bundles [TigerFS](https://github.com/timescale/tigerfs), so you can work with the **same database** through SQL *and* ordinary file tools — `ls`, `cat`, and `grep` your tables and rows like any directory. Jump to [Mount your database as a filesystem](#mount-your-database-as-a-filesystem).

## What pgembed gives you

- **Bundled PostgreSQL runtime**: Current release artifacts target Darwin/Linux only: macOS arm64 (deployment target 26.0) and Linux x86_64/aarch64
- **No external PostgreSQL setup**: The database runtime is packaged with pgembed and does not require a separately managed server
- **Container-friendly core database**: SQL access works in containers and sandboxes; TigerFS filesystem mounts additionally require the host mount facility described below
- **Simple initialization**: `pgembed.get_server(MY_DATA_DIR)` handles `initdb`, port management, and process cleanup automatically
- **Filesystem access**: Includes [TigerFS](https://github.com/timescale/tigerfs) to mount your database as a filesystem — read and write the same data with SQL or with `ls`/`cat`/`grep`
- **Vector search ready**: Includes [pgvector](https://github.com/pgvector/pgvector) and [VectorChord](https://github.com/tensorchord/VectorChord) for vector similarity queries and high-performance vector storage
- **Graph ready**: Includes [Apache AGE](https://github.com/apache/age) for graph traversals and property graphs
- **Text search ready**: Includes [psql_bm25s](https://github.com/Intelligent-Internet/psql_bm25s) for BM25-based full-text search with ranking
- **Time-series ready**: Includes [TimescaleDB](https://github.com/timescale/timescaledb) for hypertables and time-series workloads
- **Scheduling, HTTP & shell**: Includes [pg_cron](https://github.com/citusdata/pg_cron) (job scheduler), [pg_net](https://github.com/supabase/pg_net) (async HTTP client), [pgsql-http](https://github.com/pramsey/pgsql-http) (synchronous HTTP client), and [PL/sh](https://github.com/petere/plsh) (shell-script functions — run bash from SQL)

## Quick start

```python
import pgembed

# Initialize and start the server
pgembed.get_server("/path/to/my/data/dir")

# Connect and use like any PostgreSQL database
# ... your database code here
# Look in examples/*.py for more complete examples that could be run via uv
```

PostgreSQL binaries are available at `pgembed.POSTGRES_BIN_PATH` if you need direct access to tools like `initdb`, `pg_ctl`, `psql`, or `pg_config`. The installed wheel also exposes `pgembed.BUNDLED_PG_MAJOR` and `pgembed.BUNDLED_POSTGRES_VERSION`; PG18 wheels report `18` and `"18.4"`. In an unbuilt editable checkout those constants are `None`, extension availability is fail-closed, and the first `get_server()` explains that bundle metadata is unavailable.

## Mount your database as a filesystem

pgembed bundles [TigerFS](https://github.com/timescale/tigerfs) (v0.7.0) — a standalone client daemon that mounts your running database as a real filesystem. Tables appear as directories and rows as files, and pipeline paths such as `.by/<col>/<val>/.order/<col>/.last/<n>` push down to SQL. The same database stays usable through SQL **and** through ordinary file tools at the same time.

```python
import os
import subprocess
import sys
import time
from pathlib import Path

import pgembed


def wait_for_mount(path: Path, daemon: subprocess.Popen, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon.poll() is not None:
            raise RuntimeError(f"TigerFS exited before the mount was ready: {daemon.returncode}")
        if os.path.ismount(path):
            return
        time.sleep(0.1)
    raise TimeoutError(f"TigerFS mount was not ready after {timeout}s: {path}")


def wait_for_unmount(path: Path, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not os.path.ismount(path):
            return True
        time.sleep(0.1)
    return not os.path.ismount(path)


mount_dir = Path("/mnt/db")
mount_dir.mkdir(parents=True, exist_ok=True)

with pgembed.get_server("/path/to/my/data/dir") as server:
    uri = server.get_uri("postgres")
    tigerfs = pgembed.POSTGRES_BIN_PATH / "tigerfs"
    daemon = subprocess.Popen([str(tigerfs), "mount", "--foreground", uri, str(mount_dir)])
    try:
        try:
            wait_for_mount(mount_dir, daemon)
            subprocess.run(["ls", str(mount_dir)], check=True)
            # SQL clients can use `uri` while file tools read the same database.
        finally:
            # Unmount. `tigerfs unmount` can exit non-zero or time out on macOS
            # even when it succeeds, so the fallback decision uses the ACTUAL
            # mount state, not the return code.
            try:
                subprocess.run(
                    [str(tigerfs), "unmount", str(mount_dir)],
                    check=False,
                    timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
            if os.path.ismount(mount_dir):
                try:
                    fallback = (
                        ["diskutil", "unmount", "force", str(mount_dir)]
                        if sys.platform == "darwin"
                        else ["fusermount", "-u", str(mount_dir)]
                    )
                    subprocess.run(fallback, check=False, timeout=10)
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    pass
                wait_for_unmount(mount_dir)
    finally:
        # Unconditional process reclamation — the outer finally, so any unmount
        # error above cannot skip reclaiming the daemon.
        try:
            daemon.wait(timeout=15)
        except subprocess.TimeoutExpired:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
        # After reclaiming the daemon, a lingering mount is a hard error.
        if os.path.ismount(mount_dir):
            raise RuntimeError(f"TigerFS mount still present after cleanup: {mount_dir}")
```

TigerFS is a **companion client tool, not a PostgreSQL extension** — it ships as a binary (next to `psql`/`initdb`), runs as a separate process, and connects to PostgreSQL as a client; there is no `CREATE EXTENSION` form. There is also **no `pgembed.tigerfs()` callable**: invoke `pgembed.POSTGRES_BIN_PATH / "tigerfs"` with `subprocess.Popen` for mounts. A lifecycle-aware `PostgresServer.mount_filesystem()` API remains deferred.

For build/install steps, functional tests, troubleshooting, and the file-first (`.build/`) mode, see **[docs/tigerfs.md](docs/tigerfs.md)**.

## Extensions

pgembed bundles a curated set of PostgreSQL extensions, built specifically for PostgreSQL 18 by `pgbuild/Makefile` and shipped inside the wheel. Availability is attested by `pginstall/bundle-metadata.json`: a leftover `.so`/`.dylib` alone is never treated as compatible.

| Extension | `CREATE EXTENSION` | pgembed key | Preload | Notes |
|---|---|---|---|---|
| [pgvector](https://github.com/pgvector/pgvector) | `vector` | `pgvector` | — | required by VectorChord |
| [VectorChord](https://github.com/tensorchord/VectorChord) | `vchord` | `vectorchord` | `vchord` | high-perf vector storage (Rust/pgrx) |
| [Apache AGE](https://github.com/apache/age) | `age` | `age` | — | graph / openCypher |
| [psql_bm25s](https://github.com/Intelligent-Internet/psql_bm25s) | `psql_bm25s` | `psql_bm25s` | — | BM25 full-text search |
| [TimescaleDB](https://github.com/timescale/timescaledb) | `timescaledb` | `timescaledb` | `timescaledb` | hypertables / time-series |
| [pg_cron](https://github.com/citusdata/pg_cron) | `pg_cron` | `pg_cron` | `pg_cron` | job scheduler |
| [pg_net](https://github.com/supabase/pg_net) | `pg_net` | `pg_net` | `pg_net` | async HTTP (requires libcurl) |
| [pgsql-http](https://github.com/pramsey/pgsql-http) | `http` | `pgsql_http` | — | synchronous HTTP client (requires libcurl) |
| [PL/sh](https://github.com/petere/plsh) | `plsh` | `plsh` | — | shell-script functions (untrusted; superuser) |

`pgembed-pgvector` is also published as a standalone wheel; the rest are bundled into the base `pgembed` wheel.

> 🐯 **TigerFS is not in this table** — it is a *companion client tool*, not a PostgreSQL extension. Unlike the C/Rust extensions above, TigerFS is a standalone binary at `pgembed.POSTGRES_BIN_PATH / "tigerfs"` that runs as a separate daemon and connects to your database as a client (no `CREATE EXTENSION`). See [Mount your database as a filesystem](#mount-your-database-as-a-filesystem).

### Checking available extensions

```python
import pgembed

# Check which extensions are available
print(pgembed.list_extensions())
# {'pgvector': True, 'vectorchord': True, 'age': True, 'psql_bm25s': True, 'timescaledb': True, 'pg_cron': True, 'pg_net': True, 'pgsql_http': True, 'plsh': True}

# Check if a specific extension is available, then create it
if pgembed.has_extension('vectorchord'):
    server.create_extension('vchord')
```

### Running shell commands from SQL

PL/sh functions are shell scripts; stdout is the return value. Note the script must start with `#!` on the first line (only blank lines may precede it), and a non-zero exit raises a SQL error:

```python
server.create_extension('plsh')
server.psql("""
CREATE FUNCTION run_bash(text) RETURNS text AS $$
#!/bin/bash
out=$(eval "$1" 2>&1); rc=$?
printf '%s\n[exit:%d]' "$out" "$rc"
$$ LANGUAGE plsh;
SELECT run_bash('ls -la | head -3');
""")
```

PL/sh is an untrusted language: only superusers may define functions (the bundled server runs as superuser), so every function is arbitrary command execution on the database host.

### Platform Support

pgembed's release pipeline is Darwin/Linux-only:

- **macOS:** arm64 only, with deployment target **26.0**. The project does not claim Intel, universal2, or older macOS compatibility.
- **Linux:** x86_64 and aarch64.
- **Extensions:** the bundled extension set is built for those release targets. `pg_net` and `pgsql_http` additionally require **libcurl ≥ 7.83**: CI builds a private curl 8 via `tools/build_curl.sh` (auditwheel vendors `libcurl.so.4` into the Linux wheels); on macOS they link the SDK/system libcurl. Local Linux hosts need a curl that new, or run `tools/build_curl.sh` and pass `PG_NET_CURL_PREFIX` / `PGSQL_HTTP_CURL_CONFIG`.
- **TigerFS** *(companion tool, not an extension)*: uses NFS on macOS and FUSE on Linux. Linux mounts require usable `/dev/fuse` access, so mount tests are normally unavailable in default containers, Google Colab, and other unprivileged sandboxes unless the host grants the needed device/capability. The embedded database and non-mount TigerFS package tests do not require FUSE.

### Preload before start

TimescaleDB, VectorChord, pg_cron, and pg_net must be in `shared_preload_libraries` **before** PostgreSQL starts. Configure them when creating the server:

```python
import pgembed

with pgembed.get_server(
    "/path/to/my/data/dir",
    shared_preload_libraries=["vchord", "pg_cron", "pg_net"],
) as server:
    server.create_extension("vector")        # pgvector (VectorChord dependency)
    server.create_extension("vchord")        # VectorChord
    server.create_extension("pg_cron")
    server.create_extension("pg_net")
```

### Building specific extensions

To build only specific extensions:

```bash
# Build only pgvector
make pgvector

# Build only vectorchord
make vectorchord

# Build only age
make age

# Build only psql_bm25s
make psql_bm25s

# Build only timescaledb
make timescaledb

# Build only pg_cron
make pg_cron

# Build only pg_net (needs libcurl)
make pg_net

# Build only pgsql_http (needs libcurl)
make pgsql_http

# Build only plsh
make plsh

# Build specific combination
make EXTENSIONS="pgvector vectorchord timescaledb pg_cron pg_net pgsql_http plsh" all
```

## History

pgembed is a fork of [pgserver](https://github.com/orm011/pgserver), which was inspired by [postgresql-wheel](https://github.com/michelp/postgresql-wheel). While those projects focused primarily on Linux wheels, pgembed extends the approach with:

- Bundled Darwin/Linux releases for macOS arm64 and Linux x86_64/aarch64
- Robust process management and cleanup
- Built-in pgvector, VectorChord, Apache AGE, psql_bm25s, TimescaleDB, pg_cron, and pg_net extensions
