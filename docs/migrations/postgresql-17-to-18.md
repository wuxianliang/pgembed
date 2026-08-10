# 从 PostgreSQL 17 迁移到 pgembed PostgreSQL 18

**适用版本：** pgembed `0.3.0rc1`（bundled PostgreSQL 18.4）  
**迁移边界：** pgembed 只负责在启动前检测并拒绝旧 PGDATA；不会自动执行 major upgrade。

## 1. 升级后会发生什么

pgembed 的 PostgreSQL 18 bundle 在读取现有数据目录时会先检查 `PG_VERSION`：

- `PG_VERSION` 为 `17`：抛出 `PostgresDataDirectoryVersionError`。
- `PG_VERSION` 为空、损坏或不可解析：抛出 `PostgresDataDirectoryInspectionError`。
- 目录非空但没有 `PG_VERSION`：同样 fail closed。

这些错误发生在创建文件、修改 ownership/mode、写 PostgreSQL 配置、注册 PID handle、启动或停止 postmaster 之前。pgembed 不会把 PG17 数据目录传给 PG18，也不会静默执行 `initdb`、dump/restore 或 `pg_upgrade`。

```python
from pathlib import Path

import pgembed

try:
    pgembed.get_server(Path("/srv/my-app/pgdata"))
except pgembed.PostgresDataDirectoryVersionError as exc:
    print(exc.found_major, exc.expected_major)  # 17 18
    print(exc.migration_documentation)
```

> 不要通过手工把 `PG_VERSION` 从 `17` 改成 `18` 来绕过检查。该文件不是升级开关；catalog、WAL、控制文件和所有原生扩展仍属于 PG17 ABI。

## 2. 迁移前清单

在仍能使用原 PG17 wheel 和原 PGDATA 时完成清单与备份。记录输出并保存在 PGDATA 之外。

### 2.1 记录版本、数据库和对象

```sql
SELECT version();
SHOW data_directory;
SHOW shared_preload_libraries;
SHOW data_checksums;

SELECT datname, datcollate, datctype
FROM pg_database
ORDER BY datname;

SELECT extname, extversion
FROM pg_extension
ORDER BY extname;

SELECT spcname, pg_tablespace_location(oid)
FROM pg_tablespace
ORDER BY spcname;
```

另外记录：

- roles、role memberships、ownership 和 grants；
- 所有数据库、tablespaces、large objects 和应用连接参数；
- 原 PG17 bundle 的完整版本、扩展版本及 preload 顺序；
- locale/collation provider 与 collation version；
- checksums 是否启用；
- 备份文件路径、校验和、恢复命令和所需磁盘空间；
- TigerFS workspace、mount 状态以及任何正在运行的 TigerFS daemon。

### 2.2 盘点 UUIDv7 历史 shim

PG18 提供原生 `pg_catalog.uuidv7()`。迁移前记录旧对象，而不是按名称直接覆盖或删除：

```sql
SELECT
    to_regprocedure('public.uuidv7()') AS public_uuidv7,
    to_regprocedure('public.generate_uuidv7()') AS public_generate_uuidv7;

SELECT
    n.nspname,
    p.proname,
    pg_get_userbyid(p.proowner) AS owner,
    l.lanname AS language,
    pg_get_functiondef(p.oid) AS definition
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid = p.pronamespace
JOIN pg_language AS l ON l.oid = p.prolang
WHERE n.nspname = 'public'
  AND p.proname IN ('uuidv7', 'generate_uuidv7');
```

`public.uuidv7()` 或 `public.generate_uuidv7()` 可能由应用拥有。只有 owner、language 和 definition 与已知旧 wrapper 完全一致时，才可在单独的应用迁移中考虑删除。未知实现必须保留并人工审查。fresh PG18 cluster 不应创建 `public.uuidv7()` shim。

## 3. 先做可恢复备份

至少保留一份未被 PG18 修改的 PG17 PGDATA 快照，以及一份逻辑备份。停止写入或建立明确的一致性窗口；在复制 PGDATA 前先正常停止 PG17 server 和 TigerFS mount/daemon。

### 3.1 全局对象

使用与源 PG17 server 匹配的工具：

```bash
pg_dumpall --globals-only --file=globals-pg17.sql "postgresql://.../postgres"
```

### 3.2 每个数据库的 custom-format dump

```bash
pg_dump --format=custom --file=mydb-pg17.dump "postgresql://.../mydb"
```

对所有数据库重复，并对 dump 文件生成独立校验和。执行一次测试恢复；只有“命令成功”而未验证恢复结果的备份不能作为唯一 rollback 依据。

## 4. 选择外部迁移方式

pgembed 本身不提供迁移 API。以下工具链必须由用户或外部运维环境提供，并且必须包含与源/目标 major 匹配的 PostgreSQL binaries 和目标 PG18 兼容扩展。

### 4.1 逻辑 dump/restore（默认建议）

适用于可以接受维护窗口、希望获得干净 PG18 cluster、或无法同时提供 PG17/PG18 原生扩展 binary 的场景。

1. 保留原 PG17 PGDATA，不在其上运行 PG18。
2. 使用 pgembed PG18 bundle 创建一个**不同路径**的 fresh PG18 PGDATA。
3. 先恢复 roles/tablespaces 等全局对象。
4. 创建目标数据库并安装/预加载 PG18-compatible 扩展。
5. 使用 PG18 `pg_restore` 恢复数据库。
6. 执行 application migrations、`ANALYZE`、索引和业务校验。

示例骨架：

```bash
psql "postgresql://.../postgres" -f globals-pg17.sql
createdb --dbname="postgresql://.../postgres" mydb
pg_restore --dbname="postgresql://.../mydb" --exit-on-error mydb-pg17.dump
```

根据 ownership 策略选择是否使用 `--no-owner` / `--role`；不要无条件加入这些参数，否则可能改变权限语义。

### 4.2 外部 `pg_upgrade`

仅在同时具备以下条件时使用：

- 可用、可信且与源 cluster 匹配的 PG17 `bindir`；
- pgembed PG18.4 的目标 `bindir`；
- 源与目标所需扩展在两个 major 下都可用；
- 足够的磁盘、停机窗口和可测试 rollback；
- 已阅读 PostgreSQL 18 的 `pg_upgrade` 要求。

先创建独立的 fresh PG18 target，再在外部环境运行检查：

```bash
pg_upgrade \
  --check \
  --old-bindir=/path/to/postgresql-17/bin \
  --new-bindir=/path/to/pgembed-pg18/bin \
  --old-datadir=/path/to/pg17-data \
  --new-datadir=/path/to/new-pg18-data
```

只有 `--check` 通过并处理所有 extension/library 报告后，才能安排实际升级。优先使用复制模式来保留 rollback；不要在没有独立备份时默认使用 `--link`。实际参数、socket/port、tablespace mapping 和权限必须按部署环境确定。

### 4.3 Logical replication / 双写迁移

适用于需要缩短停机但能承担更复杂运维的场景：

1. 建立独立 PG18 cluster。
2. 先迁移 schema、roles 和扩展。
3. 配置 publication/subscription 或应用级双写。
4. 监控复制延迟、sequence、large objects、DDL 和不受支持对象。
5. 在受控切换窗口停止写入、追平、验证后切换连接。

该方案不是 pgembed 自动化能力；复制拓扑、冲突处理和回切策略由应用负责。

## 5. 扩展与 preload 检查

PG18 candidate 的 bundle metadata 位于：

```text
pgembed/pginstall/share/pgembed/build-metadata.json
```

迁移前后比较：

```sql
SHOW shared_preload_libraries;
SELECT extname, extversion FROM pg_extension ORDER BY extname;
SELECT name, default_version, installed_version
FROM pg_available_extensions
ORDER BY name;
```

注意事项：

- 只恢复 PG18 bundle metadata 声明为 built 的扩展。
- preload 扩展必须在启动前配置；当前完整 release bundle 包括 `vchord,timescaledb,pg_cron,pg_net`，实际集合以 packaged metadata 为准。
- 不要把 PG17 `.so`、control/SQL 文件或旧 source/build directory 复制到 PG18 prefix。
- 恢复后验证 vector/VectorChord/BM25 索引、Timescale hypertable、AGE、pg_cron 和 pg_net 的实际功能，而不仅是 `CREATE EXTENSION` 成功。

## 6. Collation、checksum 与索引

major migration 前后记录并比较：

```sql
SHOW data_checksums;

SELECT collname, collprovider, collversion,
       pg_collation_actual_version(oid) AS actual_version
FROM pg_collation
WHERE collversion IS DISTINCT FROM pg_collation_actual_version(oid)
ORDER BY collname;
```

不要假定目标 cluster 的 locale、ICU、libc 或 checksum 默认与源一致。对 collation version 变化，按 PostgreSQL 报告和应用索引使用情况安排 `REINDEX`/刷新 collation version；对 checksum 选择，在创建目标 cluster 前明确决定并记录。pgembed 不会自动修改这些设置。

## 7. 迁移后验收

在切换应用流量前至少验证：

```sql
SELECT current_setting('server_version_num')::int / 10000 = 18;
SELECT pg_catalog.uuidv7();
SELECT substr(pg_catalog.uuidv7()::text, 15, 1) = '7';
SELECT to_regprocedure('public.uuidv7()');
```

并完成：

- database/role/schema/table/row-count 抽样与权限检查；
- extension catalog、preload、worker、索引和 query-plan smoke；
- 同一 PG18 PGDATA 的 stop/restart 验证；
- TigerFS 使用时，在不创建 UUID shim 的情况下验证 history/savepoint/undo，并按 `unmount → reclaim daemon → stop PostgreSQL` 清理；
- application read/write、备份与恢复演练。

## 8. Rollback 边界

| 场景 | 可用 rollback |
|---|---|
| PG18 wheel 仅检测到 PG17 PGDATA 并 fail-fast | 原目录未被 pgembed 修改；重新安装 PG17 wheel 后可继续使用原 PG17 PGDATA。 |
| 已创建新的 fresh PG18 PGDATA | PG17 不能读取它；删除目标或切回保留的 PG17 source/backup。 |
| dump/restore 到独立 PG18 目录 | 保留 PG17 source 和切换前写入边界即可回切；需要处理切换后的新写入。 |
| `pg_upgrade` copy 模式 | 以检查结果和保留的旧 cluster 为 rollback 基础。 |
| `pg_upgrade --link` | 旧 cluster 可能不再是安全 rollback 点；没有独立快照时不要使用。 |
| 已让应用在 PG18 上持续写入 | 回切需要数据同步/补偿，不能只降级 Python package。 |

PG17 wheel 也不应直接读取 PG18 PGDATA。package downgrade 不是 data downgrade。

## 9. 常见错误

- **错误：** 编辑 `PG_VERSION` 绕过 fail-fast。  
  **后果：** PG18 会面对 PG17 格式的数据文件，存在严重损坏风险。

- **错误：** 在原 PG17 目录上让 pgembed 创建 fresh cluster。  
  **正确做法：** 使用一个新的空路径并保留源目录。

- **错误：** 只复制数据库目录中的部分文件或 PG17 extension libraries。  
  **正确做法：** 使用受支持的逻辑恢复或完整的外部 `pg_upgrade` 流程。

- **错误：** 因名称相同而自动删除 `public.uuidv7()`。  
  **正确做法：** 先记录 owner/language/definition，再进行明确的应用迁移。

- **错误：** 迁移成功后立即删除 PG17 backup。  
  **正确做法：** 完成业务、扩展、restart、备份恢复和观察期验收后再按保留策略清理。

## 10. 参考

- PostgreSQL 18：Upgrading a PostgreSQL Cluster
- PostgreSQL 18：`pg_upgrade`
- PostgreSQL 18：UUID Functions
- `docs/tigerfs.md`
