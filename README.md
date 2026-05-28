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
- **Vector search ready**: Includes [pgvector](https://github.com/pgvector/pgvector), [pgvectorscale](https://github.com/timescale/pgvectorscale), and [VectorChord](https://github.com/tensorchord/VectorChord) extensions for vector similarity queries and high-performance vector storage
- **Graph ready**: Includes [Apache AGE](https://github.com/apache/age) and [pgGraph](https://github.com/evokoa/pggraph) (`CREATE EXTENSION graph;`, Python alias `pggraph`) for graph traversals and property graphs
- **Text search ready**: Includes [pg_textsearch](https://github.com/timescale/pg_textsearch) and [psql_bm25s](https://github.com/Intelligent-Internet/psql_bm25s) extensions for BM25-based full-text search with ranking
- **Time-series ready**: Includes [TimescaleDB](https://github.com/timescale/timescaledb) for hypertables and time-series workloads

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

pgembed supports optional PostgreSQL extensions as separate packages. Install the extensions you need:

```bash
# Base installation (PostgreSQL only)
pip install pgembed

# With specific extensions (separate wheels)
pip install pgembed-pgvector
pip install pgembed-pgvectorscale
pip install pgembed-pgtextsearch

# Multiple extensions
pip install pgembed-pgvector pgembed-pgvectorscale pgembed-pgtextsearch
```

Available extensions:
- Package-backed wheels: `pgembed-pgvector`, `pgembed-pgvectorscale`, `pgembed-pgtextsearch`
- Bundled native extensions built by `pgbuild/Makefile`: `pg_duckdb`, `pg_search`, `vectorchord`, `graph` (pgGraph; Python alias `pggraph`), `age`, `psql_bm25s`, `timescaledb`


### Checking available extensions

```python
import pgembed

# Check which extensions are available
print(pgembed.list_extensions())
# {'pgvector': True, 'pgvectorscale': True, 'pgtextsearch': False, 'pg_search': False, 'pg_duckdb': True, 'vectorchord': True, 'graph': True, 'pggraph': True, 'age': True, 'psql_bm25s': True, 'timescaledb': True}

# Check if a specific extension is available
if pgembed.has_extension('pggraph'):
    # Create the extension
    server.create_extension('pggraph')
```

### Platform Support

- **pgvector**: Works on Linux, macOS (Intel & Apple Silicon), Windows
- **pgvectorscale**: Works on Linux, macOS (Intel & Apple Silicon). NOT available on Alpine Linux or Windows (requires Rust)
- **pgtextsearch**: Works on Linux, macOS (Intel & Apple Silicon). NOT available on Alpine Linux or Windows (requires Rust)
- **Bundled native extensions** (`pg_duckdb`, `pg_search`, `vectorchord`, `graph` / pgGraph / `pggraph`, `age`, `psql_bm25s`, `timescaledb`): built from source via `pgbuild/Makefile`; availability depends on platform and toolchain

TimescaleDB requires `shared_preload_libraries = 'timescaledb'` before PostgreSQL starts. Configure it when creating the server:

```python
import pgembed

with pgembed.get_server(
    "/path/to/my/data/dir",
    shared_preload_libraries="timescaledb",
) as server:
    server.create_extension("timescaledb")
```

### Building specific extensions

To build only specific extensions:

```bash
# Build only pgvector
make pgvector

# Build only pgvectorscale
make pgvectorscale

# Build only pgtextsearch
make pgtextsearch

# Build only vectorchord
make vectorchord

# Build only graph (pgGraph)
make graph

# Build only age
make age

# Build only psql_bm25s
make psql_bm25s

# Build only timescaledb
make timescaledb

# Build only pg_duckdb
make pg_duckdb

# Build specific combination
make EXTENSIONS="pgvector pgtextsearch graph timescaledb" all
```

## History

pgembed is a fork of [pgserver](https://github.com/orm011/pgserver), which was inspired by [postgresql-wheel](https://github.com/michelp/postgresql-wheel). While those projects focused primarily on Linux wheels, pgembed extends the approach with:

- Multi-platform support (Linux, macOS, Windows)
- Robust process management and cleanup
- Built-in pgvector, pgvectorscale, pg_textsearch, pg_duckdb, pg_search, vectorchord, graph (pgGraph / pggraph), age, psql_bm25s, and timescaledb extensions
