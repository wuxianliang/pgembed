![Python Version](https://img.shields.io/badge/python-3.12%2C%203.13%2C%203.14-blue)
![Postgres Version](https://img.shields.io/badge/PostgreSQL-18.4-blue)

> **PostgreSQL 18 release candidate:** `0.3.0rc2` continues the PG18 channel (adds firebird_fdw and pgmq). Test migrations before production use. Wheels are published for CPython **3.12, 3.13, and 3.14** on macOS arm64 (deployment target **26.0**) and Linux x86_64/aarch64. Python 3.10/3.11 artifacts stop because the project requires Python >=3.12.
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
- **XML support built in**: `--with-libxml` — the `xml` type, `xpath()`, `XMLTABLE`, and the XML functions work out of the box
- **Container-friendly core database**: SQL access works in containers and sandboxes; TigerFS filesystem mounts additionally require the host mount facility described below
- **Simple initialization**: `pgembed.get_server(MY_DATA_DIR)` handles `initdb`, port management, and process cleanup automatically
- **Filesystem access**: Includes [TigerFS](https://github.com/timescale/tigerfs) to mount your database as a filesystem — read and write the same data with SQL or with `ls`/`cat`/`grep`
- **Vector search ready**: Includes [pgvector](https://github.com/pgvector/pgvector) and [VectorChord](https://github.com/tensorchord/VectorChord) for vector similarity queries and high-performance vector storage
- **Graph ready**: Includes [Apache AGE](https://github.com/apache/age) for graph traversals and property graphs
- **Text search ready**: Includes [psql_bm25s](https://github.com/Intelligent-Internet/psql_bm25s) for BM25-based full-text search with ranking
- **Time-series ready**: Includes [TimescaleDB](https://github.com/timescale/timescaledb) for hypertables and time-series workloads
- **Scheduling, HTTP & shell**: Includes [pg_cron](https://github.com/citusdata/pg_cron) (job scheduler), [pg_net](https://github.com/supabase/pg_net) (async HTTP client), [pgsql-http](https://github.com/pramsey/pgsql-http) (synchronous HTTP client), and [PL/sh](https://github.com/petere/plsh) (shell-script functions — run bash from SQL)
- **Firebird FDW**: Includes [firebird_fdw](https://github.com/ibarwick/firebird_fdw) so PostgreSQL can `SELECT`/`INSERT`/`UPDATE`/`DELETE` against a remote Firebird database
- **Message queue**: Includes [pgmq](https://github.com/pgmq/pgmq) for a lightweight Postgres-native queue (SQS/RSMQ-style send/read/archive) and [pg_partman](https://github.com/pgpartman/pg_partman) for partitioned queues
- **Validation & tests**: Includes [pg_jsonschema](https://github.com/supabase/pg_jsonschema) for JSON Schema checks on `json`/`jsonb`, and [pgTAP](https://pgtap.org/) for SQL-level TAP tests
- **AI classification**: Includes [pg_typesafe](https://github.com/giuliosmall/pg_typesafe) to call TypeSafe AI (Jev) from SQL for categorical tasks — classify, detect, score, and ask (pre-alpha; requires libcurl; a one-line local patch adapts it to PG18)
- **PostgreSQL contrib**: `pg_stat_statements`, `pg_trgm`, `unaccent`, `pgcrypto`, `ltree`, `hstore`, and `postgres_fdw` are installed with the server so `CREATE EXTENSION` works without extra packages

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
| [firebird_fdw](https://github.com/ibarwick/firebird_fdw) | `firebird_fdw` | `firebird_fdw` | — | read/write Firebird via SQL/MED; bundles the Firebird **client** (not a Firebird server) |
| [pgmq](https://github.com/pgmq/pgmq) | `pgmq` | `pgmq` | — | SQL-only message queue; partitioned queues use bundled [pg_partman](https://github.com/pgpartman/pg_partman) |
| [pg_partman](https://github.com/pgpartman/pg_partman) | `pg_partman` | `pg_partman` | — | SQL-only partition manager (background worker not bundled; use `pg_cron` for maintenance) |
| [pgTAP](https://pgtap.org/) | `pgtap` | `pgtap` | — | SQL-only TAP test framework |
| [pg_jsonschema](https://github.com/supabase/pg_jsonschema) | `pg_jsonschema` | `pg_jsonschema` | — | JSON Schema validation (Rust/pgrx) |
| [pg_typesafe](https://github.com/giuliosmall/pg_typesafe) | `typesafe` | `pg_typesafe` | — | TypeSafe AI (Jev) categorical classification from SQL; works with a TypeSafe key or via OpenRouter's Decisions API (requires libcurl; pre-alpha, PG18-patched) |

`pgembed-pgvector` is also published as a standalone wheel; the rest are bundled into the base `pgembed` wheel.

> 🐯 **TigerFS is not in this table** — it is a *companion client tool*, not a PostgreSQL extension. Unlike the bundled extensions above, TigerFS is a standalone binary at `pgembed.POSTGRES_BIN_PATH / "tigerfs"` that runs as a separate daemon and connects to your database as a client (no `CREATE EXTENSION`). See [Mount your database as a filesystem](#mount-your-database-as-a-filesystem).

### Checking available extensions

```python
import pgembed

# Check which extensions are available
print(pgembed.list_extensions())
# {'pgvector': True, 'vectorchord': True, 'age': True, 'psql_bm25s': True, 'timescaledb': True, 'pg_cron': True, 'pg_net': True, 'pgsql_http': True, 'plsh': True, 'firebird_fdw': True, 'pgmq': True, 'pg_partman': True, 'pgtap': True, 'pg_jsonschema': True, 'pg_typesafe': True}

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

### Connecting to Firebird

`firebird_fdw` is a foreign data wrapper: it needs a running Firebird server (the wheel only bundles the client). User-mapping credentials are stored in the PostgreSQL catalog.

```python
server.create_extension("firebird_fdw")
server.psql("""
CREATE SERVER firebird_server
  FOREIGN DATA WRAPPER firebird_fdw
  OPTIONS (address 'localhost', database '/path/to/database.fdb');
CREATE USER MAPPING FOR CURRENT_USER
  SERVER firebird_server
  OPTIONS (username 'sysdba', password 'masterkey');
IMPORT FOREIGN SCHEMA public FROM SERVER firebird_server INTO public;
""")
```

See the [upstream firebird_fdw documentation](https://github.com/ibarwick/firebird_fdw) for server, table, and user-mapping options. Third-party license notices for the bundled Firebird client and libfq are under `pginstall/share/licenses/`.

### Using pgmq

pgmq is SQL-only (no shared library, no `shared_preload_libraries`). Partitioned queues need [pg_partman](https://github.com/pgpartman/pg_partman), which is bundled (also SQL-only; the optional `pg_partman_bgw` worker is not).

```python
server.create_extension("pgmq")
server.psql("""
SELECT pgmq.create('jobs');
SELECT pgmq.send('jobs', jsonb_build_object('task', 'hello'));
SELECT msg_id, message FROM pgmq.read('jobs', 30, 1);
""")

server.create_extension("pg_partman")
server.psql("SELECT pgmq.create_partitioned('part_jobs');")
```

### JSON Schema and pgTAP

```python
server.create_extension("pg_jsonschema")
server.psql("""
SELECT json_matches_schema('{"type": "object"}'::json, '{}'::json);
""")

server.create_extension("pgtap")
server.psql("SELECT pgtap_version();")
```

### Using pg_typesafe

`pg_typesafe` calls the [TypeSafe AI](https://typesafe.ai) (Jev) API from SQL for categorical tasks: `typesafe_classify` (choice), `typesafe_detect`/`typesafe_noul` (binary + intensity), `typesafe_score` (0–100 score), and `typesafe_ask` (free-form label). The API key comes from the `typesafe.api_key` GUC or the `TYPESAFE_API_KEY` environment variable of the server process; other GUCs: `typesafe.endpoint`, `typesafe.model`, `typesafe.timeout_ms`, `typesafe.batch_size`, `typesafe.http_concurrency`. Without a key you can still exercise the SQL surface offline via the mock GUC:

```python
import os

os.environ.setdefault("TYPESAFE_API_KEY", "...")  # inherited by the server process

with pgembed.get_server("/path/to/my/data/dir") as server:
    server.create_extension("pg_typesafe")
    # Real calls need the API key above; mock mode needs no key and no network:
    server.psql("""
SET typesafe.mock_response = $${
  "model": "jev-latest",
  "answers": {"label": {"type": "choice", "choice": "technical",
                        "confidence": 0.82,
                        "probabilities": {"billing": 0.08, "technical": 0.85, "sales": 0.07}}},
  "usage": {"input_tokens": 312, "output_tokens": 48}
}$$;
SELECT * FROM typesafe_classify(
    'Help! My payouts have been failing for 3 days.',
    'Which team should handle this?',
    '{"billing": "Payments, invoicing, refunds",
      "technical": "Bugs, outages, integrations",
      "sales": "Pricing, upgrades, new accounts"}'::jsonb);
""")
```

See the [upstream pg_typesafe README](https://github.com/giuliosmall/pg_typesafe) for the full function set (including `_many` batch variants and `typesafe_last_request()`). The project is pre-alpha; it is pinned to commit `93a5acb` with `pgbuild/patches/pg_typesafe-pg18-noreturn.patch` adapting it to PostgreSQL 18.

#### Using OpenRouter instead of a TypeSafe account

OpenRouter hosts Jev as [`~typesafe/jev-latest`](https://openrouter.ai/~typesafe/jev-latest) (an alias over versioned ids like `typesafe/jev-1.13-20260917`) and serves the same System One wire schema through its **Decisions API (alpha)** — the extension works against it unchanged. Point the GUCs at OpenRouter and put your OpenRouter key (`sk-or-v1-...`) in `TYPESAFE_API_KEY`:

```python
import os

os.environ["TYPESAFE_API_KEY"] = os.environ["OPENROUTER_API_KEY"]

with pgembed.get_server("/path/to/my/data/dir") as server:
    server.create_extension("pg_typesafe")
    # SET is session-scoped: keep it in the same psql() call as the SELECT,
    # or persist with ALTER SYSTEM ... + SELECT pg_reload_conf().
    print(server.psql("""
SET typesafe.endpoint = 'https://openrouter.ai/api/alpha/decisions';
SET typesafe.model    = '~typesafe/jev-latest';
SELECT * FROM typesafe_classify(
    'Help! My payouts have been failing for 3 days.',
    'Which team should handle this?',
    '{"billing": "Payments, invoicing, refunds",
      "technical": "Bugs, outages, integrations",
      "sales": "Pricing, upgrades, new accounts"}'::jsonb);
"""))
#  choice  | confidence |                 probabilities                  |           model            | input_tokens | output_tokens
# ---------+------------+------------------------------------------------+----------------------------+--------------+---------------
#  billing |       0.84 | {"sales": 0, "billing": 0.89, "technical": 0.11} | typesafe/jev-1.13-20260917 |          349 |            38
```

Notes, verified live on 2026-09-18:

- `SET typesafe.endpoint` must precede the query **in the same session** (`server.psql()` opens a fresh connection per call — combine them in one call, or use `ALTER SYSTEM SET` + `pg_reload_conf()`).
- `TYPESAFE_API_KEY` must be in the environment **before** `get_server()` starts the postmaster (the server process inherits it).
- The working endpoint is `https://openrouter.ai/api/alpha/decisions`. OpenRouter's own docs page currently shows a doubled path (`/api/v1/api/alpha/...`) that returns 404 — trust the URL here, and re-verify if the alpha API moves.
- Pricing at time of writing: input $0.042/M tokens, output free (a typical classify call is a fraction of a cent).
- Pin `typesafe.model = 'typesafe/jev-1.13'` (or a dated variant) instead of the `~...latest` alias for reproducible behavior across Jev releases.

### PostgreSQL contrib

These ship with the PG18 server (not listed by `pgembed.list_extensions()`, but `CREATE EXTENSION` works). `pg_stat_statements` must be in `shared_preload_libraries` before start.

```python
server.create_extension("pg_trgm")
server.create_extension("unaccent")
server.create_extension("pgcrypto")
server.create_extension("ltree")
server.create_extension("hstore")
server.create_extension("postgres_fdw")

with pgembed.get_server(
    "/path/to/my/data/dir",
    shared_preload_libraries=["pg_stat_statements"],
) as server:
    server.create_extension("pg_stat_statements")
```

An agent-oriented preload set is `vchord, pg_cron, pg_stat_statements`. Do not preload every bundled library by default. `pg_net` only if the database itself sends webhooks.

### Platform Support

pgembed's release pipeline is Darwin/Linux-only:

- **macOS:** arm64 only, with deployment target **26.0**. The project does not claim Intel, universal2, or older macOS compatibility.
- **Linux:** x86_64 and aarch64.
- **Extensions:** the bundled extension set is built for those release targets. `pg_net` and `pgsql_http` additionally require **libcurl ≥ 7.83** (`pg_typesafe` links the same libcurl provider and needs ≥ 7.61): CI builds a private curl 8 via `tools/build_curl.sh` (auditwheel vendors `libcurl.so.4` into the Linux wheels); on macOS they link the SDK/system libcurl. Local Linux hosts need a curl that new, or run `tools/build_curl.sh` and pass `PG_NET_CURL_PREFIX` / `PGSQL_HTTP_CURL_CONFIG` (`pg_typesafe` picks the prefix up from `PG_NET_CURL_PREFIX` via pkg-config). `firebird_fdw` vendors [libfq](https://github.com/ibarwick/libfq) 0.6.2 and the Firebird 5.0.3 **client** libraries (plus libtommath on Linux); it does not ship a Firebird server. musl builds skip it. `pgcrypto` needs OpenSSL: Linux uses the distro library; macOS vendors Homebrew `openssl@3` into the prefix (`@loader_path`) because Apple no longer ships `/usr/lib/libssl`. musl builds skip VectorChord and `pg_jsonschema`.
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

# Build only firebird_fdw (Firebird client + libfq)
make firebird_fdw

# Build only pgmq (SQL-only message queue)
make pgmq

# Build only pg_partman (SQL-only; no background worker)
make pg_partman

# Build only pgTAP
make pgtap

# Build only pg_jsonschema (Rust/pgrx)
make pg_jsonschema

# Build only pg_typesafe (needs libcurl)
make pg_typesafe

# Build specific combination
make EXTENSIONS="pgvector vectorchord timescaledb pg_cron pg_net pgsql_http plsh firebird_fdw pgmq pg_partman pgtap pg_jsonschema pg_typesafe" all
```

## History

pgembed is a fork of [pgserver](https://github.com/orm011/pgserver), which was inspired by [postgresql-wheel](https://github.com/michelp/postgresql-wheel). While those projects focused primarily on Linux wheels, pgembed extends the approach with:

- Bundled Darwin/Linux releases for macOS arm64 and Linux x86_64/aarch64
- Robust process management and cleanup
- Built-in pgvector, VectorChord, Apache AGE, psql_bm25s, TimescaleDB, pg_cron, pg_net, firebird_fdw, pgmq, pg_partman, pgTAP, pg_jsonschema, and pg_typesafe extensions
