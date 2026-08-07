# Investigation: pg_duckdb ↔ TimescaleDB `time_bucket` collision

## Summary
pg_duckdb and TimescaleDB both install signature-identical `time_bucket(interval, …)` functions in the `public` schema, so they collide at `CREATE EXTENSION` time. The collision is **order-dependent and already half-mitigated upstream**: pg_duckdb v1.1.0 wraps its `public.time_bucket` creation in a `DO $$ … EXCEPTION WHEN duplicate_function` guard and unconditionally provides a `duckdb.time_bucket` fallback — so creating **TimescaleDB first, then pg_duckdb** succeeds cleanly with no rebuild. The remaining hard risk is a C-level **planner segfault** (upstream pg_duckdb #845/#963) when both engines run in one Postgres instance; it did **not** reproduce in our basic probes and is best treated as a documented limitation (use separate instances for heavy mixed workloads).

## Symptoms
- pgembed PG17 build, server preloaded `vchord,timescaledb,pg_duckdb,pg_cron,pg_net`.
- `CREATE EXTENSION pg_duckdb;` then `CREATE EXTENSION timescaledb;` →
  `ERROR: function "time_bucket" already exists with same argument types`.
- Each alone (or in separate DBs) installs and works (timescaledb v2.27.1 incl. `create_hypertable`; pg_duckdb v1.1.0).
- Build is fine — this is a SQL-object name collision at `CREATE EXTENSION` time.

## Background / Prior Research
(Phase 1.5 — explore agents on github.com/duckdb/pg_duckdb @v1.1.0 and timescale/timescaledb @v2.27.1)

**Definitions & schema**
- TimescaleDB `time_bucket` = SQL wrappers (`@extschema@.time_bucket(...) AS 'MODULE_PATHNAME','ts_*_bucket'`, LANGUAGE C) in `sql/time_bucket.sql`. Core, heavily used (hypertables, continuous aggregates). NOT a shim.
- pg_duckdb `time_bucket` = SQL wrappers (`@extschema@.time_bucket(...) AS 'MODULE_PATHNAME','duckdb_only_function'`) in `sql/pg_duckdb--1.0.0.sql`. A DuckDB-compatibility shim; its SQL comment states it *deliberately mirrors Timescale's signatures "for consistency"*.
- Both use `@extschema@`; both `relocatable = false`. pg_duckdb control additionally hardcodes `schema = public`. TimescaleDB control has no hardcoded schema → installable into a custom schema at creation (`CREATE EXTENSION timescaledb SCHEMA x;`), just non-relocatable afterward (it creates 7 internal `_timescaledb_*` schemas).

**Upstream awareness / fixes**
- pg_duckdb #693, #934 (closed): the collision is known. PR #747 (merged May 2025, present in v1.1.0): the 10 standard overloads are inside `DO $$ … EXCEPTION WHEN duplicate_function THEN RAISE WARNING '… use duckdb.time_bucket instead …'`; pg_duckdb ALSO always creates 16 `duckdb.time_bucket(...)` overloads in a dedicated `duckdb` schema + 6 `unresolved_type` variants in public.
- pg_duckdb PR #1023 (NOT in v1.1.0): removes hardcoded `schema = public` → would let pg_duckdb install into a custom schema. Not yet in our pinned version.
- pg_duckdb #845 (OPEN), #963: **planner segfault** — pg_duckdb calls `standard_planner()` directly, bypassing TimescaleDB's planner hook → null deref in `BaserelInfo_insert`. Triggered by specific mixed-query patterns, not universal.
- No flag/GUC on either side to skip or relocate `time_bucket` post-install.

## Investigator Findings
(verified directly in the built `src/pgembed/pginstall/...` artifacts + two runtime tests)

1. **Collision signatures are identical.** pg_duckdb--1.0.0.sql lines 1439-1487 and timescaledb--2.27.1.sql lines 1410-1437 both define `@extschema@.time_bucket(interval, {date,timestamp,timestamptz})` plus origin/offset/timezone variants — ~10 overlapping signatures.
2. **pg_duckdb v1.1.0 HAS the guard.** `pg_duckdb--1.0.0.sql` lines 1436-~1505: `DO $$ BEGIN CREATE FUNCTION @extschema@.time_bucket(...)` ×10 `… EXCEPTION WHEN duplicate_function THEN RAISE WARNING '… use duckdb.time_bucket instead …'`. Confirmed verbatim.
3. **Relocatability confirmed:** `pg_duckdb.control` → `relocatable=false`, `schema=public`; `timescaledb.control` → `relocatable=false`, no `schema=` line.
4. **RUNTIME TEST A (order):** timescaledb-first then pg_duckdb → both install; pg_duckdb emits `WARNING: time_bucket function already exists, use duckdb.time_bucket instead…`; `public.time_bucket` owned by timescaledb (`ts_int16_bucket`/`ts_int64_bucket`); `duckdb.time_bucket` exists. pg_duckdb-first then timescaledb → ERROR (verified, matches symptom).
5. **RUNTIME TEST B (segfault probe):** both preloaded + installed (correct order); ran timescaledb hypertable + `time_bucket` aggregation + DuckDB-engine attempts in one session. **Server stayed up; no segfault** in log. (DuckDB `read_csv` only failed at function resolution — pg_duckdb intercepts `read_csv`/`read_parquet` at parser level, not as ordinary functions.) So the crash is edge-case, not triggered by basic coexistence.

## Root Cause
Two independent issues, only the first is what the user observed:

1. **Name collision (the reported error).** Both extensions unconditionally create `time_bucket(interval, …)` in `public` (pg_duckdb forces `public` via its control; timescaledb defaults to public). pg_duckdb's `DO/EXCEPTION` guard makes this **order-dependent**: it only skips its own `public.time_bucket` when a duplicate *already exists* — i.e. when **TimescaleDB is created first**. Created in the other order, pg_duckdb populates `public.time_bucket`, and TimescaleDB (which has no guard) then hard-fails on the duplicate. This is by-design on pg_duckdb's side ("Timescale also has a function with the same name… create in duckdb schema as backup").
2. **Planner segfault (latent, not reproduced here).** Upstream pg_duckdb #845/#963: when both are loaded in one instance, pg_duckdb's planner invocation bypasses TimescaleDB's hook → null deref. Schema isolation / correct ordering do **not** address this; it is C-level. Appears to require specific query patterns; did not reproduce in our probes on pg_duckdb 1.1.0 + timescaledb 2.27.1 + PG17.

## Recommendations

**Primary (name collision, no rebuild) — Option A: order-aware activation.**
- Always create **TimescaleDB before pg_duckdb**. Leverages pg_duckdb's *existing* upstream guard; zero patch maintenance.
- In `src/pgembed/postgres_server.py` `create_extension` (≈ lines 369-395), add a small conflict-aware ordering layer: when `create_extension("pg_duckdb")` is requested and `has_extension("timescaledb")` is true but the extension isn't yet installed in the DB, create TimescaleDB first; or expose `server.create_extensions([...])` that topologically orders a known-conflicts map (`{"pg_duckdb": ["timescaledb"]}`). DuckDB bucketing stays available as `duckdb.time_bucket`.
- Document for raw-`psql` users: install timescaledb first.

**Stronger alternative (order-independent, build patch) — Option C: neutralize pg_duckdb's `public.time_bucket`.**
- In `pgbuild/Makefile` pg_duckdb recipe, after clone, `sed`-patch `sql/pg_duckdb--1.0.0.sql` to remove the `DO $$ … public.time_bucket … EXCEPTION` block (lines ~1436-1505) so pg_duckdb **never** emits `public.time_bucket`. It still ships `duckdb.time_bucket` (16 overloads) + the 6 non-conflicting `unresolved_type` variants. Same sed-patching pattern already used for `psql_bm25s`.
- Blast radius: pg_duckdb internals do not call `public.time_bucket` (it's user-facing only, dispatched via `duckdb_only_function`); only user SQL that calls bare `time_bucket(...)` for DuckDB bucketing must switch to `duckdb.time_bucket(...)`. Acceptable for a bundled/embedded product.
- Trade-off: a sed patch against upstream SQL is fragile if pg_duckdb restructures the file (same risk class as the psql_bm25s patch). Prefer Option A unless order-fragility is unacceptable.

**Do NOT pursue (rejected)**
- Patching the C segfault (Option E): a fragile fork of pg_duckdb's planner integration; unjustifiable maintenance.
- Making TimescaleDB live in a custom schema (Option B) as the *primary*: forces every caller to qualify timescaledb functions / manage `search_path`, and does not fix the segfault.
- pg_duckdb.control `relocatable=true` patch (Option D): requires proving pg_duckdb's event triggers / AM handler are schema-safe; higher risk than C for the same benefit.

**Residual segfault — documented limitation (Option F, scoped)**
- State in README: pg_duckdb + TimescaleDB in the **same Postgres instance** can, under specific mixed queries, trigger a planner crash (upstream #845/#963). It did not reproduce in our smoke tests, so light coexistence is usable; for production workloads that heavily mix both engines, run them in **separate pgembed instances** (separate `pgdata` via `pgembed.get_server(...)`).

## Preventive Measures
- Keep a "known extension conflicts" table in pgembed (seed: `pg_duckdb ↔ timescaledb` via `time_bucket`) and have `create_extension(s)` consult it for ordering.
- Smoke-test the pair together in `tests/test_pgembed.py` (both orders; assert timescaledb-first succeeds, and that `duckdb.time_bucket` is reachable).
- When bumping `pg_duckdb`/`timescaledb` versions, re-check whether PR #1023 (pg_duckdb relocatable) or a segfault fix has landed upstream, then revisit Option C/D.

## Investigation Log

### Phase 1.5 — External (explore agents)
**Hypothesis:** upstream design + known-conflict status for each extension's `time_bucket`.
**Findings:** pg_duckdb has guard (PR #747) + duckdb-schema fallback + hardcoded `schema=public`; timescaledb is core, non-relocatable but installable in custom schema; known segfault #845/#963.
**Evidence:** pg_duckdb #693/#934/#845/#963, PR #747/#1023; timescaledb `sql/time_bucket.sql`, `timescaledb.control.in`, `sql/pre_install/schemas.sql`; pg_duckdb `pg_duckdb--1.0.0.sql`, `pg_duckdb.control`.

### Phase 3 — In-workspace verification (direct)
**Hypothesis:** built v1.1.0 has the guard; collision is order-dependent; segfault reproducible on basic use.
**Findings:** guard present (lines 1436-~1505); order-dependence confirmed (TEST A); segfault NOT reproduced (TEST B).
**Evidence:** `pg_duckdb--1.0.0.sql:1428-1510`, `timescaledb--2.27.1.sql:1405-1445`, both `.control` files; two live pgembed servers (PGDATA /tmp/pgcol.*, /tmp/pgsf.*).

### Phase 4 — Oracle synthesis
Oracle backend unavailable (503 model_not_found); synthesis performed by the orchestrating agent from the verified evidence above.

### Phase 5 — PG18.4 planner-hook patch and residual parallel-scan hang (2026-08-07)

**Context:** the PostgreSQL 18.4 upgrade carries `pgbuild/patches/pg_duckdb-v1.1.1-planner-hook-chain.patch`, pinned by SHA-256 in `pgbuild/Makefile`. It replaces pg_duckdb's direct `standard_planner()` call in `PostgresTableReader::InitUnsafe` with a `PlanPostgresQuery()` helper that enters the full `planner_hook` chain behind a nest-level guard, so TimescaleDB can initialize per-query state. This addresses the #845/#963 null-deref crash: the crash probe no longer segfaults.

**Residual defect (open):** the patch converts the crash into a **hang**, not a clean pass. With `timescaledb` and `pg_duckdb` both preloaded, after a hypertable + `time_bucket` query has run, this sequence intermittently hangs:

```sql
CREATE TABLE public.i963 (id integer PRIMARY KEY, value double precision);
INSERT INTO public.i963 VALUES (1, 1.0);
SET duckdb.force_execution = true;
SELECT count(*) FROM public.i963;   -- hangs here
```

**Reproduction rate:** 2/8 isolated runs (~25%). It also fails `tests/test_pgembed.py::test_pg_duckdb_timescaledb_planner_probe_survives` intermittently in full-suite runs (1 failure in 3 consecutive full runs), surfacing as `subprocess.TimeoutExpired` after the test's 30 s psql timeout — not as a crash-marker assertion.

**Evidence:** `pg_stat_activity` from a second connection during a live hang shows the backend blocked in

```
pid   | state  | wait_event_type | wait_event     | query
55984 | active | IPC             | ParallelFinish | SELECT count(*) FROM public.i963;
```

The backend is waiting for a parallel worker that never finishes. `SET max_parallel_workers_per_gather = 0` does **not** prevent it (still 1/6 hangs), because `PostgresTableReader::InitRunWithParallelScan` launches workers itself via `ExecInitParallelPlan`/`LaunchParallelWorkers`, bypassing that GUC. The plausible mechanism is that routing the scan plan through the TimescaleDB hook can now yield a plan shape whose worker teardown pg_duckdb's self-managed parallel reader does not complete; this last step is inferred from the code path, not directly observed in a worker stack trace.

**Status:** the patch is a net improvement (crash → intermittent hang) and is retained, but it does **not** fully close #845/#963. Treat "pg_duckdb + TimescaleDB in one instance under `duckdb.force_execution`" as still unsafe for production; the existing guidance to use separate pgembed instances for heavy mixed workloads stands. Attempting a worker-teardown fix, or gating pg_duckdb's self-managed parallel scan when TimescaleDB is loaded, is the natural follow-up.
