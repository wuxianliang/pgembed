![Python Version](https://img.shields.io/badge/python-3.12%2C%203.13%2C%203.14-blue)
![Postgres Version](https://img.shields.io/badge/PostgreSQL-17-blue)

![Linux Support](https://img.shields.io/badge/Linux%20Support-manylinux%2C%20alpine%2C%20x64/arm64-green)
![macOS Apple Silicon Support >=11](https://img.shields.io/badge/macOS%20Apple%20Silicon%20Support-%E2%89%A513(Tahoe)-green)
![Windows Support >= 2022](https://img.shields.io/badge/Windows%20AMD64%20Support-%E2%89%A52022-green)

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

## What pgembed gives you

- **Pip-installable PostgreSQL binaries**: Pre-built wheels for Linux, macOS (Apple Silicon & Intel), and Windows
- **No admin rights needed**: Runs without `sudo` or root access
- **Handles edge cases**: Works in Docker containers, Google Colab, and environments with multiple PostgreSQL installations
- **Simple initialization**: `pgembed.get_server(MY_DATA_DIR)` handles `initdb`, port management, and process cleanup automatically
- **Vector search ready**: Includes [pgvector](https://github.com/pgvector/pgvector) and [VectorChord](https://github.com/tensorchord/VectorChord) for vector similarity queries and high-performance vector storage
- **Graph ready**: Includes [Apache AGE](https://github.com/apache/age) for graph traversals and property graphs
- **Text search ready**: Includes [psql_bm25s](https://github.com/Intelligent-Internet/psql_bm25s) for BM25-based full-text search with ranking
- **Time-series ready**: Includes [TimescaleDB](https://github.com/timescale/timescaledb) for hypertables and time-series workloads
- **DuckDB in Postgres**: Includes [pg_duckdb](https://github.com/duckdb/pg_duckdb) for DuckDB's columnar query engine inside PostgreSQL
- **Scheduling & HTTP**: Includes [pg_cron](https://github.com/citusdata/pg_cron) (job scheduler) and [pg_net](https://github.com/supabase/pg_net) (async HTTP client)

## Quick start

```python
import pgembed

# Initialize and start the server
pgembed.get_server("/path/to/my/data/dir")

# Connect and use like any PostgreSQL database
# ... your database code here
# Look in examples/*.py for more complete examples that could be run via uv
```

PostgreSQL binaries are available at `pgembed.POSTGRES_BIN_PATH` if you need direct access to tools like `initdb`, `pg_ctl`, `psql`, or `pg_config`.

## Extensions

pgembed bundles a curated set of PostgreSQL extensions, built from source by `pgbuild/Makefile` and shipped inside the wheel:

| Extension | `CREATE EXTENSION` | pgembed key | Preload | Notes |
|---|---|---|---|---|
| [pgvector](https://github.com/pgvector/pgvector) | `vector` | `pgvector` | — | required by VectorChord |
| [VectorChord](https://github.com/tensorchord/VectorChord) | `vchord` | `vectorchord` | `vchord` | high-perf vector storage (Rust/pgrx) |
| [Apache AGE](https://github.com/apache/age) | `age` | `age` | — | graph / openCypher |
| [psql_bm25s](https://github.com/Intelligent-Internet/psql_bm25s) | `psql_bm25s` | `psql_bm25s` | — | BM25 full-text search |
| [TimescaleDB](https://github.com/timescale/timescaledb) | `timescaledb` | `timescaledb` | `timescaledb` | hypertables / time-series |
| [pg_duckdb](https://github.com/duckdb/pg_duckdb) | `pg_duckdb` | `pg_duckdb` | `pg_duckdb` | DuckDB in Postgres |
| [pg_cron](https://github.com/citusdata/pg_cron) | `pg_cron` | `pg_cron` | `pg_cron` | job scheduler |
| [pg_net](https://github.com/supabase/pg_net) | `pg_net` | `pg_net` | `pg_net` | async HTTP (requires libcurl) |

`pgembed-pgvector` and `pgembed-pgduckdb` are also published as standalone wheels; the rest are bundled into the base `pgembed` wheel.

### Checking available extensions

```python
import pgembed

# Check which extensions are available
print(pgembed.list_extensions())
# {'pgvector': True, 'pg_duckdb': True, 'vectorchord': True, 'age': True, 'psql_bm25s': True, 'timescaledb': True, 'pg_cron': True, 'pg_net': True}

# Check if a specific extension is available, then create it
if pgembed.has_extension('vectorchord'):
    server.create_extension('vchord')
```

### Platform Support

- **pgvector**, **age**, **psql_bm25s**: C extensions; work on Linux, macOS, and Windows.
- **VectorChord**: Rust/pgrx; Linux and macOS (Apple Silicon & Intel). Not available on Alpine (musl) or Windows.
- **TimescaleDB**: Linux and macOS. Not on Windows.
- **pg_duckdb**: Linux and macOS. Not on Windows.
- **pg_cron**, **pg_net**: C extensions; Linux and macOS. `pg_net` additionally requires libcurl (`libcurl-dev` / `libcurl-devel`, or `curl-dev` on Alpine).

### Preload before start

TimescaleDB, VectorChord, pg_duckdb, pg_cron, and pg_net must be in `shared_preload_libraries` **before** PostgreSQL starts. Configure them when creating the server:

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

### Known limitations

**pg_duckdb + TimescaleDB (`time_bucket`).** Both extensions define `public.time_bucket(interval, …)` with identical signatures, so they collide at `CREATE EXTENSION` time. `server.create_extension("pg_duckdb")` handles this automatically: when TimescaleDB is available it is created **first**, so pg_duckdb's own conflict guard skips its `public.time_bucket` (falling back to `duckdb.time_bucket`) and both install cleanly. If you create extensions by hand via `psql`, create TimescaleDB **before** pg_duckdb.

> ⚠️ Beyond the name collision, running pg_duckdb and TimescaleDB in the **same PostgreSQL instance** can, under certain mixed queries, trigger a planner crash (upstream [pg_duckdb#845](https://github.com/duckdb/pg_duckdb/issues/845)). It does not reproduce in basic use; for workloads that heavily mix both engines, run them in **separate pgembed instances** (separate `pgdata` directories via `pgembed.get_server(...)`).

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

# Build only pg_duckdb
make pg_duckdb

# Build only pg_cron
make pg_cron

# Build only pg_net (needs libcurl)
make pg_net

# Build specific combination
make EXTENSIONS="pgvector vectorchord timescaledb pg_cron pg_net" all
```

## History

pgembed is a fork of [pgserver](https://github.com/orm011/pgserver), which was inspired by [postgresql-wheel](https://github.com/michelp/postgresql-wheel). While those projects focused primarily on Linux wheels, pgembed extends the approach with:

- Multi-platform support (Linux, macOS, Windows)
- Robust process management and cleanup
- Built-in pgvector, VectorChord, Apache AGE, psql_bm25s, TimescaleDB, pg_duckdb, pg_cron, and pg_net extensions
