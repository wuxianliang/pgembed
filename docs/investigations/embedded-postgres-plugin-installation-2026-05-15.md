# Investigation: Embedded PostgreSQL Plugin Installation

## Summary
pgembed does not install arbitrary PostgreSQL plugins at application runtime. Its supported model is build-time bundling: PostgreSQL and selected server-side extension artifacts are compiled/installed into `src/pgembed/pginstall`, packaged into the wheel, detected by hard-coded Python registry logic, and then activated per database with `CREATE EXTENSION`.

To add more plugins safely, a new extension must be built against pgembed’s embedded PostgreSQL `pg_config`, have its `.so` plus `.control`/`.sql` files installed into the embedded PostgreSQL layout, be registered/detected by Python code, be packaged into wheels for each supported platform, and—if needed—configure `shared_preload_libraries` before server startup.

## Symptoms
- User asks: “这个嵌入式postgres如何安装更多的插件？”
- Repository contains examples and namespace packages for PostgreSQL extensions such as pgvector, pgvectorscale, and pgtextsearch, but the installation flow for additional plugins needs investigation.

## Background / Prior Research

### External PostgreSQL extension installation facts
- PostgreSQL extensions must have `extension_name.control` and SQL install/update scripts under the target PostgreSQL install’s `$(pg_config --sharedir)/extension`.
- Native extensions also need a PostgreSQL-major/platform-compatible shared library under `$(pg_config --pkglibdir)`; SQL files typically refer to it via `MODULE_PATHNAME` or `$libdir`.
- `CREATE EXTENSION name;` only activates already-installed extension files in a database. PostgreSQL does not download or compile native extension artifacts at runtime.
- Source extensions are normally installed against a specific target PostgreSQL using commands such as `make install PG_CONFIG=/path/to/embedded/bin/pg_config`.
- Some extensions require `shared_preload_libraries` before server start, then a restart, then `CREATE EXTENSION`; if a preloaded library is missing, PostgreSQL can fail to start.
- Official docs referenced by explore agent: PostgreSQL `extend-extensions`, `app-pgconfig`, `CREATE EXTENSION`, `xfunc-c`, runtime config / shared preloading, and PGXS documentation.

### External packaging ecosystem facts
- Typical Python packages such as `pgvector`/`pgvector-python` are client adapters and do not install server-side extension binaries; users still need the server extension installed separately.
- Native PostgreSQL extension artifacts are tied to PostgreSQL major version, OS/libc, CPU architecture, and compiler/runtime ABI rather than Python ABI alone.
- Runtime installation can safely mean copying/symlinking already-bundled compatible artifacts into the embedded PostgreSQL layout and then running `CREATE EXTENSION`; it should not mean arbitrary downloading/building unless the project owns a strict compatibility matrix.
- Current pgembed extension packages appear to follow a bundled-plugin pattern: include `.so` plus control/SQL files inside Python packages, then install/copy them into the embedded PostgreSQL distribution.
- More complex extensions such as pg_search and pg_duckdb may require preload/restart behavior and have larger ABI/dependency risks than simple extensions such as pgvector.

## Investigator Findings

### Current flow: build-time artifacts, import-time detection, SQL-only activation
- **Artifact production/installation is build-time, not runtime.** `pgbuild/Makefile` sets `INSTALL_PREFIX := $(shell pwd)/../src/pgembed/pginstall`, so PostgreSQL and extension installs target the Python package tree before wheel creation (`pgbuild/Makefile:2`). PostgreSQL itself is configured with `--prefix=$(INSTALL_PREFIX)` (`pgbuild/Makefile:63-65`) and installed there (`pgbuild/Makefile:73-76`). Extension recipes build against the embedded `pg_config` and run each extension's install step into the same prefix: pgvector (`pgbuild/Makefile:95-98`), pg_duckdb (`pgbuild/Makefile:110-113`), pgvectorscale (`pgbuild/Makefile:131-137`), pg_textsearch (`pgbuild/Makefile:157-161`), and pg_search (`pgbuild/Makefile:182-188`).
- **The wheel bundles whatever is already under `src/pgembed/pginstall`.** The root `Makefile` runs `$(MAKE) -d -C pgbuild all` for `build`, then `python setup.py bdist_wheel` for `wheel` (`Makefile:4-11`). `MANIFEST.in` has a single inclusion rule, `graft src/pgembed/pginstall`, so package artifact inclusion depends on that tree already being populated (`MANIFEST.in:1`). The CFFI build hook is explicitly a dummy and says "The build is done by the Makefile" (`src/pgembed/_build.py:1-5`; `setup.py:1-7`).
- **Runtime command paths assume bundled PostgreSQL already exists.** `_commands.py` resolves `POSTGRES_BIN_PATH` to `<pgembed package>/pginstall/bin` (`src/pgembed/_commands.py:13-18`) and only warns if binaries are missing during development/editable use (`src/pgembed/_commands.py:20-29`). It dynamically exposes command wrappers only by iterating that existing bin directory (`src/pgembed/_commands.py:113-126`).

### Detection / registry behavior
- **The extension registry is hard-coded.** `EXTENSION_PACKAGES` only maps `pgvector`, `pgvectorscale`, and `pgtextsearch` to Python package names (`src/pgembed/__init__.py:23-27`). `EXTENSION_SO_FILES` separately hard-codes `.so` names for `pgvector`, `pgvectorscale`, `pgtextsearch`, `pg_search`, and `pg_duckdb` (`src/pgembed/__init__.py:29-35`). `_detect_extensions()` only loops over the names in `EXTENSION_NAMES`, so unknown/new extensions are invisible until code is changed (`src/pgembed/__init__.py:37-49`).
- **Availability means "known `.so` path exists," not "PostgreSQL can create it."** For package-backed extensions, `_detect_extensions()` imports the hard-coded package and calls `get_extension_path()`, then marks available only if that path exists (`src/pgembed/__init__.py:49-58`). Otherwise it checks `<pgembed>/pginstall/lib/postgresql/<so_file>` and marks available if that bundled `.so` exists (`src/pgembed/__init__.py:62-67`). It does not verify `.control` or SQL install files under `share/postgresql/extension` (`src/pgembed/__init__.py:46-70`).
- **`list_extensions()` and `has_extension()` are import-time cache readers.** `_detect_extensions()` runs once at import (`src/pgembed/__init__.py:141`). `has_extension(name)` returns `AVAILABLE_EXTENSIONS.get(name, False)` without re-detecting (`src/pgembed/__init__.py:73-82`), and `list_extensions()` returns a shallow copy of the same cache (`src/pgembed/__init__.py:85-91`).
- **Extension package stubs can locate libraries and share/control directories, but core detection ignores share paths.** Each stub checks a package-local `.so`, then falls back to `pgembed.EXTENSION_LIB_PATH / "postgresql" / EXTENSION_SO` (`src/pgembed_pgvector/__init__.py:8-23`; `src/pgembed_pgvectorscale/__init__.py:8-23`; `src/pgembed_pgtextsearch/__init__.py:8-23`). They also define `get_extension_share_path()` and look for control files in package-local or base `pginstall/share/postgresql/extension` paths (`src/pgembed_pgvector/__init__.py:26-49`; `src/pgembed_pgvectorscale/__init__.py:26-49`; `src/pgembed_pgtextsearch/__init__.py:26-49`), but `src/pgembed/__init__.py` never calls those methods during detection.

### Activation / server startup behavior
- **`create_extension()` only runs SQL after a cached availability check.** It maps SQL names back to package names (`vector -> pgvector`, `vectorscale -> pgvectorscale`, `pg_textsearch -> pgtextsearch`, plus `pg_search` and `pg_duckdb`) (`src/pgembed/postgres_server.py:329-335`), calls `pgembed.has_extension(pkg_name)` (`src/pgembed/postgres_server.py:337-342`), maps back to the SQL create name (`src/pgembed/__init__.py:94-109`), and executes `CREATE EXTENSION IF NOT EXISTS {create_name};` (`src/pgembed/postgres_server.py:343-344`). There is no code here to copy `.so`, `.control`, or SQL files into PostgreSQL directories.
- **The server lifecycle does not install extension artifacts.** `ensure_pgdata_inited()` initializes `PGDATA` with `initdb` when `PG_VERSION` is absent (`src/pgembed/postgres_server.py:105-171`) or skips initialization when it already exists (`src/pgembed/postgres_server.py:172-173`). `ensure_postgres_running()` constructs `pg_ctl start` arguments for host/socket/log handling (`src/pgembed/postgres_server.py:201-224`) and calls `pg_ctl` (`src/pgembed/postgres_server.py:226-231`). The only `shutil` operation in the class is cleanup deletion of `pgdata` (`src/pgembed/postgres_server.py:294-304`), not extension copying.
- **There is no generic preload-extension API.** Core startup args do not set `shared_preload_libraries` or `dynamic_library_path` (`src/pgembed/postgres_server.py:201-231`). The only preload handling found is the `pg_duckdb` example monkey-patch: it overrides `PostgresServer.ensure_pgdata_inited` before server creation (`examples/pgduckdb_example.py:12-18`, `examples/pgduckdb_example.py:64-65`), searches an example `.venv` for `pgembed/pginstall/lib/postgresql/pg_duckdb.*` (`examples/pgduckdb_example.py:21-38`), then appends `shared_preload_libraries = 'pg_duckdb'` and `dynamic_library_path = '<libdir>'` to `postgresql.conf` (`examples/pgduckdb_example.py:55-61`). It attempts `CREATE EXTENSION IF NOT EXISTS pg_duckdb` only after the server is running and explicitly notes preload is required on failure (`examples/pgduckdb_example.py:301-311`).

### Packaging / CI evidence
- **CI builds artifacts before wheel packaging.** The matrix requests `pgvector pgvectorscale pgtextsearch pg_duckdb` on macOS/Linux and only `pgvector` on Windows (`.github/workflows/build-and-test.yml:13-38`). CI caches both `pgbuild` and `src/pgembed/pginstall`, confirming `pginstall` is a build output worth reusing (`.github/workflows/build-and-test.yml:67-76`, `.github/workflows/build-and-test.yml:132-140`, `.github/workflows/build-and-test.yml:199-208`). In cibuildwheel, `CIBW_BEFORE_ALL_*` installs toolchains, runs `make`, then separately runs `make EXTENSIONS=pg_search` where supported (`.github/workflows/build-and-test.yml:145-196`), and uploads `wheelhouse/*.whl` (`.github/workflows/build-and-test.yml:211-216`).
- **The base package includes extension stubs.** `pyproject.toml` discovers packages under `src` and includes both `pgembed*` and `pgembed_pg*` names (`pyproject.toml:26-29`). The README describes separate extension wheels and lists `pip install pgembed-pgvector`, `pgembed-pgvectorscale`, and `pgembed-pgtextsearch` (`README.md:49-69`), but the root package discovery also includes those namespace-like stub packages in the base build.
- **Declared extension build docs are stale relative to the root `Makefile`.** README suggests targets like `make pgvector`, `make pgvectorscale`, and `make pgtextsearch` (`README.md:92-108`), but the root `Makefile` only defines `build`, `wheel`, `install-wheel`, `install-dev`, `clean`, and `test` (`Makefile:1-24`). Those extension targets exist in `pgbuild/Makefile` instead (`pgbuild/Makefile:41-49`).
- **Test coverage only proves pgvector activation in one path.** The test suite smoke-tests `CREATE EXTENSION vector;` (`tests/test_pgembed.py:225-227`) and otherwise focuses on server lifecycle, sockets, cleanup, stale postmaster handling, and SQLAlchemy connectivity (`tests/test_pgembed.py:82-394`). Linux cibuildwheel tests are disabled in `cibuildwheel_test.bash` (`cibuildwheel_test.bash:6-12`), and Windows cibuildwheel tests are overridden to `true` (`.github/workflows/build-and-test.yml:193`). No tests assert wheel contents, `.control`/SQL availability, `list_extensions()` correctness, or preload extension startup.

### Eliminated hypotheses
- **Eliminated: pgembed downloads/builds/copies extension artifacts at application runtime.** The runtime code paths inspected only initialize/start/stop PostgreSQL and run SQL. Artifact installation occurs via `pgbuild/Makefile` using `PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config` and install commands before wheel creation (`pgbuild/Makefile:95-98`, `pgbuild/Makefile:110-113`, `pgbuild/Makefile:157-161`, `pgbuild/Makefile:182-188`); `PostgresServer.create_extension()` only issues SQL (`src/pgembed/postgres_server.py:314-344`).
- **Eliminated: installing an arbitrary Python package automatically makes a new PostgreSQL extension discoverable.** Detection is restricted to `EXTENSION_NAMES` and hard-coded package/`.so` maps (`src/pgembed/__init__.py:23-43`), with cached lookup functions (`src/pgembed/__init__.py:73-91`).
- **Eliminated: `has_extension()` guarantees `CREATE EXTENSION` will succeed.** It only verifies a package/bundled `.so` exists (`src/pgembed/__init__.py:49-70`); it does not verify matching `.control` or SQL files, server preload state, ABI compatibility, or PostgreSQL catalog visibility.
- **Eliminated: pg_duckdb has first-class preload support in `PostgresServer`.** The only preload configuration is an example-level monkey-patch that edits `postgresql.conf` before startup (`examples/pgduckdb_example.py:12-65`); core startup has no preload configuration hook (`src/pgembed/postgres_server.py:201-231`).

### Practical conclusions
- Adding more embedded PostgreSQL plugins today means adding a **build-time recipe** that compiles/installs the extension into `src/pgembed/pginstall` using the embedded PostgreSQL `pg_config`, ensuring the resulting `lib/postgresql` and `share/postgresql/extension` artifacts are present before wheel build, then adding the extension to the hard-coded Python registry if `list_extensions()` / `has_extension()` / `create_extension()` should know about it.
- `CREATE EXTENSION` in pgembed is an activation step only; it assumes the extension files were already installed into the embedded PostgreSQL layout by the build process or are otherwise already visible to PostgreSQL.
- Preload extensions such as `pg_duckdb` need an explicit pre-start configuration path. The current example proves the workaround shape (`shared_preload_libraries` plus `dynamic_library_path` before `pg_ctl start`) but not a reusable API.
- The most important gaps for additional plugin support are: a non-hard-coded extension registry/metadata model, verification of control/SQL files in addition to `.so`, support for pre-start configuration/restart semantics, wheel-content tests, and CI coverage for each supported platform/extension combination.

## Investigation Log

### Phase 1 - Initial Assessment
**Hypothesis:** The project embeds a PostgreSQL binary and provides a packaging convention for selected extensions; adding plugins likely requires both build-time bundling and runtime registration.
**Findings:** Initial file map shows core package under `src/pgembed`, extension packages under `src/pgembed_pgvector`, `src/pgembed_pgvectorscale`, and `src/pgembed_pgtextsearch`, plus examples for these extensions.
**Evidence:** User-provided file map.
**Conclusion:** Needs external PostgreSQL extension context plus workspace investigation.

### Phase 1.5 - External PostgreSQL Extension Facts
**Hypothesis:** PostgreSQL server extensions must be installed into the target PostgreSQL installation before `CREATE EXTENSION` can work.
**Findings:** External PostgreSQL docs and ecosystem checks confirm that extension control/SQL files must live under the target `sharedir/extension`, native libraries under `pkglibdir`, and native binaries must match the PostgreSQL major version/platform/ABI. Some extensions require `shared_preload_libraries` before server start.
**Evidence:** See `## Background / Prior Research`.
**Conclusion:** Confirmed. Runtime `CREATE EXTENSION` is activation only, not artifact installation.

### Phase 2 - Workspace Context Gathering
**Hypothesis:** The repository has a hard-coded bundled-extension architecture.
**Findings:** Context builder selected core runtime files, extension stubs, build metadata, tests, examples, README, and CI. It identified a build-time bundle plus runtime detection/activation model.
**Evidence:** Selected files include `src/pgembed/__init__.py`, `src/pgembed/postgres_server.py`, `src/pgembed/_commands.py`, extension packages, `MANIFEST.in`, `Makefile`, `.github/workflows/build-and-test.yml`, README, tests, and examples.
**Conclusion:** Confirmed enough for deeper pair investigation.

### Phase 3 - Pair Investigator
**Hypothesis:** Runtime installation/copying does not exist; artifacts are bundled at build time and `create_extension()` only activates them.
**Findings:** Pair investigator appended structured findings above. Key verified points: `_commands.py` expects `pginstall/bin` to exist (`src/pgembed/_commands.py:16-29`); hard-coded detection checks known `.so` paths at import time (`src/pgembed/__init__.py:23-70`, `src/pgembed/__init__.py:141`); `PostgresServer.create_extension()` only checks availability and runs SQL (`src/pgembed/postgres_server.py:314-344`); startup code initializes PGDATA and starts `pg_ctl` but has no generic extension-copying or preload API (`src/pgembed/postgres_server.py:105-231`); `pg_duckdb` preload handling is example-level monkeypatching (`examples/pgduckdb_example.py:12-65`).
**Evidence:** See `## Investigator Findings` plus direct spot checks of the cited files.
**Conclusion:** Confirmed.

### Phase 4 - Build Evidence Verification
**Hypothesis:** The hidden/ignored `pgbuild/Makefile` contains the actual build-time extension installation recipes.
**Findings:** Local read-only verification found `pgbuild/Makefile` and confirmed it sets `INSTALL_PREFIX := $(shell pwd)/../src/pgembed/pginstall`, configures PostgreSQL with `--prefix=$(INSTALL_PREFIX)`, and builds/installs pgvector, pg_duckdb, pgvectorscale, pgtextsearch, and pg_search using the embedded `$(INSTALL_PREFIX)/bin/pg_config`. RepoPrompt selection could not load `pgbuild/Makefile` because it is filtered from the loaded workspace view, so these references are verified supplemental evidence.
**Evidence:** `pgbuild/Makefile:2`, `pgbuild/Makefile:41-49`, `pgbuild/Makefile:63-76`, `pgbuild/Makefile:95-98`, `pgbuild/Makefile:110-113`, `pgbuild/Makefile:131-137`, `pgbuild/Makefile:157-161`, `pgbuild/Makefile:182-188`.
**Conclusion:** Confirmed. The project’s build system installs extension artifacts into the embedded PostgreSQL tree before wheel packaging.

## Root Cause
The core issue is semantic: pgembed treats PostgreSQL extensions as **build-time bundled server artifacts**, not as arbitrary runtime-installable Python plugins. `pip install pgembed-pgvector` or similar can only work when the project has already produced compatible extension artifacts and packaged/discovered them. At runtime, `pgembed.has_extension()` mostly checks whether a known `.so` exists, and `PostgresServer.create_extension()` only executes `CREATE EXTENSION IF NOT EXISTS ...`; neither path compiles, downloads, copies, or validates full extension payloads.

Concrete evidence:
- Embedded PostgreSQL binaries are expected under `pgembed/pginstall/bin` (`src/pgembed/_commands.py:16-29`).
- Extension detection is hard-coded to known names and `.so` files (`src/pgembed/__init__.py:23-70`).
- Detection runs once at import time and `has_extension()` / `list_extensions()` read that cache (`src/pgembed/__init__.py:73-91`, `src/pgembed/__init__.py:141`).
- Extension activation is SQL-only (`src/pgembed/postgres_server.py:314-344`).
- The wheel includes the already-built embedded tree via `graft src/pgembed/pginstall` (`MANIFEST.in:1`), and root `Makefile` delegates build to `pgbuild` before wheel creation (`Makefile:4-11`).
- Supplemental verified build evidence shows `pgbuild/Makefile` installs PostgreSQL and extensions into `src/pgembed/pginstall` using the embedded `pg_config`.

## Recommendations
1. **For users of existing plugins:** install the supported pgembed extension packages, then activate extensions per database:
   - `pip install pgembed-pgvector pgembed-pgvectorscale pgembed-pgtextsearch`
   - `pg.create_extension("vector")`, `pg.create_extension("vectorscale")`, or `pg.create_extension("pg_textsearch")`.
2. **For adding a new plugin:** add a build-time recipe that compiles/installs the extension against `src/pgembed/pginstall/bin/pg_config`, producing at least:
   - `pginstall/lib/postgresql/<extension>.so` (or platform equivalent)
   - `pginstall/share/postgresql/extension/<extension>.control`
   - `pginstall/share/postgresql/extension/<extension>--*.sql`
3. **Register the plugin in Python:** update the hard-coded extension metadata in `src/pgembed/__init__.py` (`EXTENSION_NAMES`, `EXTENSION_SO_FILES`, `EXTENSION_PACKAGES`, and create-name mapping) or replace it with a proper plugin registry/metadata mechanism.
4. **Package and test the artifacts:** ensure wheel packaging includes native libraries, control files, SQL files, and dependent shared libraries where applicable. Add tests that verify wheel contents, `list_extensions()`, `has_extension()`, and actual `CREATE EXTENSION` success.
5. **Handle preload extensions explicitly:** for plugins such as `pg_duckdb` that require `shared_preload_libraries`, add a first-class pre-start configuration API instead of relying on example-level monkeypatching.
6. **Maintain a compatibility matrix:** document and test PostgreSQL major version, OS, architecture, libc, Rust/C/C++ toolchain requirements, and unsupported platforms for each extension.

## Preventive Measures
- Do not assume a Python client package installs PostgreSQL server-side extension files.
- Do not treat `.so` existence alone as sufficient; verify `.control` and `.sql` files and test `CREATE EXTENSION`.
- Do not load arbitrary third-party native extensions unless built against pgembed’s embedded PostgreSQL and target platform.
- Require CI smoke tests for every bundled extension/platform combination.
- For preload extensions, require tests that initialize a fresh PGDATA, write required config before startup, start PostgreSQL, and then run `CREATE EXTENSION`.
- Keep README/build docs aligned with the actual root `Makefile` and `pgbuild/Makefile` targets.
