# NOOA-on-DBOS with TigerFS on pgembed

This guide describes how to coordinate durable NOOA Agent execution with a versioned TigerFS workspace when DBOS, NOOA projections, and TigerFS all use one pgembed PostgreSQL cluster.

The design is based on:

- TigerFS **v0.7.0** file-first history, operation log, savepoints, and undo.
- pgembed's bundled PostgreSQL **17.10** and TimescaleDB **2.27.1** as tested on August 7, 2026.
- NOOA's current DBOS durable profile, where recovery reads committed `TurnCommit.state` and the durable effect registry is deny-by-default.

> **Status:** This is an integration design and operating guide. pgembed can run the required PostgreSQL, TimescaleDB, and TigerFS pieces, but NOOA does not yet ship a TigerFS `WorkspaceStore`, a transactional effect adapter, or a `workspace_checkpoint` field in `DurableSessionState`.

## 1. The design in plain language

Think of the system as three cooperating parts:

- **DBOS is the official work diary.** It records which Agent turn was committed and which recoverable NOOA state belongs to that turn.
- **TigerFS is the versioned workbench.** The Agent reads and writes source code and intermediate files there. TigerFS records file versions and can roll the whole workspace back to a named savepoint.
- **pgembed is the building that contains both.** It runs one PostgreSQL server, but each subsystem has its own database and responsibilities.

Putting them on the same pgembed server is reasonable. It simplifies packaging, lifecycle management, backup, and local deployment. It does **not** make a DBOS commit and a TigerFS file write one atomic transaction. PostgreSQL transactions do not span separate databases, and TigerFS normally commits filesystem operations one at a time.

The coordination rule is therefore:

> **DBOS owns the NOOA-authoritative pointer; TigerFS owns the files and their history. TigerFS commits each filesystem operation and savepoint immediately, but NOOA accepts a workspace state as authoritative only after DBOS commits its checkpoint reference.**

Concurrent filesystem readers can see the TigerFS changes before DBOS accepts them. If the process crashes in that interval, recovery uses the previous DBOS pointer and asks TigerFS to compensate the NOOA-provisional operation tail.

## 2. What TigerFS versioning provides

A file-first workspace created with history enabled exposes:

```text
workflow_main/
├── source files and intermediate files
├── .history/       past content versions for each file
├── .log/           create, edit, rename, delete, and undo operations
├── .savepoint/     named bookmarks in the operation timeline
└── .undo/          preview and atomic rollback operations
```

Create such a workspace with:

```bash
printf 'markdown,history' > /mnt/nooa/.build/workflow_main
```

TigerFS v0.7.0 provides three related but distinct concepts:

1. **File versions.** Every create, edit, rename, and delete in a history-enabled file-first workspace produces history and log data. Historical content is addressed through `.history/` using UUIDv7-based version IDs.
2. **Workspace savepoints.** A savepoint is a named bookmark in the operation log, not a copied directory tree.
3. **Atomic undo.** Undo-to-savepoint restores every affected file in one PostgreSQL transaction. If PostgreSQL crashes during that undo transaction, PostgreSQL rolls it back.

Create and inspect a savepoint:

```bash
printf '{"description":"DBOS commit 42"}' \
  > /mnt/nooa/workflow_main/.savepoint/dbos-wf-main-commit-000042.json

cat /mnt/nooa/workflow_main/.savepoint/dbos-wf-main-commit-000042/savepoint_id
```

Preview and apply rollback:

```bash
cat /mnt/nooa/workflow_main/.undo/to-savepoint/dbos-wf-main-commit-000042/.info/summary

touch /mnt/nooa/workflow_main/.undo/to-savepoint/dbos-wf-main-commit-000042/.apply
```

Undo operations are logged and can themselves be undone.

### What it does not provide

TigerFS history does not automatically make a complete Agent turn atomic:

- A turn may perform many independently committed file operations before its final savepoint is created.
- Savepoint creation is another operation after those writes; it is not a transaction that encloses them.
- A logical editor save may appear as several operations because editors often create a temporary file, delete the old file, and rename the temporary file.
- `--user-id` records attribution and supports filtered undo, but it is not safe isolation when writers interleave changes to the same file.
- Writes through `.tables/<workspace>/` bypass file-first history and undo.
- A savepoint can be renamed or deleted unless database permissions prevent it.
- Local direct-PostgreSQL mounts do not gain a lightweight branch mechanism from `tigerfs fork`. That command is a database-service fork for supported cloud backends, not a local workspace branch.

The integration must therefore add coordination, isolation, and access control around TigerFS's primitives.

## 3. Required database layout

Use three databases on one pgembed PostgreSQL cluster:

```text
{app}_dbos_sys    DBOS system and workflow recovery state
{app}_nooa_proj   NOOA projections and durable LLM ledger
{app}_workspace   TigerFS file-first workspaces and version history
```

For example:

```text
code_agent_dbos_sys
code_agent_nooa_proj
code_agent_workspace
```

This is the recommended deployment topology. The current `DurableDBOSRuntime` defaults the first two names to `{app}_dbos_sys` and `{app}_nooa_proj`; the workspace database is an additional integration requirement.

The connection contract is explicit:

- DBOS uses `{app}_dbos_sys`.
- `projection_conn_factory()` targets `{app}_nooa_proj`; both the NOOA projection schema and the `nooa_llm` durable ledger schema are initialized through that connection.
- The TigerFS URI targets only `{app}_workspace`, for example `server.get_uri("code_agent_workspace")`.
- All TimescaleDB, UUIDv7, and mount-role checks must run against that exact workspace database.

### Isolation rules

- Mount only `{app}_workspace` through TigerFS.
- Never expose `{app}_dbos_sys` or `{app}_nooa_proj` as a writable Agent filesystem.
- Give the TigerFS mount role no privileges in the DBOS system or projection databases.
- Give the Agent access to the normal workspace tree, but do not expose `.tables/` as an alternate writable path.
- Protect TigerFS companion tables and savepoint rows from direct Agent SQL.
- Use one writable TigerFS workspace per `(workflow_id, branch_id)` and one active writer lease per workspace.

A stable `--user-id` remains useful for audit data:

```bash
tigerfs mount --user-id code-agent-worker-1 \
  "$WORKSPACE_DATABASE_URI" /mnt/nooa
```

It must not replace workspace-level single-writer isolation.

## 4. pgembed compatibility prerequisites

TigerFS history, log, savepoints, and undo require TimescaleDB. The workspace database must meet all of these conditions:

1. The TimescaleDB binary is bundled for the target pgembed platform.
2. `timescaledb` is present in `shared_preload_libraries` before PostgreSQL starts.
3. `CREATE EXTENSION timescaledb` has run in `{app}_workspace`.
4. A compatible `uuidv7()` generator is available.

Start pgembed with TimescaleDB preloaded:

```python
from pathlib import Path

import pgembed

server = pgembed.get_server(
    Path(".nooa/pgdata"),
    shared_preload_libraries="timescaledb",
)
```

Create and initialize the workspace database with an administrative connection:

```sql
CREATE DATABASE code_agent_workspace;
```

Then connect to `code_agent_workspace` and run:

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
```

### PostgreSQL 17 and `uuidv7()`

The pgembed build tested for this guide contains PostgreSQL 17.10. TigerFS v0.7.0 emits table definitions that call `uuidv7()`, while this PostgreSQL/TimescaleDB combination exposes `generate_uuidv7()` instead of a function named `uuidv7()`.

Without a compatibility function, creation of a file-first workspace fails with:

```text
ERROR: function uuidv7() does not exist
```

For this exact pgembed PostgreSQL 17 + TimescaleDB 2.27.1 combination, first inspect the available functions as an administrator:

```sql
SELECT
    to_regprocedure('pg_catalog.uuidv7()') AS native_uuidv7,
    to_regprocedure('public.uuidv7()') AS public_uuidv7,
    to_regprocedure('public.generate_uuidv7()') AS timescaledb_generator;
```

If neither native nor public `uuidv7()` exists, and `public.generate_uuidv7()` is present, create a compatibility function without replacing any existing implementation:

```sql
CREATE FUNCTION public.uuidv7()
RETURNS uuid
LANGUAGE sql
VOLATILE
AS 'SELECT public.generate_uuidv7()';
```

Verify the unqualified lookup using the exact database role that will run TigerFS:

```sql
SET ROLE code_agent_tigerfs;
SHOW search_path;
SELECT public.uuidv7(), uuidv7();
RESET ROLE;
```

The mount role's `search_path` must resolve the unqualified `uuidv7()` emitted by TigerFS. If an implementation already exists, verify rather than overwrite it.

This compatibility function was functionally verified with savepoint creation and undo, but it is a compatibility measure, not an upstream TigerFS promise. Before production use, run the ordering, concurrency, savepoint, undo, backup, and crash tests in this guide against the exact pgembed wheel. Prefer a future pgembed PostgreSQL/TigerFS combination with a natively supported UUIDv7 contract when available.

Basic prerequisite checks:

```sql
SHOW shared_preload_libraries;

SELECT extversion
FROM pg_extension
WHERE extname = 'timescaledb';

SELECT uuidv7();
```

Fail startup if any check fails. Do not silently create a workspace without history.

## 5. Workspace identity and checkpoint contract

Map each durable branch to a deterministic workspace name. Because workflow and branch IDs may contain unsafe path or SQL identifier characters, derive the visible name from sanitized prefixes and a stable hash, for example:

```text
wf_8d04f2b1__br_main_0d6e
```

The authoritative NOOA durable state should include a checkpoint similar to:

```python
from pydantic import BaseModel


class WorkspaceCheckpoint(BaseModel):
    cluster_identity: str
    database: str
    workspace: str
    workflow_id: str
    branch_id: str
    commit_seq: int
    fencing_token: int
    savepoint_name: str
    savepoint_id: str
    manifest_hash: str
```

Recommended meanings:

- `cluster_identity`: stable identity of the pgembed data directory or runtime manifest.
- `database`: expected `{app}_workspace` database.
- `workspace`: deterministic TigerFS workspace name.
- `workflow_id` and `branch_id`: prevent a checkpoint from being attached to a different durable run.
- `commit_seq`: monotonic NOOA turn/workspace checkpoint sequence.
- `fencing_token`: lease epoch of the writer that created this checkpoint; a takeover must acquire a strictly greater token.
- `savepoint_name`: deterministic human-readable TigerFS name.
- `savepoint_id`: TigerFS's generated UUIDv7 identity, used to detect deletion and replacement under the same name.
- `manifest_hash`: hash of the workspace configuration and integration version, not the entire file tree.

The field must be inside recoverable durable state and covered by its canonical hash. It must not live only in `TurnEvidence`, the query-only projection, an in-memory Agent object, or a process-local cache, because NOOA recovery does not treat those as authoritative inputs.

This cannot be added in place to the current `nooa.durable.codeact:a8-v1` contract without a compatibility decision. A workspace-enabled implementation must define a new durable profile/application version or a formally compatible extension envelope. Its rollout plan must cover pending-workflow drain or migration, legacy state decoding, state-hash fixtures, DBOS application-version derivation, and rollback behavior. Until that migration exists, current A8 pending workflows must not be resumed under a workspace-enabled state model.

Fencing and compare-and-set state require an application-owned coordination table or equivalent service; TigerFS savepoints alone do not implement leases. The coordination record should be keyed by workspace and store the current lease epoch, expected prior checkpoint identity, and candidate sequence. Only the coordinator role may update it. DBOS remains authoritative for which successfully prepared candidate is accepted as a durable NOOA checkpoint.

Use deterministic savepoint names:

```text
dbos-<workflow-hash>-<branch-hash>-commit-000042
```

Do not choose a new random savepoint name on every DBOS retry.

## 6. Commit and recovery protocol

Assume DBOS currently points to committed TigerFS savepoint `S_n`.

### 6.1 Before executing a turn

1. Acquire the exclusive writer lease and a monotonically increasing fencing token for the workflow branch's workspace.
2. Fence the previous owner. If TigerFS cannot validate the token on every file operation, the supervisor must terminate or unmount the old writer before takeover.
3. Restore `WorkspaceCheckpoint(S_n)` from committed `TurnCommit.state`.
4. Verify cluster identity, database, workspace, workflow ID, and branch ID; require the newly acquired fencing token to be strictly greater than `S_n.fencing_token` on takeover.
5. Read `.savepoint/<name>/savepoint_id` and require an exact match with `S_n.savepoint_id`.
6. Inspect the TigerFS operation tail after `S_n`.
7. If any NOOA-provisional operations exist, atomically undo to `S_n`.
8. Remove or reject a stale deterministic candidate name for `S_n+1`.
9. Only then expose the workspace to Agent code.

Missing or mismatched savepoint metadata is corruption. A failed undo or fencing operation is also a recovery failure, not permission to continue. Fail closed and enter operator reconciliation; do not guess from the newest savepoint.

### 6.2 During the turn

The Agent may create, edit, rename, and delete source or intermediate files inside its assigned workspace. All persistent writes must go through the file-first path.

Recommended sandbox policy:

- Make the TigerFS workspace the only persistent writable project path.
- Keep caches and disposable temporary data on explicitly non-durable paths.
- Reject persistent writes outside the mounted workspace.
- Do not give generated code credentials for direct writes to TigerFS backing or companion tables.

TigerFS commits and exposes each operation immediately. These writes are **NOOA-provisional** until DBOS commits the turn checkpoint, so uncoordinated readers may observe state that recovery later compensates.

### 6.3 Preparing the next checkpoint

After the Agent has completed all intended writes:

1. Flush and close Agent-owned file descriptors.
2. Create deterministic savepoint `S_n+1`.
3. Read back its generated `savepoint_id`.
4. Verify its name, ID, workspace, operation boundary, and current fencing token.
5. In the application-owned coordination record, compare-and-set the candidate against the expected `S_n` name, ID, sequence, and the current lease token; reject stale ownership or an advanced checkpoint.
6. Construct `WorkspaceCheckpoint(S_n+1)`.
7. Include it in the next recoverable durable state and its canonical hash.
8. Return the complete `TurnCommit` from the DBOS step.

Only DBOS's durable commit of that step makes `S_n+1` authoritative.

### 6.4 Recovery after a crash or retry

On retry, always start from the checkpoint found in committed DBOS state:

- Fence the previous writer before inspecting or changing the workspace.
- If DBOS still points to `S_n`, compensate every TigerFS operation after `S_n`, even if an `S_n+1` candidate savepoint already exists.
- If DBOS points to `S_n+1`, verify that exact name, savepoint ID, sequence, and lease epoch before continuing.
- Never select the latest TigerFS savepoint merely because it exists.
- If compensation cannot complete, stop recovery and require operator reconciliation.

This is a **saga/compensation protocol**, not a cross-database transaction. Its safety property is:

> TigerFS may contain committed database history that is still provisional from NOOA's perspective, but committed DBOS state must never point to a missing or unverified TigerFS savepoint.

## 7. Crash matrix

| Crash point | DBOS authority | Recovery action |
|---|---|---|
| Before any file write | `S_n` | Verify `S_n`; run the turn. |
| During a sequence of file writes | `S_n` | Undo atomically to `S_n`; retry. |
| After writes, before creating `S_n+1` | `S_n` | Undo to `S_n`; retry. |
| During savepoint creation | `S_n` | Verify whether the deterministic candidate is complete; regardless, restore to `S_n` and remove/reject the stale candidate. |
| After `S_n+1` exists, before DBOS commits | `S_n` | Undo to `S_n`; the candidate is not authoritative. |
| DBOS commits but the caller loses the response | `S_n+1` | DBOS recovery returns committed state; verify `S_n+1`, do not undo it. |
| After DBOS commit | `S_n+1` | Continue from `S_n+1`. |
| Savepoint name exists with a different ID | Unknown/corrupt | Fail closed; require reconciliation. |
| DBOS references a missing savepoint | Corrupt | Stop the workflow; restore from a coordinated backup or operator-approved reconstruction. |

## 8. NOOA durable effect integration

NOOA's durable effect registry rejects undeclared external effects. A raw `Path.write_text()` does not become exactly-once merely because it ran inside a DBOS step.

Introduce a controlled workspace subsystem with two layers:

### `WorkspaceStore`

Expose deterministic operations and checkpoint control, for example:

```python
class WorkspaceStore:
    async def acquire(self, workflow_id: str, branch_id: str) -> int: ...
    async def fence_previous_writer(self, fencing_token: int) -> None: ...
    async def reconcile(self, checkpoint: WorkspaceCheckpoint, fencing_token: int): ...
    async def create_checkpoint(
        self,
        previous: WorkspaceCheckpoint,
        expected_seq: int,
        fencing_token: int,
    ) -> WorkspaceCheckpoint: ...
    async def release(self, fencing_token: int) -> None: ...
```

Normal file operations may still use the mounted filesystem while the lease is held, but checkpoint creation and reconciliation must be brokered and auditable.

### `DurableTransactionalEffectAdapter`

Register the checkpoint/reconciliation boundary as a NOOA `TRANSACTIONAL` effect with:

- the deterministic NOOA invocation key;
- a canonical request hash;
- expected previous checkpoint name and ID;
- expected next sequence and previous checkpoint;
- current fencing token and compare-and-set validation in the application-owned workspace coordination record;
- deterministic candidate savepoint name;
- verification before returning success;
- compensation back to the previous committed savepoint on retry.

Here, `TRANSACTIONAL` means that NOOA routes the effect through a dedicated adapter. It does **not** mean one PostgreSQL transaction covers both the TigerFS database and the DBOS system database.

Record file/checkpoint activity in `TurnEvidence` for audit, but keep the authoritative `WorkspaceCheckpoint` in `DurableSessionState`.

A process-local runtime cache may hold mount handles or health checks, provided cold restore is semantically equivalent. Never serialize FUSE/NFS handles, database connections, or mount process IDs into durable state.

## 9. Concurrency and branch rules

### Single writer

The mandatory rule is one fenced active writer for each `(workflow_id, branch_id)` workspace. Read-only observers can use a separate read-only mount or SQL connection. Lease expiry alone is insufficient: takeover must prevent the stale process or its TigerFS mount from issuing later writes.

Do not depend on per-user undo for isolation. If two users edit the same file between one savepoint and another, undoing one user's operations can also reverse the other's interleaved content.

### Branches

Give each durable branch a separate TigerFS workspace or backing table:

```text
wf_8d04f2b1__br_main_0d6e
wf_8d04f2b1__br_fix_auth_31aa
```

Creating a NOOA branch is an application-level operation:

1. Select and verify the parent's committed `WorkspaceCheckpoint`.
2. Materialize that checkpoint into a new isolated workspace.
3. Create a genesis savepoint in the child workspace.
4. Commit the child's checkpoint in its own durable branch state.

Do not use `--user-id` as a branch, and do not assume local `tigerfs fork` creates a workspace branch.

## 10. Security and corruption controls

Use separate PostgreSQL roles for administration, TigerFS mounting, NOOA projections, and DBOS runtime ownership.

The TigerFS mount role should be able to operate only on the intended workspace database and schemas. Generated Agent code should not be able to:

- connect directly to the DBOS system database;
- edit the NOOA projection/LLM ledger database;
- write through `.tables/`;
- alter or drop TigerFS triggers and companion tables;
- rename or delete committed savepoints;
- create arbitrary compatibility functions or extensions.

Store the mount role's credentials outside Agent-visible context. Treat a savepoint name/ID mismatch, missing history trigger, disabled history feature, or direct backing-table modification as a fatal compatibility error.

## 11. Backup, restore, and lifecycle

All three databases form one logical recovery set even though they remain isolated databases.

For the simplest consistent backup:

1. Stop new DBOS workflow execution.
2. Wait for active turns to finish or compensate them to their last committed checkpoint.
3. Unmount TigerFS cleanly.
4. Stop the pgembed PostgreSQL server.
5. Back up the entire pgembed data directory and the NOOA runtime sidecar/manifest together.

Independent online `pg_dump` operations do not automatically provide a common point-in-time snapshot across databases. If online backup is required, build a coordinated protocol that records the committed DBOS checkpoint set, prevents writers from advancing during capture, and validates every referenced TigerFS savepoint after restore.

Startup order:

1. Start pgembed with required preload libraries.
2. Verify all three databases and compatibility manifests.
3. Verify TimescaleDB, `uuidv7()`, workspace history, and committed checkpoint references.
4. Mount TigerFS.
5. Start DBOS workflow recovery and new work.

Shutdown in reverse: stop new work, drain or compensate active turns, unmount TigerFS, then stop PostgreSQL.

## 12. Post-initialization smoke test

This is not a standalone installer. Before running it:

- start pgembed with `timescaledb` preloaded;
- initialize the exact workspace database returned by `server.get_uri("code_agent_workspace")`;
- activate TimescaleDB and verify `uuidv7()` as the TigerFS mount role;
- mount that URI at `/mnt/nooa` with a role allowed to use `.build`, `.savepoint`, and `.undo`;
- ensure no other writer owns the target workspace.

For example, after exporting the verified workspace URI:

```bash
tigerfs mount --user-id code-agent-smoke \
  "$WORKSPACE_DATABASE_URI" /mnt/nooa
```

The following sequence then validates the core TigerFS behavior:

```bash
set -eu

WORKSPACE=/mnt/nooa/workflow_main

printf 'markdown,history' > /mnt/nooa/.build/workflow_main
printf '# original\n' > "$WORKSPACE/result.md"

printf '{"description":"DBOS commit 0"}' \
  > "$WORKSPACE/.savepoint/dbos-wf-main-commit-000000.json"

savepoint_id="$({
  cat "$WORKSPACE/.savepoint/dbos-wf-main-commit-000000/savepoint_id"
})"
test -n "$savepoint_id"

printf '# provisional change\n' > "$WORKSPACE/result.md"

cat "$WORKSPACE/.undo/to-savepoint/dbos-wf-main-commit-000000/.info/summary"
touch "$WORKSPACE/.undo/to-savepoint/dbos-wf-main-commit-000000/.apply"

test "$(cat "$WORKSPACE/result.md")" = '# original'
```

This exact behavior was exercised successfully against the pgembed PostgreSQL 17.10 + TimescaleDB 2.27.1 build using the `uuidv7()` compatibility function above. The test confirmed savepoint ID creation, one-file undo preview, atomic undo application, and restoration of the original file content.

## 13. Required kill-point tests

Before calling the integration production-ready, automate at least these tests:

- Kill the worker before the first file write.
- Kill it between two file writes.
- Kill it after all writes but before savepoint creation.
- Kill it during savepoint creation.
- Kill it after savepoint creation but before the DBOS step commits.
- Lose the client response after DBOS commits.
- Retry the same deterministic checkpoint request several times.
- Start two writers and prove the lease rejects the second.
- Simulate lease takeover and prove the stale worker or stale TigerFS mount cannot write after fencing.
- Attempt a write through `.tables/` and prove access is denied.
- Delete or replace a committed savepoint as an administrator and prove recovery fails closed.
- Restart pgembed and TigerFS, then recover from the last committed checkpoint.
- Back up and restore the full consistency set, then verify every DBOS checkpoint name and ID.
- Generate UUIDv7 values under expected concurrency and verify the ordering assumptions used by TigerFS savepoint/undo queries.

## 14. Decision summary

This architecture is reasonable with the following boundaries:

- **DBOS is authoritative for committed Agent behavior and the selected workspace checkpoint.**
- **TigerFS is authoritative for current files, historical file content, and rollback mechanics.**
- **pgembed provides one deployable PostgreSQL cluster, not a cross-database transaction.**
- **A deterministic checkpoint and compensation protocol coordinates the two systems.**
- **One writable workspace per durable branch avoids unsafe interleaving.**
- **TimescaleDB preload, workspace activation, and UUIDv7 compatibility are hard startup gates.**
- **NOOA still needs an explicit `WorkspaceStore`, durable-state checkpoint field, lease, and effect adapter before the design is implemented end to end.**

## 15. References

- [TigerFS repository](https://github.com/timescale/tigerfs)
- [TigerFS history, savepoints, and undo](https://github.com/timescale/tigerfs/blob/v0.7.0/docs/history.md)
- [TigerFS file-first mode](https://github.com/timescale/tigerfs/blob/v0.7.0/docs/file-first.md)
- [TigerFS versioned history ADR](https://github.com/timescale/tigerfs/blob/v0.7.0/docs/adr/012-versioned-history.md)
- [TigerFS undo and recovery ADR](https://github.com/timescale/tigerfs/blob/v0.7.0/docs/adr/016-undo-and-recovery.md)
- [General TigerFS with pgembed guide](tigerfs.md)
