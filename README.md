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
- **AI classification**: Includes [pg_typesafe](https://github.com/giuliosmall/pg_typesafe) **0.1.0** to call TypeSafe AI (Jev) from SQL. Keeps the `typesafe_*` text classifiers and adds native `jev_*` row predicates (pg-jev **0.2.0** API: `WHERE jev(tbl, '…')`, `jev_choice`, `jev_score`, …). Requires libcurl.
- **Provenance & uncertainty**: Includes [ProvSQL](https://github.com/PierreSenellart/provsql) for semiring provenance (`why`/`how`/`where`), probabilities, and Shapley/Banzhaf attribution. Requires `shared_preload_libraries=["provsql"]`.
- **Indexed search engine**: Includes [stannum](https://github.com/wuxianliang/stannum) (our fork of TeamSpringbird/stannum, a fork of PlanetScale Lead; carries the `jieba` word-level Chinese tokenizer) for BM25 search with Boolean/phrase/proximity TINQL queries, highlighting, and `stannum` access-method indexes (Rust/pgrx; development software)
- **History & branches**: Includes [gnomon](https://github.com/bitsinfra/gnomon) for version history, time travel, branches, diff, three-way merges and undo inside PostgreSQL, plus its optional C module (`gnomon_c`) for statement routing, real row locks and a heartbeat worker
- **PostgreSQL contrib**: `pg_stat_statements`, `pg_trgm`, `unaccent`, `pgcrypto`, `ltree`, `hstore`, and `postgres_fdw` are installed with the server so `CREATE EXTENSION` works without extra packages

## Quick start

```python
import pgembed

# Zero config: per-user PGDATA, required preloads applied, base extensions created.
with pgembed.bootstrap() as db:
    db.psql("SELECT extversion FROM pg_extension WHERE extname = 'typesafe';")
```

`bootstrap()` resolves `pgembed.default_pgdata()`, applies the profile's
`shared_preload_libraries` *before* the first start, and creates the profile's
extensions. It returns a normal `pgembed.PostgresServer`; `db.bootstrap_report`
records what was created, skipped (not in this wheel), or failed, and
`bootstrap(strict=True)` raises instead of recording a failure.

The `base` profile creates `vector`, `vchord`, `stannum`, `typesafe`, `pgmq`,
`pg_jsonschema`, `ltree`, `pg_trgm`, and `pgcrypto`, and preloads `vchord` and
`pg_stat_statements`. Order matters: PostgreSQL refuses `CREATE EXTENSION vchord`
unless the postmaster already loaded `vchord`, so the preload has to be in place
at start time and cannot be added afterwards.

```python
# Prefer to drive it yourself.
server = pgembed.get_server("/path/to/my/data/dir")
server.create_extension("vector")
server.create_extension("vchord")  # raises PreloadRequiredError without the preload
```

`get_server()` also defaults to `pgembed.default_pgdata()` when called with no
argument, so `pgembed.get_server()` works on a fresh machine. Extensions that
need a preload report `PreloadRequiredError` with the library name instead of
surfacing a raw `psql` failure.

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
| [gnomon](https://github.com/bitsinfra/gnomon) | `gnomon` | `gnomon` | — | SQL-only core: version history, time travel, branches, diff, three-way merge and undo. Creates its own schema and `gnomon_*` roles per cluster; `CREATE EXTENSION` needs a superuser (the bundled server is one). |
| [gnomon C module](https://github.com/bitsinfra/gnomon/blob/main/src/c/README.md) | `gnomon_c` | `gnomon_c` | `gnomon_c` | optional: statement routing (`SET gnomon.branch`), real row locks, point reads, and the heartbeat worker. `requires = 'gnomon'`. |
| [pg_jsonschema](https://github.com/supabase/pg_jsonschema) | `pg_jsonschema` | `pg_jsonschema` | — | JSON Schema validation (Rust/pgrx) |
| [pg_typesafe](https://github.com/giuliosmall/pg_typesafe) | `typesafe` | `pg_typesafe` | — | **0.1.0**: `typesafe_*` classifiers plus native `jev_*` row predicates (pg-jev 0.2.0). `bootstrap()` defaults the native family to api.tu-zi.com/jev-1.14; TypeSafe or OpenRouter also work via GUCs. `\dx` comment: *TypeSafe AI client and native Jev row predicates*. Requires libcurl. |
| [stannum](https://github.com/wuxianliang/stannum) | `stannum` | `stannum` | — | BM25 search engine with TINQL Boolean/phrase queries, highlighting, and exact counts under concurrent writes (Rust/pgrx; development software; our fork of TeamSpringbird/stannum with the `jieba` tokenizer) |
| [uuid-ossp](https://www.postgresql.org/docs/18/uuid-ossp.html) | `uuid-ossp` | `uuid-ossp` | — | PostgreSQL contrib UUID generators; required by ProvSQL (OSSP UUID linked statically) |
| [ProvSQL](https://github.com/PierreSenellart/provsql) | `provsql` | `provsql` | `provsql` | semiring provenance: `why`/`how`/`where`, probabilities, Shapley/Banzhaf; uses `uuid-ossp` |

`pgembed-pgvector` is also published as a standalone wheel; the rest are bundled into the base `pgembed` wheel.

> 🐯 **TigerFS is not in this table** — it is a *companion client tool*, not a PostgreSQL extension. Unlike the bundled extensions above, TigerFS is a standalone binary at `pgembed.POSTGRES_BIN_PATH / "tigerfs"` that runs as a separate daemon and connects to your database as a client (no `CREATE EXTENSION`). See [Mount your database as a filesystem](#mount-your-database-as-a-filesystem).

### Checking available extensions

```python
import pgembed

# Check which extensions are available
print(pgembed.list_extensions())
# {'pgvector': True, 'vectorchord': True, 'age': True, 'psql_bm25s': True, 'timescaledb': True, 'pg_cron': True, 'pg_net': True, 'pgsql_http': True, 'plsh': True, 'firebird_fdw': True, 'pgmq': True, 'pg_partman': True, 'pgtap': True, 'gnomon': True, 'gnomon_c': True, 'pg_jsonschema': True, 'pg_typesafe': True, 'stannum': True, 'uuid-ossp': True, 'provsql': True}

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

### Full-text search with stannum

`stannum` is a BM25 search engine with the TINQL query language (Boolean operators, phrases, proximity), highlighting, and exact counts under concurrent writes. Create a `stannum` access-method index and query with the `==>` operator:

```python
server.create_extension("stannum")
server.psql("""
CREATE TABLE documents (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    body text
);
INSERT INTO documents (body) VALUES
  ('PostgreSQL supports full text search'),
  ('A search engine with exact phrase matching');
CREATE INDEX documents_search ON documents USING stannum (body);
ANALYZE documents;
""")

# BM25-ranked search
print(server.psql("""
SELECT id, stannum.full_score(ctid) AS score
FROM documents
WHERE body ==> 'search'
ORDER BY score DESC
LIMIT 10;
"""))

# Exact phrase, highlighting, and index diagnostics
print(server.psql("SELECT count(*) FROM documents WHERE body ==> '\"phrase matching\"';"))
print(server.psql("SELECT stannum.highlight(body, '<mark>', '</mark>', query => 'search') FROM documents;"))
print(server.psql("SELECT * FROM stannum.segment_info('documents_search');"))
```

For mixed Chinese/English corpora, create the index with the optional `jieba`
tokenizer for word-level Chinese segmentation (dictionary-based; the default
`unicode` tokenizer analyzes Han text per character, so a Chinese query term
matches as a character-adjacency phrase instead):

```python
server.psql("""
CREATE INDEX documents_search_cn ON documents USING stannum (body)
  WITH (tokenizer = 'jieba');
""")
print(server.psql("SELECT count(*) FROM documents WHERE body ==> '数据库';"))
print(server.psql("""
SELECT string_agg(tok, '/') FROM stannum.tokenize(
    'PostgreSQL 是开源数据库', tokenizer => 'jieba') AS t(tok);
"""))  # postgresql/是/开源/数据库
```

`stannum` loads on demand on the primary (no `shared_preload_libraries` needed); reads on a hot standby require preloading it. It is development software, pinned to our fork's `main` by commit.

### History, branches, and merges with gnomon

`gnomon` makes tracked tables versioned data: a branch reads main and keeps only what it changes, so it opens without copying the database. `gnomon.diff()` shows every changed row and field, and `gnomon.merge()` is a three-way, type-aware merge that reports conflicts as rows. The SQL core needs no preload and supports `track`, `create_branch`, `use`, time travel, `diff`, `merge` and `undo_merge`.

The optional C module adds cluster-wide statement routing (`SET gnomon.branch`), real `FOR UPDATE` locks, `INSERT ... ON CONFLICT`, point reads, and the heartbeat worker. Its hooks are installed at postmaster start, so preload it before the first start.

The opt-in `gnomon` profile preloads the C module, creates both extensions, and points the heartbeat at the bootstrap database in one call:

```python
import pgembed

server = pgembed.bootstrap(profile="gnomon")
try:
    server.psql("""
    CREATE TABLE orders (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        status text,
        amount numeric
    );
    INSERT INTO orders (status, amount) VALUES ('open', 10), ('paid', 20);
    SELECT gnomon.track('orders');
    SELECT gnomon.create_branch('agent/refunds');
    SELECT gnomon.use('agent/refunds');
    UPDATE orders SET status = 'refunded' WHERE id = 1;
    """)
    # gnomon.use() is session-scoped: the UPDATE above ran on the branch in
    # that psql call. A fresh connection still reads main until the merge.
    print(server.psql("SELECT * FROM gnomon.diff('agent/refunds');"))
    print(server.psql("SELECT gnomon.merge('agent/refunds');"))
finally:
    server.cleanup()
```

To add it to an existing server instead, preload the C module at start and call `create_extension`; it walks `gnomon_c.control`'s `requires = 'gnomon'` dependency for you:

```python
server = pgembed.get_server(
    "/path/to/pgdata",
    shared_preload_libraries=["gnomon_c"],
)
server.create_extension("gnomon")     # SQL core; also works without preload
server.create_extension("gnomon_c")   # optional C module
```

Without the preload, `create_extension("gnomon_c")` raises `PreloadRequiredError`; the SQL core still works on its own. The C module's heartbeat worker only runs for databases named in `gnomon.heartbeat_databases` (the `gnomon` profile sets it to `postgres`). The base profile does not include gnomon because preloading the C module installs cluster-wide planner and utility hooks even for servers that never track a table.

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

This tree's bundled prefix (`src/pgembed/pginstall`) installs **pg_typesafe 0.1.0**, built from the pinned source (`source_ref=11d9358`), not from a local overlay. `CREATE EXTENSION typesafe` exposes two families:

| Family | Surface | Typical use |
|---|---|---|
| `typesafe_*` | text in, record/scalar out | classify / detect / score / ask one string or a batch |
| `jev_*` | row in (`anyelement`), native C | `WHERE jev(tbl, 'the country is Germany')`, `jev_choice`, `jev_score` |

`pg_extension.extversion` is `0.1.0`. `jev_version()` is `0.2.0` (Jev API compatibility, not the extension catalog version). `EXECUTE` is revoked from `PUBLIC` on both families.

`typesafe_*` reads `typesafe.api_key` / `typesafe.api_key_file` or the postmaster's `TYPESAFE_API_KEY`. Native `jev_*` live calls resolve `jev.api_key` (non-empty) then `TYPESAFE_API_KEY`; they do **not** read `typesafe.api_key`. Other useful GUCs: `typesafe.endpoint`, `typesafe.model`, `typesafe.timeout_ms`, `typesafe.batch_size`, `typesafe.http_concurrency`; `jev.api_url`, `jev.model`, `jev.threshold`, `jev.batch_size`, `jev.concurrency`. `jev.api_key` / `jev.api_url` are superuser-set.

`pgembed.bootstrap()` already points the native family at a working provider: it writes `jev.api_url = 'https://api.tu-zi.com/v1/systemone'` and `jev.model = 'jev-1.14'` into `postgresql.conf` before the postmaster starts, and accepts the token from `TYPESAFE_API_KEY` or `TUZI_API_KEY`. Override per call with `bootstrap(settings={"jev.model": "jev-1.13"})`; plain `get_server()` writes nothing, so the extension defaults apply. The key is only ever read from the environment — nothing secret is written to `postgresql.conf`.

Without a key, `typesafe.mock_response` (superuser-only) short-circuits the **`typesafe_*` HTTP path**. Native `jev_*` offline tests need the loopback mock in the `pg_typesafe` repo (`test/jev/mock_api.py`), not this GUC.

```python
import os

os.environ.setdefault("TUZI_API_KEY", "...")  # inherited by the server process

with pgembed.bootstrap("/path/to/my/data/dir") as server:
    print(server.psql("SELECT extversion FROM pg_extension WHERE extname = 'typesafe';"))
    print(server.psql("SELECT jev_version();"))
    server.psql("CREATE TABLE city(name text, country text)")
    server.psql("INSERT INTO city VALUES ('Paris', 'Germany')")
    # Real call: jev_prob is 0.97 for this row against api.tu-zi.com/jev-1.14.
    print(server.psql("SELECT jev_prob(city, 'the country is Germany') FROM city;"))
    # Mock mode needs no key and no network (typesafe_* family only):
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

Native predicates (live key or Jev mock server — not `typesafe.mock_response`):

```sql
SET jev.api_key = 'tsk_...';          -- superuser, or TYPESAFE_API_KEY in the environment
SET jev.api_url = 'https://api.tu-zi.com/v1/systemone';  -- bootstrap() default
SELECT name FROM cities WHERE jev(cities, 'the country is Germany');
SELECT name, jev_prob(cities, 'the country is Germany') AS p FROM cities;
SELECT jev_choice(cities, 'which continent?', ARRAY['europe','asia','americas']) FROM cities;
```

A runnable mock walkthrough is `examples/typesafe_jev.py`. See the [pg_typesafe README](https://github.com/giuliosmall/pg_typesafe) (and this workspace's local `pg_typesafe` tree) for `_many` batch variants, `typesafe_last_request()`, `jev_eval` / `jev_stats`, and resource GUCs. Existing databases on **0.0.1** can `ALTER EXTENSION typesafe UPDATE;` to 0.1.0.

The wheel recipe pins upstream `11d9358` (catalog `0.1.0`) and no longer applies
`patches/pg_typesafe-pg18-noreturn.patch`. `tools/generate_bundle_metadata.py
--expected-version pg_typesafe=0.1.0` makes attestation fail loudly when the
installed payload came from an older pin, instead of stamping it with a pin it
does not match; `make -C pgbuild reattest` re-runs attestation against the
current prefix without rebuilding anything.

#### Using OpenRouter instead of a TypeSafe account

OpenRouter hosts Jev as [`~typesafe/jev-latest`](https://openrouter.ai/~typesafe/jev-latest) (an alias over versioned ids like `typesafe/jev-1.13-20260917`) and serves the same System One wire schema through its **Decisions API (alpha)** — the `typesafe_*` HTTP client works against it unchanged. Point the GUCs at OpenRouter and put your OpenRouter key (`sk-or-v1-...`) in `TYPESAFE_API_KEY`:

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
- **Extensions:** the bundled extension set is built for those release targets. `pg_net` and `pgsql_http` additionally require **libcurl ≥ 7.83** (`pg_typesafe` links the same libcurl provider and needs ≥ 7.61): CI builds a private curl 8 via `tools/build_curl.sh` (auditwheel vendors `libcurl.so.4` into the Linux wheels); on macOS they link the SDK/system libcurl. Local Linux hosts need a curl that new, or run `tools/build_curl.sh` and pass `PG_NET_CURL_PREFIX` / `PGSQL_HTTP_CURL_CONFIG` (`pg_typesafe` picks the prefix up from `PG_NET_CURL_PREFIX` via pkg-config). `firebird_fdw` vendors [libfq](https://github.com/ibarwick/libfq) 0.6.2 and the Firebird 5.0.3 **client** libraries (plus libtommath on Linux); it does not ship a Firebird server. musl builds skip it. `pgcrypto` needs OpenSSL: Linux uses the distro library; macOS vendors Homebrew `openssl@3` into the prefix (`@loader_path`) because Apple no longer ships `/usr/lib/libssl`. musl builds skip VectorChord, `pg_jsonschema`, and `stannum`. `provsql` needs Boost headers and `libboost_serialization` at build time (Homebrew `boost` on macOS, where the static archive is linked into `provsql.dylib`; `boost-devel` on Linux, where auditwheel vendors the shared library). Its `uuid-ossp` dependency is built from the pinned PostgreSQL source against a private static OSSP UUID, so neither artifact records a Homebrew path.
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

ProvSQL has the same preload requirement. `create_extension("provsql")` installs
its `uuid-ossp` dependency first (ProvSQL's control file declares
`requires = 'uuid-ossp'`):

```python
with pgembed.get_server(
    "/path/to/my/data/dir",
    shared_preload_libraries=["provsql"],
) as server:
    server.create_extension("provsql")
    server.psql("SET search_path TO public, provsql;")
```

### Building specific extensions

All source fetches are pinned and shallow: the PostgreSQL tree is a checksummed
tarball at a fixed commit (`POSTGRES_TARBALL_URL` / `POSTGRES_TARBALL_SHA256`),
and every extension clone uses `--depth 1` or `fetch --depth 1`. That matters on
a slow link — git smart-HTTP measured ~80 KB/s here while the same 30 MB over
codeload HTTPS took 14 s, so the tarball is roughly 40x faster than
`git clone --single-branch`.

A full rebuild is still a PostgreSQL compile plus four Rust extensions;
re-downloading is the only part the network can ruin. Two knobs if you need to
change the transport:

```bash
# crates.io: use a reachable mirror (static.crates.io measured ~11 KB/s here,
# rsproxy ~400 KB/s). Checksums are unchanged, existing caches still work.
cat >> ~/.cargo/config.toml <<'EOF'
[source.crates-io]
replace-with = "rsproxy-sparse"
[source.rsproxy-sparse]
registry = "sparse+https://rsproxy.cn/index/"
EOF

# git: route every github.com fetch through a mirror, no Makefile edit needed.
git config --global url."https://<mirror>/".insteadOf "https://github.com/"
```

`insteadOf` does **not** cover the tarball downloads (`curl`), which need
`https_proxy` or an edited `*_URL` rather than a git rewrite.

To build only specific extensions:

```bash
# Build only pgvector
make pgvector

# Build only ProvSQL (also builds its uuid-ossp dependency)
make provsql

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

# Build only gnomon (SQL core + optional C module, both from one tree)
make gnomon

# Build only pg_jsonschema (Rust/pgrx)
make pg_jsonschema

# Build the Makefile-pinned upstream 0.0.1 (needs libcurl; overwrites a local 0.1.0 overlay)
make pg_typesafe
# Install the local sibling 0.1.0 + native jev into src/pgembed/pginstall instead:
#   bash ../pg_typesafe/scripts/install-into-pgembed.sh

# Build only stannum (Rust/pgrx search engine)
make stannum

# Build specific combination
make EXTENSIONS="pgvector vectorchord timescaledb pg_cron pg_net pgsql_http plsh firebird_fdw pgmq pg_partman pgtap gnomon gnomon_c pg_jsonschema pg_typesafe stannum uuid-ossp provsql" all
```

## History

pgembed is a fork of [pgserver](https://github.com/orm011/pgserver), which was inspired by [postgresql-wheel](https://github.com/michelp/postgresql-wheel). While those projects focused primarily on Linux wheels, pgembed extends the approach with:

- Bundled Darwin/Linux releases for macOS arm64 and Linux x86_64/aarch64
- Robust process management and cleanup
- Built-in pgvector, VectorChord, Apache AGE, psql_bm25s, TimescaleDB, pg_cron, pg_net, firebird_fdw, pgmq, pg_partman, pgTAP, gnomon, pg_jsonschema, pg_typesafe, stannum, uuid-ossp, and ProvSQL extensions
