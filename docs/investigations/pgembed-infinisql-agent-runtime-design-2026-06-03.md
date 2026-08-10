# Investigation: pgembed 专用 infinisql / pgsql Agent Runtime 设计

> **[Superseded] pg_duckdb has since been removed from pgembed.** This investigation is a dated historical record; its statements about the `pg_duckdb` entry in pgembed's extension registry no longer reflect the current codebase.

## Summary
结论：不要逐行迁移现有 infinisynapse Infinity SQL / NestJS / Java Spark Byzer runtime；应新建 pgembed-native `pgsql` runtime。保留现有产品概念（SQL tool UX、schema inspector、RAG/memory、sub-agent roles、task snapshots、tool/skill/template manifests），但底层改为 embedded Postgres + extension manifest + safe SQL executor + Postgres-native hybrid retrieval + provenance event log + Agno/DSPy/ActiveGraph 可选层。

## Symptoms
- 现有 infinisql 面向多数据库/多后端 inspector 与工具链，pgembed 场景可能只需要 Postgres-native runtime。
- pgembed 已组合向量、图、全文/BM25 等 Postgres 扩展，部分 RAG、检索、审计、任务模块可能可以下沉到 SQL/扩展或由新框架实现。
- 预期可能采用 Agno 或 DSPy 作为 agent/programming framework，审计可考虑 ActiveGraph。

## Background / Prior Research

### pgembed / embedded Postgres extension model
- pgembed is viable as a Python embedded Postgres substrate: it packages Postgres binaries and lifecycle helpers around `initdb`, `pg_ctl`, `psql`, and includes pgvector in current distributions. Source: https://pypi.org/project/pgembed/0.1.5/
- The hard part is native extension bundling. C/Rust extensions must match Postgres major version, OS, arch, ABI, and preload requirements. A pgembed-native runtime should ship an extension bundle manifest: extension name, PG majors, OS/arch, required `shared_preload_libraries`, dependency extensions, init SQL, and license.
- Reference install pattern from Rust `pg-embed`: copy shared libraries to `lib/`, `.control`/`.sql` files to `share/postgresql/extension/`, start DB, then run `CREATE EXTENSION`. Source: https://docs.rs/crate/pg-embed/latest
- Startup must be extension-aware: copy native files before first start, write `postgresql.conf`/startup options for `shared_preload_libraries`, then run per-database `CREATE EXTENSION` migrations.

### Vector / BM25 / graph SQL primitives
- VectorChord provides pgvector-compatible ANN vector search with `vchordrq` indexes and `CREATE EXTENSION IF NOT EXISTS vchord CASCADE;`. Source: https://github.com/tensorchord/VectorChord
- Recommended dense retrieval model: store embeddings in normal Postgres rows with `doc_id`, `content`, JSONB metadata, timestamps, ACL fields; expose query knobs for `top_k`, `candidate_k`, distance metric, metadata filters, and optional rerank.
- BM25 candidates:
  - `pg_textsearch`: native BM25 extension requiring `shared_preload_libraries = 'pg_textsearch'`, `CREATE EXTENSION pg_textsearch`, and `USING bm25(...)` index. Sources: https://github.com/timescale/pg_textsearch and https://docs.tigerdata.com/use-timescale/latest/extensions/pg-textsearch/
  - ParadeDB `pg_search`: richer Elastic-like BM25/faceted/hybrid search in Postgres; more featureful but heavier and license/deployment must be checked. Source: https://docs.paradedb.com/welcome/introduction
  - `psql_bm25s`: agent-retrieval-oriented BM25-family extension visible in Pigsty packages, but public docs are thinner; treat as exploratory until SQL API/license/maturity are verified. Source: https://pigsty.cc/ext/e/psql_bm25s/
- Hybrid retrieval should be explicit SQL orchestration: run vector and lexical branches, over-fetch, fuse via RRF/weighted rank, then optionally rerank. Avoid hiding retrieval behavior behind magic; expose branch weights/candidate counts.
- Graph should start relational/recursive-CTE-first. Apache AGE is mature for property graph/openCypher but adds `agtype`, search_path, and Cypher-in-SQL complexity. pgGraph works over ordinary Postgres tables but appears very new/alpha. Sources: https://age.apache.org/overview/ and https://pgxn.org/dist/pggraph/0.1.5/
- Native Postgres JSONB, generated columns, GIN indexes, expression/partial indexes, recursive CTEs, and SQL window functions should be the baseline substrate.

### Agno / DSPy runtime framework implications
- Agno is the stronger primary runtime/control plane candidate: agents, teams/workflows, tool/function calling, Pydantic/JSON structured outputs, sessions/storage/memory, tracing/evals, human-in-loop, Postgres/PgVector alignment. Docs: https://docs.agno.com/sdk and https://docs.agno.com/agents
- DSPy is the stronger tunable analysis pipeline candidate: typed Signatures/Modules, ReAct/tools, evaluation metrics, optimizers such as GEPA/MIPRO/BootstrapFewShot, and repeatable prompt/program optimization. Docs: https://dspy.ai/ and https://github.com/stanfordnlp/dspy
- Recommended architecture is hybrid: Agno as runtime/control plane; DSPy as optional optimizer/evaluator for specific SQL-analysis modules such as intent parsing, schema relevance, retrieval planning, SQL generation, validation, plan diagnosis, and result summarization.

### ActiveGraph / audit runtime implications
- “ActiveGraph” has a name collision. The relevant candidate is ActiveGraph.ai / Yohei Nakajima’s Python event-sourced reactive graph runtime for agents, not the Ruby Neo4j OGM gem. Sources: https://activegraph.ai/ and https://docs.activegraph.ai/
- ActiveGraph.ai models an append-only event log, graph projection, behaviors, replay, fork, diff, lineage, and auditable agent runs. It is conceptually well aligned with agent provenance but very new (May 2026 ecosystem), so production readiness must be validated.
- pgGraph is better viewed as a graph traversal/search accelerator over Postgres tables, not an audit runtime. If production reliability matters now, use Postgres event log + relational provenance schema as system of record, optionally mirror/project into ActiveGraph or accelerate lineage traversal later with pgGraph.

### Initial design implication from external research
- pgembed 专用 infinisql should become a Postgres-native analysis runtime rather than a multi-database abstraction layer.
- Core should be: extension-aware pgembed bootstrap + SQL compiler/planner for hybrid retrieval + Postgres-native memory/provenance schema + safe SQL execution/analyzer tools + agent runtime orchestration.
- Optional advanced layers: Agno control plane, DSPy optimization modules, ActiveGraph audit projection, pgGraph/AGE graph query acceleration.

## Investigator Findings
<!-- Pair investigator appends structured analysis here with file:line refs, evidence, and conclusions. -->

### Phase 3 - Workspace Evidence Verification (2026-06-03)

#### 1. Verified pgembed runtime / extension substrate

**Evidence**
- Extension discovery is explicit but still code-hardcoded, not manifest-driven: `EXTENSION_PACKAGES`, `EXTENSION_SO_FILES`, `EXTENSION_ARTIFACT_STEMS`, `EXTENSION_NAMES`, and SQL create-name mapping cover `pgvector`, `pgvectorscale`, `pgtextsearch`, `pg_search`, `pg_duckdb`, `vectorchord`, `vectorchord_new`, `graph`/`pggraph`, `age`, `psql_bm25s`, and `timescaledb` (`pgembed/src/pgembed/__init__.py:18-76`, `pgembed/src/pgembed/__init__.py:163-182`).
- Bundled extension detection checks package wheels first, then native shared libraries under `pginstall/lib/postgresql`, and for VectorChord variants additionally verifies control/default-version SQL artifacts before declaring them creatable (`pgembed/src/pgembed/__init__.py:103-143`).
- `PostgresServer` lifecycle already supports pgdata init, shared-server reuse by pgdata, cleanup modes, Unix socket startup, and preload injection before server start (`pgembed/src/pgembed/postgres_server.py:39-82`, `pgembed/src/pgembed/postgres_server.py:116-151`, `pgembed/src/pgembed/postgres_server.py:229-279`, `pgembed/src/pgembed/postgres_server.py:321-360`).
- `create_extension()` maps user-facing keys to real SQL extension names and rejects unavailable extensions; AGE helpers exist for session setup and openCypher query execution (`pgembed/src/pgembed/postgres_server.py:374-429`).
- Tests verify core startup and SQLAlchemy usability (`pgembed/tests/test_pgembed.py:211-247`), no default AGE preload (`pgembed/tests/test_pgembed.py:35-43`), pgvector creation (`pgembed/tests/test_pgembed.py:369-372`), AGE catalog objects (`pgembed/tests/test_pgembed.py:375-391`), `psql_bm25s` access method/query API (`pgembed/tests/test_pgembed.py:394-417`), VectorChord preload/index APIs for both `vchord` and `vchord_new` (`pgembed/tests/test_pgembed.py:419-526`), TimescaleDB preload/hypertable creation (`pgembed/tests/test_pgembed.py:536-550`), and pgGraph status API via `CREATE EXTENSION graph` (`pgembed/tests/test_pgembed.py:553-558`).

**Conclusion**
- The pgembed substrate is sufficient for a pgsql-native runtime prototype: lifecycle, preload, extension discovery, `CREATE EXTENSION`, vector/BM25/graph smoke coverage all exist.
- The current registry should be **replaced with a manifest-backed extension bundle model** because runtime design needs declarative preload requirements, SQL extension names, dependency extensions, PG/OS/arch compatibility, install SQL, and license/provenance metadata. The existing constants are good seed data, not the final architecture.

#### 2. Verified current Infinity SQL / inspector / Byzer coupling

**Evidence**
- Agent tool registration exposes SQL as `execute_infinity_sql`, `register_table`, `list_tables`, `show_create`, and `load_infinity_sql_doc` (`infinisynapse/src/agent/tools/all-tool-specs.ts:132-136`, `infinisynapse/src/agent/tools/tool-dispatch.ts:177-183`).
- SQL safety is currently a shallow first-token blacklist over semicolon-split statements, flagging `DROP`, `DELETE`, `TRUNCATE`, `UPDATE`, `INSERT`, `ALTER`, `CREATE`, `EXEC`, `SHUTDOWN`, `XP_` (`infinisynapse/src/agent/infini-sql/sql-risk-check.ts:1-39`).
- The multi-database inspector fleet is coupled to Byzer/Spark/JDBC-style script execution: inspectors build `connect jdbc`, `load jdbc`, or `RUN command AS JDBC` snippets and submit `RunScriptParams` through `engineService.runScript()`; examples include ClickHouse (`infinisynapse/recovered/src.humanified/agent/infini-sql/inspectors/clickhouse-inspector.ts:274-286`, `:305-310`), Postgres (`infinisynapse/src/agent/infini-sql/inspectors/postgres-inspector.ts:290-293`, `:330-333`, `:364-367`), and other DB-specific inspectors in the same directory.
- The execution engine itself requires `InfinitySqlService.ensureRunning()` and obtains an Infinity SQL URL before submitting scripts (`infinisynapse/recovered/src.humanified/agent/infini-sql/engine-service.ts:518-523`, `:556-557`).
- SQL result handling already contains useful product behavior: permission-result filtering (`infinisynapse/src/agent/infini-sql/handle-execute-infinity-sql.ts:213-235`) and archived JSONL/stat summaries for large result sets (`infinisynapse/src/agent/infini-sql/handle-execute-infinity-sql.ts:242-286`).

**Conclusion**
- The existing SQL layer should **not** be migrated line-by-line. Its core is a multi-backend Byzer/JDBC/Spark routing layer, whereas pgembed needs a local Postgres-native executor.
- Retain concepts: tool UX (`execute/list/show/register`), result summarization, permission reminders, table/schema inspection, and user-visible SQL docs.
- Rewrite implementation: `execute_infinity_sql -> execute_pgsql`, `DatabaseHub`/inspector fleet -> `PgCatalogInspector`, `InfinitySqlService`/`EngineService.runScript` -> Python `PgsqlRuntime` over `pgembed.PostgresServer` + psycopg/sqlalchemy.

#### 3. Verified RAG, tool, sub-agent, and persistence surfaces

**Evidence**
- RAG product surface already includes a built-in memory server named `InfiniSynapse Memory`, described as persistent historical conversations and Q&A sessions (`infinisynapse/src/modules/ai-rag/rag-hub.ts:227-230`). RAG client lookup routes built-in and external/subscribed RAG servers through `RagHub.getRagByName()` and SDK clients (`infinisynapse/src/modules/ai-rag/rag-hub.ts:270-321`).
- Tool surface is broad and declarative: SQL, RAG, skills, external tools, files, browser, web search/fetch, charts, planning, delegation, etc. are centralized in `ALL_TOOL_SPECS` and dispatch table (`infinisynapse/src/agent/tools/all-tool-specs.ts:132-190`, `infinisynapse/src/agent/tools/tool-dispatch.ts:177-215`).
- Sub-agent concepts are explicit: registry includes `browser`, `web_research`, `data_analysis`, `general_purpose`, and `sql_curator` (`infinisynapse/src/agent/sub-agents/agent-type-registry.ts:24-31`). Delegation creates isolated sub-agents, registers them, assigns allowed tools, starts tasks, records status/duration/output/error, and unregisters them (`infinisynapse/src/agent/core/execute-delegate-sub-agents.ts:625-644`, `:645-678`, `:693-741`).
- Persistence concepts already exist but are TypeORM/app-server shaped: `ai_database`, `ai_setting`, `ai_skill`, `ai_tool`, `ai_template`, `ai_task_info`, `ai_task_snapshot`, `ai_task_ui_message`, `ai_task_api_conversation`, and extraction metadata (`infinisynapse/src/database/entities/ai-database.entity.full.ts:160-162`, `ai-skill.entity.ts:176-210`, `ai-tool.entity.ts:190-208`, `ai-task-info.entity.full.ts:132-146`, `ai-task-snapshot.entity.ts:121-161`, `ai-task-ui-message.entity.ts:46-64`, `ai-task-api-conversation.entity.ts:46-64`, `ai-extract-sql.entity.ts:101-139`).

**Conclusion**
- Product concepts to keep: task/session history, snapshots, UI/API conversation streams, built-in memory, user-defined skills/tools/templates/settings, SQL-curator/data-analysis sub-agent roles, browser/web tools as optional sidecars.
- Storage/runtime implementation should be **new Postgres schema + event log**, not TypeORM/Nest module migration. Existing entity names and columns are useful domain vocabulary for new normalized/JSONB tables and provenance/event projections.

### Migration Matrix

| Current responsibility / module | Evidence | Decision | Target implementation | Rationale |
|---|---:|---|---|---|
| pgembed lifecycle (`PostgresServer`, `get_server`) | `pgembed/src/pgembed/postgres_server.py:39-82`, `:455-493` | **Keep + extend** | `PgembedBootstrap` wrapper | Already starts/stops embedded PG; add runtime config, health, per-workspace pgdata policy. |
| pgembed extension constants | `pgembed/src/pgembed/__init__.py:18-76`, `:163-182` | **Replace** | `extensions.toml/json` manifest + loader | Constants prove capability but lack preload/dependency/license/compat metadata required for product runtime. |
| Shared preload handling | `postgres_server.py:116-151`; tests `test_pgembed.py:419-526`, `:536-550` | **Keep + harden** | Manifest-derived preload set before first start | Correct primitive exists; add conflict detection and restart-required diagnostics. |
| `create_extension()` | `postgres_server.py:374-407` | **Keep + extend** | idempotent migration runner | Map key->SQL name exists; add dependency ordering, schema placement, verification queries. |
| Vector retrieval | VectorChord tests `test_pgembed.py:419-526` | **New implementation** | `documents/chunks/embeddings` tables + VectorChord/pgvector indexes | Existing infinisynapse RAG is SDK/client-mediated; pgsql runtime should own SQL retrieval. |
| Lexical/BM25 retrieval | `psql_bm25s` tests `test_pgembed.py:394-417` | **New implementation** | BM25 branch with `psql_bm25s`; optional `pg_textsearch`/`pg_search` adapters | Extension works; planner must expose branch weights/candidate counts and fallback APIs. |
| Graph/lineage traversal | AGE/pgGraph tests `test_pgembed.py:375-391`, `:553-558` | **New / optional** | relational graph tables first; optional AGE/pgGraph projections | Keep system of record in ordinary PG; use graph extensions after maturity/perf validation. |
| `execute_infinity_sql` tool | `all-tool-specs.ts:132-136`, `tool-dispatch.ts:177-183` | **Migrate concept, rewrite implementation** | `execute_pgsql` with structured result/artifacts | Tool UX is valuable; backend should be psycopg/Postgres, not Byzer. |
| SQL risk checker | `sql-risk-check.ts:1-39` | **Rewrite** | AST/parser-based policy + transaction/read-only role/timeouts | Current blacklist is insufficient for Postgres safety and extension functions. |
| `DatabaseHub` + multi-DB inspectors | Inspector JDBC evidence above | **Remove/replace** | `PgCatalogInspector` | Multi-database routing is out of scope for pgembed-native runtime; replace with `pg_catalog`, `information_schema`, extension catalog queries. |
| `InfinitySqlService` / `EngineService.runScript` / Byzer modules | `engine-service.ts:518-523`, `:556-557` | **Remove for pgembed runtime** | `PgsqlRuntime.execute()` | Spark/Byzer/JDBC engine is the root impedance mismatch. |
| Result filtering and archived summaries | `handle-execute-infinity-sql.ts:213-286` | **Migrate** | policy-filtered result envelopes + artifact tables/files | Product behavior remains useful for LLM context management and access control. |
| `list_tables`, `show_create`, SQL docs | `all-tool-specs.ts:132-136`; Postgres inspector refs | **Migrate concept, rewrite** | catalog inspector + generated PG docs/examples | Keep tools; derive schema from Postgres catalogs and extension introspection. |
| Built-in RAG memory | `rag-hub.ts:227-230` | **Migrate concept, rewrite** | Postgres-native memory tables + hybrid retrieval planner | Product concept maps directly to pgembed local knowledge store. |
| External/subscribed RAG SDK routing | `rag-hub.ts:270-321` | **Replace/optional bridge** | import/sync connectors; default local runtime | Useful compatibility, but pgembed runtime should not depend on remote SDK. |
| Tool registry/dispatch model | `all-tool-specs.ts:132-190`, `tool-dispatch.ts:177-215` | **Keep concept, rewrite in Python** | typed tool API for Agno/DSPy/plain runtime | Central declarative tools are good; TS handlers are app-specific. |
| Sub-agent roles/delegation | `agent-type-registry.ts:24-31`; `execute-delegate-sub-agents.ts:625-741` | **Keep concept, rewrite** | Agno Teams/Workflows or custom asyncio workers; DSPy modules for analyzers | Roles and status tracking are useful; isolated workspace/filesystem logic is not core to pgsql runtime. |
| Task/message/snapshot entities | entity refs above | **Migrate schema concepts, rewrite DDL** | `agent_runs`, `agent_events`, `messages`, `snapshots`, `artifacts` | Use Postgres JSONB + event log + provenance graph instead of TypeORM entities. |
| Skills/tools/templates/settings entities | `ai-skill.entity.ts:176-210`, `ai-tool.entity.ts:190-208`, `ai-template.entity.ts:135-136`, `ai-setting.entity.ts:118-119` | **Migrate concept, rewrite** | `tool_manifest`, `skill_manifest`, `prompt_template`, `runtime_setting` tables | Retain user-extensibility but rebase on manifest/versioning/provenance. |
| Browser/web/file/chart tools | `tool-dispatch.ts:184-214` | **Keep optional sidecars** | external tool adapters with audit events | Product useful but not part of core SQL/RAG runtime. |
| NestJS modules (`ai-rag`, `infinity-sql`, `byzer`, `engine`, TypeORM controllers/services) | module tree + evidence above | **Not necessary in pgembed-native package** | Python package/service boundary; optional API wrapper | Existing modules are application shell, not portable pgsql runtime. |

### Target pgsql Architecture

1. **Bootstrap / extension manifest**
   - New package layer: `infinisql_pgsql` or `pgembed_infinisql`.
   - Manifest fields: `key`, `sql_extension_name`, `artifact_stem`, `packages`, `library_files`, `control_file`, `default_version`, `requires_preload`, `preload_name`, `dependencies`, `init_sql`, `verification_sql`, `pg_major`, `platform/arch`, `license`, `source_url`, `build_provenance`.
   - Bootstrap flow: resolve manifest -> validate files -> initialize pgdata -> inject preload list before start -> start `PostgresServer` -> run ordered `CREATE EXTENSION` + init migrations -> store installed-extension state in `runtime_extension_state`.

2. **Execution / safety**
   - Replace `containsRiskCommands` with parse/policy enforcement: read-only default role, explicit write capability grants, statement timeout, lock timeout, row/output limits, transaction wrapper with rollback for analysis, allowlisted extension functions, blocked `COPY PROGRAM`/FDW/server-file operations, and SQL comments/provenance tagging.
   - API: `execute_pgsql(sql, mode={read_only|write|admin}, params, row_limit, artifact_policy) -> ResultEnvelope(rows, columns, notices, plan, artifacts, provenance_event_id)`.

3. **Postgres catalog inspector**
   - `PgCatalogInspector` should use `pg_catalog`/`information_schema` plus extension catalogs, not JDBC/Byzer.
   - Methods: schemas, tables/views/materialized views, columns/types, constraints, indexes, functions/operators, extension list/version, vector index metadata, BM25 index metadata, graph catalogs, table sample/stats, `EXPLAIN` plan summaries, safe `pg_get_*def()` DDL reconstruction.

4. **Hybrid retrieval planner**
   - Tables: `documents`, `chunks`, `embeddings`, `lexical_index_state`, `retrieval_runs`, `retrieval_candidates`, `rerank_results`.
   - Branches: vector (`pgvector`/VectorChord), lexical (`psql_bm25s`, optionally `pg_textsearch`/`pg_search`), metadata JSONB filters, graph-neighborhood expansion, recency/ACL filters.
   - Planner: explicit over-fetch, RRF/weighted fusion, optional rerank, traceable branch scores. Store each retrieval run for audit and tuning.

5. **Memory / provenance / event graph**
   - System of record: append-only `agent_events(event_id, run_id, parent_event_id, actor, event_type, payload jsonb, created_at)`, plus normalized projections (`agent_runs`, `messages`, `tool_calls`, `sql_executions`, `artifacts`, `facts`, `entity_mentions`, `edges`).
   - Graph strategy: start with relational edge table + recursive CTE; optionally project to AGE/pgGraph or ActiveGraph when traversal/audit features justify extra complexity.
   - Provenance: every SQL execution, retrieval branch, tool call, sub-agent handoff, and memory write links to input messages/artifacts and output rows/artifacts.

6. **Agent runtime options**
   - **Primary recommendation:** Agno as control plane for agents, teams, tools, storage/memory integration, tracing, and human-in-loop.
   - **DSPy:** use for tunable modules: schema relevance, retrieval planning, SQL generation/repair, plan diagnosis, result summarization; log inputs/metrics to Postgres.
   - **ActiveGraph:** optional audit/projection layer only after API stability validation; do not make it the primary source of truth before production maturity is proven.
   - **Fallback:** plain Python orchestrator with typed tool registry if framework lock-in is undesirable; preserve tool schemas so Agno/DSPy wrappers can be added later.

7. **Tool API**
   - Core tools: `execute_pgsql`, `inspect_catalog`, `show_create`, `retrieve_memory`, `write_memory`, `search_events`, `explain_plan`, `create_extension`, `extension_status`.
   - Optional tools: browser/web/file/chart/external skill adapters. Every tool emits `agent_events` and has a capability policy.

### Modules no longer necessary vs retained concepts

**No longer necessary in pgembed-native runtime**
- Byzer/Spark/JDBC execution path: `src/modules/byzer`, `src/modules/engine`, `src/modules/infinity-sql`, `InfinitySqlService`, `EngineService.runScript`, and multi-database JDBC inspectors.
- TypeORM/NestJS controllers/services as runtime core. They can remain in the product shell, but the pgembed-native runtime should be a Python/Postgres package or service boundary.
- Remote RAG SDK as default memory substrate; keep only compatibility/import bridges.

**New implementations required**
- Manifest-backed extension bootstrap and migration runner.
- `PgsqlRuntime` execution engine and safety policy.
- `PgCatalogInspector`.
- Postgres-native memory/retrieval schema and hybrid planner.
- Event/provenance graph schema and projections.
- Python tool registry and agent/sub-agent orchestration layer.

**Product concepts to retain**
- SQL analyst tool UX: execute/list/show/register/doc loading.
- Built-in persistent memory and Q&A/history retrieval.
- Skills/tools/templates/settings as user-extensible manifests.
- Task/session/message/snapshot history and resumability.
- Sub-agent roles: `sql_curator`, `data_analysis`, `general_purpose`, `web_research`, `browser`.
- Permission reminders, result filtering, archived artifacts/stat summaries.

### Phase Plan

1. **P0 foundation:** add extension manifest loader around existing pgembed constants; implement bootstrap verifier and extension-state table; keep tests for pgvector, VectorChord, BM25, AGE/pgGraph, TimescaleDB.
2. **P1 executor/inspector:** implement `PgsqlRuntime.execute()` with safety policy and `PgCatalogInspector`; port `execute/list/show` concepts to Python tools.
3. **P2 memory/RAG:** create document/chunk/event schemas; implement vector + BM25 retrieval branches, fusion, and retrieval-run provenance.
4. **P3 agent runtime:** wrap core tools in Agno or plain typed registry; add DSPy modules for schema selection/SQL generation/retrieval planning; log all tool calls/events.
5. **P4 migration bridge:** import selected infinisynapse tasks/messages/skills/tools/templates into new tables; provide compatibility aliases for `execute_infinity_sql` -> `execute_pgsql` if needed.
6. **P5 advanced graph/audit:** evaluate ActiveGraph projection and AGE/pgGraph acceleration against real lineage workloads before promoting beyond optional status.

### Final Investigator Conclusion

The migration hypothesis is confirmed with one qualification: pgembed already has enough extension/runtime capability for a pgsql-native agent runtime, but the target should be a **new Postgres-native implementation** that retains infinisynapse product semantics rather than a direct port of its NestJS/Byzer/Infinity SQL implementation. The highest-risk work is not SQL execution itself; it is safe execution policy, manifest-driven native extension lifecycle, traceable hybrid retrieval, and durable provenance/memory design.


## Investigation Log

### Phase 1 - Initial Assessment
**Hypothesis:** pgembed 专用 infinisql 应从“多数据库路由 + TS/Nest backend 模块”转为“Postgres embedded-first + extension-native knowledge runtime + agent/program graph”。
**Findings:** 已创建报告并完成外部资料搜集。外部资料确认：pgembed 的关键难点是 native extension bundle + preload + per-database `CREATE EXTENSION`；Agno/DSPy/ActiveGraph 分别适合作为 runtime、可优化分析模块、审计投影候选。
**Evidence:** Report path: `/Users/wxl/Projects/pgembed/docs/investigations/pgembed-infinisql-agent-runtime-design-2026-06-03.md`
**Conclusion:** Confirmed direction; proceed to workspace-driven migration analysis.

### Phase 2 - Context Builder Initial Assessment
**Hypothesis:** 现有 Infinity SQL/Byzer/NestJS 多数据库执行层不应逐行迁移；应保留产品概念与 agent/tool 边界，重写底层为 pgembed/Postgres-native runtime。
**Findings:** context_builder selected 44 representative files across pgembed bootstrap/tests/examples/docs and infinisynapse SQL/RAG/tool/sub-agent/entity modules. It identified the current path as `execute_infinity_sql -> DatabaseHub -> EngineService.runScript -> Java/Spark/Byzer InfinitySqlService -> JDBC wrappers`, and target path as `execute_pgsql -> PgsqlRuntime -> pgembed.PostgresServer/psycopg -> PostgreSQL native SQL + extensions`.
**Evidence:** Selected files include `infinisynapse/src/agent/infini-sql/*`, `infinisynapse/src/agent/tools/*`, `infinisynapse/src/modules/ai-rag/*`, selected entities, and `pgembed/src/pgembed/*`, `pgembed/tests/test_pgembed.py`, examples, README, plugin audit plan.
**Conclusion:** Needs pair investigator to verify module migration details and produce final migration matrix/architecture.

### Phase 4 - Oracle Synthesis
**Hypothesis:** pair investigator 的迁移结论需要进一步综合，确认根因和最终建议。
**Findings:** Oracle confirmed the core mismatch: current infinisql treats SQL as scripts submitted to an external multi-backend Byzer/Spark/JDBC engine, while pgembed-native pgsql must treat PostgreSQL itself as the local runtime, storage layer, retrieval engine, provenance store, and extension host.
**Evidence:** Oracle synthesis over refreshed selection including pgembed extension/runtime files, tests, infinisynapse SQL/RAG/tool/sub-agent files, and pair findings.
**Conclusion:** Confirmed: build a new pgembed-native runtime and keep compatibility only at product/tool boundaries.

## Root Cause
The existing infinisynapse infinisql stack is architected around a remote/multi-database Byzer/Spark/JDBC execution model, not around PostgreSQL as the runtime. `InfinitySqlService` starts and monitors a Java/Spark engine; `EngineService.runScript()` sends scripts over HTTP; `DatabaseHub` builds database-specific JDBC wrappers; and `handle-execute-infinity-sql` manages Infinity SQL-specific query modes.

pgembed changes the substrate completely. PostgreSQL is no longer an external database behind a JDBC wrapper; it is the embedded runtime itself. Its lifecycle, extension preload, extension creation, catalog inspection, vector/BM25/graph indexing, memory storage, and provenance can all be handled locally.

Therefore, the migration is blocked by a semantic and architectural mismatch: current infinisql is a multi-database script router; target `pgsql` is an embedded Postgres-native analysis and agent runtime.

## Recommendations
1. **Create a new pgembed-native `pgsql` runtime.** Do not port `InfinitySqlService`, `EngineService.runScript`, or Byzer/JDBC inspectors directly.
2. **Replace hardcoded extension registry with a manifest.** Seed it from `pgembed/src/pgembed/__init__.py`, but add preload requirements, SQL create names, dependencies, control/SQL artifact checks, platform support, license, and verification SQL.
3. **Build `PgsqlRuntime.execute()`.** Use psycopg/SQLAlchemy, transaction wrappers, timeouts, row limits, structured result envelopes, artifact capture, and provenance events.
4. **Rewrite SQL safety.** Replace `sql-risk-check.ts` with AST/parser-based classification, read-only default roles, allowlisted temp DDL, blocked dangerous operations, and privileged admin modes.
5. **Replace `DatabaseHub` with `PgCatalogInspector`.** Reuse catalog-query concepts from `PostgresInspector`, but execute directly against embedded Postgres and make it extension-aware.
6. **Replace RAG SDK core with Postgres-native hybrid retrieval.** Implement document/chunk/embedding tables, pgvector/VectorChord branch, BM25 branch via `psql_bm25s` or `pg_textsearch`, JSONB filters, RRF fusion, optional rerank, and retrieval-run audit logs.
7. **Use Postgres event log as provenance source of truth.** Migrate task snapshots, UI/API messages, tool calls, SQL executions, RAG results, artifacts, skills, and templates into normalized/JSONB Postgres schemas.
8. **Keep sub-agent and tool concepts, rewrite runtime.** Preserve `sql_curator`, `data_analysis`, delegation status, tool registry, and structured submit-output patterns; implement them through Agno/plain Python tools plus optional DSPy modules.
9. **Keep external/browser/file/chart tools as sidecars.** They are useful product capabilities but should not define the core pgsql runtime.
10. **Keep compatibility aliases only temporarily.** `execute_infinity_sql` can map to `execute_pgsql` during migration, but internal behavior must be pgsql-native rather than preserving Byzer semantics.

## Preventive Measures
- Make extension state declarative and testable: every extension needs artifact status, preload requirement, `CREATE EXTENSION` name, dependency order, compatibility metadata, and smoke test.
- Never infer SQL safety from string prefixes; use parser-based policy, roles, transaction modes, statement timeouts, lock timeouts, and explicit capability grants.
- Separate product shell from runtime substrate: NestJS/Electron/API modules may call the pgsql runtime, but should not own SQL execution semantics.
- Keep Postgres as system of record: agent events, tool calls, retrieval traces, SQL executions, artifacts, and memory writes should be append-only/auditable in Postgres before graph/framework projections.
- Avoid framework lock-in: expose typed tool interfaces first; wrap them with Agno, DSPy, or other frameworks second.
- Treat graph engines as projections/accelerators: AGE, pgGraph, and ActiveGraph should not replace the relational event log initially.
- Preserve compatibility only at boundaries: compatibility names like `execute_infinity_sql` may exist temporarily, but core runtime behavior should be pgsql-native.
- Test extension combinations by runtime profile, especially `shared_preload_libraries` for VectorChord and TimescaleDB before server start, using isolated pgdata profiles where required.
