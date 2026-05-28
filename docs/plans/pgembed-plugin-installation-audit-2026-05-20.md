# pgembed Plugin Installation Audit: Plan

## Goal
确认 pgembed 当前哪些 PostgreSQL 插件/扩展已经成功编译并被打包，哪些只是“构建脚本跑过但产物不完整”或“仅示例可用”，并把失败项隔离到不会影响基础启动的状态；随后补齐缺失的插件构建、注册、打包和验证链路，让 pgembed 最终稳定包含预期插件集合。

## Background
- pgembed 的插件支持是**构建期打包**而非运行期下载/编译：`pgbuild/Makefile` 将 PostgreSQL 和扩展安装到 `src/pgembed/pginstall`，然后 wheel 直接打包该目录（`MANIFEST.in:1`，`Makefile:4-11`）。
- 运行时 `PostgresServer.create_extension()` 只做 `CREATE EXTENSION`，不会复制 `.so`、`.control` 或 SQL 文件（`src/pgembed/postgres_server.py:314-344`）。
- 扩展可用性目前依赖硬编码 registry：`EXTENSION_NAMES`、`EXTENSION_SO_FILES`、`EXTENSION_PACKAGES` 以及 import-time 缓存检测（`src/pgembed/__init__.py:23-91`、`src/pgembed/__init__.py:141`）。
- 当前仓库文档和示例显示已有 pgvector / pgvectorscale / pgtextsearch / pg_duckdb 等插件路径，但 preload 型插件（如 pg_duckdb）仍是示例级别 monkey-patch（`examples/pgduckdb_example.py:12-65`, `examples/pgduckdb_example.py:301-311`）。
- 关键风险：`PostgresServer.ensure_pgdata_inited()` 目前会写入 `shared_preload_libraries = 'age'`，而 `age` 并没有被当前证据链证明为稳定可用；这可能直接破坏基础启动。
- 先前调查结论：`has_extension()` 只证明已知 `.so` 存在，不足以保证 `.control` / SQL / preload / ABI 都正确；CI 也没有覆盖所有插件组合（`.github/workflows/build-and-test.yml:13-38`, `:145-216`）。

## Background framing to preserve
Use this four-level lens throughout the audit:
1. **Documented** — README / examples / docs mention the plugin.
2. **Built** — CI or local build recipes produce artifacts for it.
3. **Packaged** — the wheel contains the complete install payload under `src/pgembed/pginstall`.
4. **Creatable** — `CREATE EXTENSION` succeeds against a running embedded server.

This distinction is the core audit output; do not collapse it back into a single boolean.

## Approach
1. **Audit current state first, before changing behavior.** Inventory each known plugin across the four-level lens above so the repo can distinguish “documented,” “built,” “packaged,” and “creatable.” Treat `pgvector`, `pgvectorscale`, `pgtextsearch`, `pg_duckdb`, `pg_search`, `age`, `vectorchord`, and `psql_bm25s` as separate cases instead of one lumped supported set.
2. **Fix the startup hazard before broader registry work.** Remove or gate the unconditional `age` preload path so base `get_server()`/`PostgresServer` startup remains safe even when optional plugins are missing.
3. **Normalize extension metadata and discovery.** Replace the boolean-only/`.so`-only idea of availability with metadata that can report partial installs and missing share files, while keeping the existing public API stable.
4. **Make preload-sensitive plugins explicit.** Keep `pg_duckdb`/other preload-required plugins opt-in at server creation, not hidden in examples or default config.
5. **Add audit-grade tests and CI checks.** Separate artifact/discovery tests from server-boot/activation tests so missing optional plugins do not fail normal usage, but incomplete plugin packaging is still visible in CI.
6. **Update docs only after behavior is correct.** README and examples should describe the real support levels and startup requirements after the code path is settled.

## Work Items
### Item 1 — Audit the current plugin matrix
**Goal:** Determine, for each known plugin, whether it is documented, built, packaged, and actually creatable.
**Done when:** The plan has a per-plugin matrix with one row per extension and four explicit status columns; unresolved rows are labeled clearly instead of being implied by registry presence.
**Key files:** `docs/investigations/embedded-postgres-plugin-installation-2026-05-15.md`, `.github/workflows/build-and-test.yml`, `README.md`, `tests/test_pgembed.py`, `examples/pgduckdb_example.py`
**Dependencies:** None.
**Size:** M

### Item 2 — Remove the default `age` preload risk
**Goal:** Make base embedded PostgreSQL startup safe even when optional plugins are absent.
**Done when:** `ensure_pgdata_inited()` no longer unconditionally writes `shared_preload_libraries = 'age'`; existing pgdata directories are handled without forcing `age` into the config; baseline startup tests pass without `age` present.
**Key files:** `src/pgembed/postgres_server.py`, `tests/test_pgembed.py`
**Dependencies:** Item 1 (to confirm whether `age` is truly intended as bundled or optional).
**Size:** S

### Item 3 — Tighten extension discovery and status reporting
**Goal:** Distinguish “known library exists” from “complete extension install is usable.”
**Done when:** Extension discovery can report complete vs partial vs missing status using both library and share/SQL artifacts, while preserving backward-compatible `has_extension()` / `list_extensions()` behavior for callers.
**Key files:** `src/pgembed/__init__.py`, `src/pgembed/postgres_server.py`, `src/pgembed_pgvector/__init__.py`, `src/pgembed_pgvectorscale/__init__.py`, `src/pgembed_pgtextsearch/__init__.py`
**Dependencies:** Item 1.
**Size:** M

### Item 4 — Make preload-required plugins explicit
**Goal:** Support preload-sensitive extensions without hiding their requirements in examples or monkey patches.
**Done when:** The server API has a clear way to request preload libraries before startup, and preload-required plugins fail with a direct, actionable error if started without that configuration.
**Key files:** `src/pgembed/postgres_server.py`, `examples/pgduckdb_example.py`, `examples/PGDUCKDB_ARCHITECTURE.md`
**Dependencies:** Item 2, Item 3.
**Size:** M

### Item 5 — Add audit-grade tests for packaging and activation
**Goal:** Prove the matrix from Item 1 in automated checks without making optional failures break the base package.
**Done when:** Tests separately cover discovery/packaging, normal activation, and preload-required activation; missing optional plugins no longer mask packaging gaps, but they also no longer break plain startup.
**Key files:** `tests/test_pgembed.py`, `cibuildwheel_test.bash`, `.github/workflows/build-and-test.yml`
**Dependencies:** Item 2, Item 3, Item 4.
**Size:** M

### Item 6 — Align docs and examples with real support levels
**Goal:** Make the public docs reflect the audited support matrix and the new startup/preload rules.
**Done when:** README and examples distinguish bundled/tested vs experimental vs preload-required plugins, and the documented setup matches the actual root build flow.
**Key files:** `README.md`, `examples/pgduckdb_example.py`, `examples/PGDUCKDB_ARCHITECTURE.md`
**Dependencies:** Item 1 through Item 5.
**Size:** S

## Open Questions
- Is `age` meant to remain a first-class bundled extension, or should it be removed from default startup entirely?
- Does `pg_search` require preload or any other startup-time configuration beyond a normal `CREATE EXTENSION` flow?
- Which plugins are intentionally unsupported on some platforms versus simply not yet verified?

## References
- `docs/investigations/embedded-postgres-plugin-installation-2026-05-15.md`
- `docs/reviews/pgembed-plugin-audit-plan-critique-2026-05-20.md`
- `src/pgembed/__init__.py`
- `src/pgembed/postgres_server.py`
- `examples/pgduckdb_example.py`
- `examples/PGDUCKDB_ARCHITECTURE.md`
- `README.md`
- `.github/workflows/build-and-test.yml`
- `tests/test_pgembed.py`
- `pgbuild/Makefile` (verified in prior investigation)
