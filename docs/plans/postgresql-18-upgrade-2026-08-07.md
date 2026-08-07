# pgembed 升级至 PostgreSQL 18：实施计划

**日期：** 2026-08-07  
**状态：** 实现完成，等待三平台 clean native validation
**交付范围：** PostgreSQL 18.4 RC implementation and release gates

## 0. 实施状态（2026-08-07）

已落地 PostgreSQL 18.4 与全部发布扩展/toolchain 的 immutable source locks、whole-prefix bundle stamp 与 completion marker、schema-v1 build metadata、runtime ABI attestation、PGDATA major fail-fast、有界生命周期与回收、standalone extension ABI 边界、PG17→PG18 迁移文档，以及分阶段 CI/release gates。构建 source verification 使用独立成功 marker；tarball 先校验临时文件再原子发布；repaired wheel 会审计 ELF/Mach-O 动态依赖。每个平台还会归档 wheel SHA-256、由 bundle metadata 派生的 source-lock 清单和 JUnit 报告，publish job 会在上传前重新校验下载到的 wheel 集合与哈希。

首次 macOS arm64 clean native build 先证明 VectorChord 1.1.1 使用的 `NonNull::from_ref` 在 Rust 1.88.0 仍为 unstable；继续完整编译又证明其 `VecDeque::pop_front_if` 在 Rust 1.89.0 仍为 unstable。本机兼容性探测确认 Rust 1.95.0 同时支持这两个 API，因此 immutable toolchain lock 已修正为 Rust 1.95.0，cargo-pgrx 保持 0.17.0，并同步 Makefile、CI 与测试期望；未修改已校验的 VectorChord 上游源码。

本地已完成 targeted unit/static validation 和全部非原生测试，并已按用户授权彻底清理 PostgreSQL 17 build cache 与 native prefix。macOS arm64 PostgreSQL 18 clean native build 已完成：bundled `postgres` 与 `pg_config` 均报告 18.4，pgvector、VectorChord、pg_duckdb、AGE、psql_bm25s、TimescaleDB、pg_cron、pg_net 全部构建并安装，TigerFS 二进制、schema-v1 `build-metadata.json` 与 bundle completion marker 均已生成，无 silent skip。在该 native prefix 上 `pytest tests/` 全量 131 passed，其中 `-m integration` 29 passed、`-m tigerfs_mount` 1 passed（真实 macOS mount）。本机结果只作为 macOS arm64 native 证据，不视为 Linux x86_64、Linux aarch64 或三平台 installed-wheel runtime 证明。

构建过程中记录到两项与配方无关的环境问题，均不需要修改已锁定的源码或 recipe：AGE clone 曾因网络吞吐跌破 1000 B/s 触发 `Error 128`，重试即通过；构建中断会因 completion marker 缺失触发既定的 whole-prefix invalidation，同一次 make 运行内的陈旧路径缓存会导致紧随其后的子 make 失败，重新发起一次构建即可干净重建。

已知未关闭缺陷：`pgbuild/patches/pg_duckdb-v1.1.1-planner-hook-chain.patch` 消除了 #845/#963 的 segfault，但把它转成了 **intermittent hang**。timescaledb 与 pg_duckdb 同时 preload、且已执行过 hypertable + `time_bucket` 查询后，`SET duckdb.force_execution = true` 下的 `SELECT count(*)` 有约 25%（2/8 隔离运行）概率挂起；`pg_stat_activity` 显示 backend 阻塞在 `IPC / ParallelFinish`，等待一个永不返回的 parallel worker，且不受 `max_parallel_workers_per_gather=0` 约束（pg_duckdb 的 `InitRunWithParallelScan` 自行启动 worker）。因此 `tests/test_pgembed.py::test_pg_duckdb_timescaledb_planner_probe_survives` 在全量运行中会间歇性因 30s psql timeout 失败（3 次全量运行中 1 次）。WI-10 的 "planner survival" 判据尚未真正满足，详见 `docs/investigations/pg_duckdb-timescaledb-time-bucket-collision-2026-07-28.md` Phase 5。

发布验收仍须由 CI 完成以下 gates，全部通过后本计划的“Done when”才算满足：

- 三平台 PostgreSQL 18.4 clean native build、bundle metadata/artifact audit 与 repaired-wheel dependency audit（macOS arm64 native build 已在本机通过，仍需 CI 无 cache 复现，Linux x86_64 与 Linux aarch64 未验证）；
- installed-wheel bundled tools、PG17 PGDATA rejection、全 preload/catalog/index/restart 与 TimescaleDB↔pg_duckdb planner survival（已在本机 native prefix 通过，尚未在任何平台的 installed wheel 上验证）；
- macOS arm64 真实 TigerFS mount/savepoint/undo 验证已在本机 native prefix 通过；Linux mount 仍待具备 `/dev/fuse` 的专用 runner 执行；
- pre-release artifact 证据归档、wheel hash verification 与实际 publish（工作流已实现，尚未执行）。

## 1. Goal

将 pgembed 的 bundled PostgreSQL 从 17 升级到 18，并把 PostgreSQL server、原生扩展、构建缓存和 wheel 内容作为一个可验证、可复现的 ABI 原子单元迁移。升级完成后，新 wheel 必须安全拒绝直接启动 PG17 PGDATA，完整验证 PG18 上的扩展、preload、索引、restart、TigerFS UUIDv7 语义与三平台发布流程，同时保持现有 Python 调用签名和 `pginstall` 目录约定。

## 2. Done when

升级只有在下列条件全部满足时才算完成：

- bundled `postgres`、`pg_config`、构建 metadata 和公开常量均报告 PostgreSQL major 18。
- PG18 wheel 遇到 PG17 PGDATA、损坏的 `PG_VERSION`，或非空但缺少 `PG_VERSION` 的目录时，在任何配置写入或进程操作前结构化 fail closed。
- clean build 中 AGE 使用 PG18 分支、pg_cron 使用 PG18-compatible tag；所有声明支持的扩展均在目标平台完成 runtime 验证，而不只是编译通过。
- 构建 stamp 和发布门禁能阻止 PG17 `.so`、control/SQL 文件或 source cache 混入 PG18 wheel。
- fresh PG18、全 preload 集合、扩展 catalog、索引、restart、TimescaleDB↔pg_duckdb 回归和 TigerFS UUIDv7 验证全部通过。
- macOS arm64、Linux x86_64、Linux aarch64 的 wheel 都通过 clean build、installed-wheel smoke、native integration 和适用的 migration/TigerFS gates。
- 发布工作流只发布通过全部适用 gates 的 artifact；首轮进入 opt-in pre-release/RC，而不是沿用“tag 即正式发布”。
- 文档明确 PG17 PGDATA 的产品承诺、迁移路径、备份与 rollback 边界。

## 3. Scope and non-goals

### 3.1 In scope

- PostgreSQL 18 source identity、configure/build/install 和缓存失效边界。
- bundled build metadata、runtime major 验证和 extension ABI attestation。
- PGDATA major 检测、结构化错误、constructor rollback、有界启动与失败恢复。
- 当前发布扩展集合在 PG18 上的 recipe、runtime、catalog、preload、索引和 restart 验证。
- TimescaleDB↔pg_duckdb 创建顺序与 planner 生存性回归。
- TigerFS 在 PG18 原生 `uuidv7()` 下的 SQL 与 mount 行为。
- CI/release 分阶段门禁、wheel audit、动态依赖审计和文档迁移说明。
- 明确采用 PG17 PGDATA fail-fast + 外部迁移文档；本次不新增 dump/restore API，也不在 wheel 中携带 PG17 双版本工具链。

### 3.2 Explicit non-goals

- 不建设通用插件系统、动态 extension dependency solver 或新的 extension registry framework。
- 不重构 `POSTGRES_BIN_PATH`、命令自动包装模型或 TigerFS mount manager。
- 不改变 `get_server()`、`PostgresServer` constructor、`has_extension()`、`list_extensions()`、`create_extension()` 的现有调用签名。
- 不新增 Windows、universal2 或 musl release artifact；musl 仅保留既有规则验证。
- 不借 PG18 升级扩大 Python 支持到 3.10/3.11；`pyproject.toml:6` 的 `requires-python >=3.12` 是权威声明。
- 不把本机 clean-build 探针视为三平台发布证明。
- 不在普通 `get_server()` 启动路径中静默迁移或原地修改 PG17 PGDATA。

## 4. Background and current state

### 4.1 Wheel-wide ABI boundary

- `pgbuild/Makefile:2` 把 PostgreSQL 与所有原生扩展安装到 `src/pgembed/pginstall`。
- `MANIFEST.in:1` graft 整棵 `src/pgembed/pginstall`，所以 server binary、`pg_config`、shared libraries、control/SQL 文件和 TigerFS 一起进入 wheel。
- `pgbuild/Makefile:50-61` 的 extension targets 全部依赖同一 installed PostgreSQL；PG major 变化不是单个变量更新，而是整个 native payload 的原子 ABI 切换。
- top-level `Makefile` 继续作为 `make -C pgbuild all` 的单一入口；不把 PostgreSQL 编译迁移进 Python build backend。

### 4.2 Why PG17 was selected historically

- Git 历史中 `8ea2c87` 曾切换到 PG18。
- 加入 pgvectorscale 0.5.1 后，`b96704b` 以 `Downgrade to pg17` 回退；该版本的 pgrx feature 只覆盖 PG13–17。
- `c000063` 已移除 pgvectorscale，因此历史上的直接 blocker 已不在当前 extension set。
- 当前选择 PG17 不再由仍存在的功能需求强制，而主要是构建 recipe、extension pin、PGDATA 安全边界和发布验证尚未完成 PG18 化。

### 4.3 Current build identity and reproducibility gaps

- `pgbuild/Makefile:64-66` 当前使用 `POSTGRES_VERSION := 17-stable`、`POSTGRES_SRC := REL_17_STABLE` 和 `POSTGRES_BLD := $(POSTGRES_SRC)`。
- checkout 实际由 mutable `REL_17_STABLE` 控制；同一 ref 在不同时间可解析到不同 commit。
- `psql_bm25s` 也使用 mutable `main`（`pgbuild/Makefile:214-218`）；VectorChord build 使用未锁定的 `nightly` Rust toolchain（`pgbuild/Makefile:144-147`）。
- tarball 组件除 TigerFS 外没有统一的 in-repo SHA-256 校验；TigerFS 的 pinned digest + content stamp 是可复用的仓库模式（`pgbuild/Makefile:260-317`）。
- CI cache 包含整个 `pgbuild` 与 `src/pgembed/pginstall`（`.github/workflows/build-and-test.yml:53-66,93-104,151-163`）。cache key 虽包含 tracked recipes 的 hash，但 make target 目前没有能对完整安装前缀执行内容级 ABI 失效的 PG bundle stamp。
- 发布/tag build 当前也未强制从空 prefix 构建，无法只依赖 cache key 证明没有 stale PG17 artifact。

### 4.4 Current runtime PGDATA hazard

- `PostgresServer.__init__()` 目前先准备root system user与`DiskList`，再注册 atexit、写 `_instances`，然后执行 init、preload 配置、启动和 `.handle_pids.json` 记录（`src/pgembed/postgres_server.py:58-98`）。任一中间异常都可能提前产生权限/registry副作用或留下 partial object。
- `ensure_pgdata_inited()` 先执行root permission/ownership准备，之后只检查 `PG_VERSION` 是否存在，不读取内容或 major（`src/pgembed/postgres_server.py:159-227`）。PG18 wheel 会把 PG17 PGDATA 当成“已初始化”，但在发现版本问题前已可能修改目录，随后还会尝试修改配置并用 PG18 `pg_ctl` 启动。
- 当 `PG_VERSION` 不存在时，当前代码会扫描并终止同路径的 postgres process 后直接 `initdb`。对于“非空但未知”的目录，这种行为过于宽松；PG18 升级后必须先证明目录确为 fresh cluster 目标。
- `ensure_postgres_running()` 对 `pg_ctl` 设有 10 秒 timeout，但随后 readiness 使用无界 `while True`（`src/pgembed/postgres_server.py:229-316`，loop始于299）。preload ABI 错误、postmaster 早退或 stale pid 情况可能变成无限等待。
- `_cleanup()` 的 `pg_ctl stop` 没有 subprocess timeout（`src/pgembed/postgres_server.py:318-359`）；后续 terminate/wait/kill fallback 已有界，因此应补齐前一调用而不是重写整段回收。

### 4.5 Current extension detection and ordering

- `src/pgembed/__init__.py:21-78` 使用 hardcoded registry、文件名和 `EXTENSION_PRECEDENCE`。
- `_detect_extensions()` 主要根据 package-local 或 bundled `.so/.dylib/.dll` 是否存在，以及部分 control/SQL 文件是否存在来判断 availability（`src/pgembed/__init__.py:105-151`）；文件存在不能证明其 built PostgreSQL major。
- package-local `pgembed_pgvector` / `pgembed_pgduckdb` artifact 可优先覆盖 bundled artifact，但当前没有 PG-major attestation。
- `EXTENSION_PRECEDENCE = {"pg_duckdb": ("timescaledb",)}` 必须保留。`docs/investigations/pg_duckdb-timescaledb-time-bucket-collision-2026-07-28.md` 证明 TimescaleDB 必须先创建，才能避免两者对 `public.time_bucket` 的冲突。
- `src/pgembed/_commands.py` 的动态 wrapper 与 TigerFS exclusion 是正确现有行为；本计划只要求回归，不要求重构。

### 4.6 PG18 clean-build reconnaissance

本机 PG18.4 clean-build 探针得到以下结果；这些结果用于确定最小 recipe 变化，但不能替代 release matrix：

| 组件 | 当前版本/ref | PG18 probe | 最小计划动作 |
|---|---|---:|---|
| pgvector | `v0.8.2` | PASS | 保持 `v0.8.2` |
| pg_duckdb | `v1.1.1` | PASS | 保持 `v1.1.1` |
| VectorChord | `1.1.1` | PASS | 保持版本并固定 Rust toolchain |
| Apache AGE | `release/PG17/1.7.0` | FAIL | 必须切 `release/PG18/1.8.0`，否则明确停止发布 |
| psql_bm25s | mutable `main` | PASS | 冻结已验证 exact commit；验证 ICU linkage |
| TimescaleDB | `2.27.1` | PASS | 保持 `2.27.1` |
| pg_cron | `v1.6.4` | FAIL | 必须切 `v1.6.7`，否则明确停止发布 |
| pg_net | `v0.20.5` | PASS | 保持 `v0.20.5` |
| TigerFS | `v0.7.0` | 非 PG ABI extension | 保持，验证 PG18 UUIDv7 语义 |

任何当前 release extension 都不能因单平台失败而静默消失。compatibility-only source lock中的每一项都必须在全部声明平台 build + runtime PASS；若实现中发现某项无法支持，必须暂停并重新进行范围决策，不能自行排除或静默修改 build default、CI matrix、runtime registry、metadata expected set、README 或 release notes。

### 4.7 Additional release inconsistencies to close

- `pyproject.toml:6` 声明 Python `>=3.12`，但 `.github/workflows/build-and-test.yml:112` 请求 cp310、cp311、cp312、cp313、cp314。PG18 release 将 wheel matrix 明确收敛为 cp312、cp313、cp314：cp310/cp311 本就不在 declared support 内，本次停止生成；cp314 保留。README/release notes 必须说明这是移除不符合声明的 artifacts，而不是额外放弃受支持版本。
- `.github/workflows/build-and-test.yml:167-188` 的 `publish-to-pypi` 只依赖 `build_wheels`；目前没有独立 runtime integration、migration boundary 或 TigerFS mount gate。
- Linux manylinux wheel smoke 不具备 FUSE 和稳定 background-worker 集成条件；wheel smoke 与 native runtime integration 必须拆开。

## 5. Architecture and detailed design

### 5.1 Invariants

实现必须保持以下不变量：

1. PostgreSQL major、source identity、configure flags、extension source locks 和 installed artifact catalog 共同定义一个 bundle identity。
2. 任意 bundle identity 变化都使整个 `INSTALL_PREFIX` 和所有 PG-linked build directories 失效；不得按单个目标“增量拼接”不同 major 的 artifact。
3. runtime 在读取或修改 PGDATA 前，先证明 bundled binary 与 packaged metadata 一致。
4. bundle validation与PGDATA major compare 在system-user创建、ownership/mode修复、配置写入、`pg_ctl`、pid handle、旧 process cleanup、`_instances` 和 atexit 注册前失败。
5. extension availability 必须由 artifact + SQL/control + built-major metadata 共同证明。
6. 所有启动、readiness 和 cleanup 等待都有 wall-clock 上限。
7. migration 是显式操作；普通 server startup 永远不自动升级旧 cluster。
8. release artifact 必须由 clean build 和 runtime gates证明，cache 只优化开发构建，不是 source identity 或 correctness boundary。

### 5.2 PostgreSQL source lock and configure identity

在 `pgbuild/Makefile` 中建立清晰变量：

- `POSTGRES_MAJOR := 18`
- `POSTGRES_VERSION := 18.4`
- `POSTGRES_SRC_REF := REL_18_4`
- `POSTGRES_SOURCE_COMMIT := f5cc81719e6da4cbdb1f797c48b693e91018153a`
- archive/checkout verification 信息
- 完整 configure 参数和构建 recipe schema version

已选择 exact source pin。首个 PG18 candidate 固定到已验证的 PostgreSQL `REL_18_4` tag，并校验其 commit 为 `f5cc81719e6da4cbdb1f797c48b693e91018153a`；release metadata 同时记录 tag 与 commit。后续 point/security update 必须显式修改这两个值、提升 bundle identity 并重跑全部 gates，禁止退回 mutable `REL_18_STABLE`。

保持当前 `--without-readline --without-icu` 还是启用 ICU，必须通过 PG18 feature需求和三平台依赖审计决定，不能因 PG18 默认或 psql_bm25s 自身 ICU linkage 被隐式改变。无论选择如何，`pg_config --configure`、checksum 默认和实际动态依赖都进入 metadata/gate。PG18 checksum/collation 影响作为迁移 preflight 和验证项，不在未验证时写成固定结论。

### 5.3 GNU Make 3.81-safe PostgreSQL bundle stamp

在 `pgbuild/Makefile` 新增 content-addressed、GNU Make 3.81-safe 的 `POSTGRES_BUNDLE_CONFIG_STAMP ?= .postgres-bundle-config.stamp`，模式复用 TigerFS stamp并使用现有 `FORCE:` prerequisite：

- stamp 内容至少包含 `schema=1`、recipe version、PG major/full version、source ref/commit、configure flags、install prefix、host/arch、deployment target、`PG_DLSUFFIX`、musl/libcurl capability、requested/final/skipped extension set、每个 extension source identity、Rust/cargo-pgrx toolchain、影响安装结果的 feature flags。
- 禁止使用 `$(file)`、`!=`、`.ONESHELL`、`private`、`undefine` 等 GNU Make 3.82+/4.x-only 功能。
- 用 temporary file + `cmp -s` + atomic `mv`；相同内容重跑不改变 mtime。
- 内容变化时删除整个 `INSTALL_PREFIX`、当前 PostgreSQL source/build directory，以及所有链接到旧 `pg_config` 的 native extension source/build directory/object；只删 `postgres` binary 不够。已由digest校验的下载 archive可保留，但重新解压目录必须删除；TigerFS仍由自己的 stamp校验并因prefix清空而重新安装。
- A→B→A、未来时间戳、partial prefix、extension set 缩减和 cache restore 后均能正确重建。
- stamp 必须在任何并行 PostgreSQL/extension build 前完成，并成为 `postgres`、所有installed extension targets和最终 bundle metadata target的直接或间接依赖；不得出现PG17 extension build与PG18 install交错。`all` 只有在 metadata 原子生成成功后才完成。
- stamp 更新或后续编译中断时，旧stamp保留或prefix已被清空；下次make必须重新失效/补齐缺失targets，不能把partial prefix视为完成。
- release/tag build仍使用全新 workspace/prefix；stamp 是 stale cache 的最终防线，不替代 clean release build。
- `POSTGRES_BUNDLE_CONFIG_STAMP` 路径和 `INSTALL_PREFIX` 必须允许测试覆盖，使 stamp target 可在 fake prefix 上独立运行，不触发真实 PostgreSQL 编译；复用 `tests/test_tigerfs_build.py` 的 dummy `postgres`/fake-prefix 模式。
- macOS CI 用 `@pytest.mark.skipif(platform.system() != "Darwin")` 限定 GNU Make 3.81 兼容测试，并断言 `make --version` 确为 GNU Make 3.81；其他平台仍执行相同 stamp 行为测试，但不宣称覆盖 3.81 实机。

### 5.4 Source verification and toolchain lock

- 对 tarball 组件记录并验证 in-repo SHA-256，模式与 TigerFS 一致。
- 对 git source 使用 exact commit；PostgreSQL 固定 `REL_18_4` + 已验证 commit，其余 git组件也记录 resolved commit。
- 将 psql_bm25s 从 `main` 冻结到本次 PG18 probe 的 exact commit。
- 将 VectorChord 的 `RUSTUP_TOOLCHAIN=nightly` 改为已验证的 exact toolchain；CI 的 Rust/cargo-pgrx 安装必须与 Makefile source lock一致。
- 对 pg_duckdb submodule tree 记录 resolved commits；不能只记录顶层 tag。
- source verification 失败必须在编译前终止，不能生成 metadata 或 wheel。

### 5.5 Packaged `build-metadata.json`

新增 `tools/generate_bundle_metadata.py`，在所有 selected components 安装完成后生成：

`src/pgembed/pginstall/share/pgembed/build-metadata.json`

建议 schema version 1 包含：

- bundle schema version与bundle recipe identity；
- `postgres_major`、full version、source ref、source commit；
- `pg_config --version`、`pg_config --configure`；
- host OS/arch、deployment target、libc；
- configure/checksum/ICU identity；
- 每个 extension 的 requested/built/skipped 状态、version/ref、commit/digest、`built_for_postgres_major`、`create_name`、`preload_name`/是否需要preload、library/control/SQL paths、skip reason；
- Rust/cargo-pgrx and other material toolchain versions；
- TigerFS tag/digest/platform；
- generation timestamp仅作审计信息，不参与内容 identity。

CI cache epoch 是 `.github/workflows/build-and-test.yml` 中独立、tracked 的 cache-busting 常量，不写入 runtime metadata。metadata 记录可重现 artifact 的 bundle recipe/source identity；content stamp 由同一 bundle identity 驱动。这样避免 CI cache epoch 与 bundle schema/recipe epoch 形成两套需要同步的身份源。

生成器必须：

1. 无 shell 地运行 installed `postgres --version`、`pg_config --version`；
2. 确认两者 major 均为 18；
3. 枚举 expected artifacts，确认 built extension 的 library/control/install SQL存在；
4. 确认 skipped extension 不残留旧 library/control/SQL；
5. 对 platform-specific skip 写明原因；
6. TigerFS被请求时确认 executable存在、可执行且version与metadata pin一致；
7. 先写临时文件、fsync/close 后 atomic rename；失败不留下“完成”JSON；
8. 阻断 `make all` 与 wheel build，而不是只 warning。

`MANIFEST.in:1` 已 graft 整棵 `pginstall`，因此无需新增平行 package-data 机制，只需 wheel test证明 metadata 已包含。

### 5.6 Runtime metadata API

新增 `src/pgembed/_bundle_metadata.py`：

- 定义 immutable records 和 schema validation；
- fail closed 读取 packaged JSON；
- 不执行磁盘 mutation；
- 提供内部 `load_bundle_metadata()` / `require_bundle_metadata()` 和可测试、可清除的 process-local cache。

`src/pgembed/__init__.py` re-export read-only：

- `BUNDLED_PG_MAJOR: int | None`
- `BUNDLED_POSTGRES_VERSION: str | None`

`None` 只表示尚未执行 native build 的 editable/source tree。import 时只读取 metadata，不 fork `postgres` 或其他 subprocess；metadata 缺失时所有 native extension availability 为 false，首次 `get_server()` 抛 structured metadata/binary-unavailable error。installed wheel 中 metadata 缺失、损坏，或后续 binary validation 与 metadata 不一致时一律 fail closed，不把 extension 标为 available，也不触碰 PGDATA。binary validation 延迟到首次 `get_server()`，成功结果按进程缓存，测试可清除或 monkeypatch。

保持现有 public extension functions/signatures。

### 5.7 Major-aware extension availability

修改 `_detect_extensions()` 的判定规则；这是对 `src/pgembed/__init__.py:117-122` 当前 package-local-first 行为的有意收紧：

- 优先选择 bundle 内由 metadata attestation 的 artifact；只有 metadata 声明 `built: true`、`built_for_postgres_major == BUNDLED_PG_MAJOR`、library 存在且 control/install SQL完整时才 available。
- metadata 声明 skipped 时，若磁盘仍有对应 artifact，视为 bundle corruption，而不是 available。
- package-local `pgembed_pgvector` / `pgembed_pgduckdb` 只有在 package 提供 `built_for_postgres_major=18` attestation 时才可覆盖 bundled artifact。
- 旧 standalone package 没有 major metadata时 fail closed并提供 warning；不能凭 Python package version推断 ABI。
- 如果继续发布 standalone extension wheels，必须同步发布 PG18-compatible package version和 dependency range，防止 PG17 extension package装到 PG18 base。
- 保持 `get_extension_path(name)` 的 public 签名；磁盘 path 存在不等于 `has_extension(name)` 为 true，后者必须通过 major/metadata完整性判定。
- `create_extension()` unavailable error保持原调用形状，但增加原因：metadata缺失、major不一致、library缺失或 SQL artifact不完整。

### 5.8 Structured error model

新增 `src/pgembed/errors.py` 并从 `pgembed.__init__` re-export：

- `PgEmbedError(RuntimeError)`
- `BundledPostgresMetadataError(PgEmbedError)`
- `PostgresDataDirectoryInspectionError(PgEmbedError)`
- `PostgresDataDirectoryVersionError(PgEmbedError)`
- `PostgresStartupError(PgEmbedError)`
- `PostgresStartupTimeoutError(PostgresStartupError, TimeoutError)`

`PostgresDataDirectoryVersionError` 提供稳定字段：

- `pgdata: Path`
- `found_major: int`
- `expected_major: int`
- `pg_version_text: str`
- `migration_documentation: str`

`PostgresStartupError` 提供 `pgdata`、`log_path`、capped `log_tail`、`postmaster_status`；timeout subtype 增加 `timeout_seconds`。底层 `CalledProcessError` / `TimeoutExpired` 通过 exception chaining 保留，不要求调用者解析 message。

该 structured hierarchy 只覆盖 bundle metadata、PGDATA inspection/version 和 server startup lifecycle。`psql()` / `create_extension()` 的 catalog/SQL失败本次继续暴露现有 `subprocess.CalledProcessError`；不在本次重构 `shell=True` 调用，也不新增 catalog error hierarchy。integration tests仍必须捕获并报告 stderr与server log context。

### 5.9 Bundled binary validation

首次 `get_server()` 实际创建 server 前（import-time 不 fork subprocess）：

1. 读取 bundle metadata；
2. 无 shell 地运行 bundled `postgres --version`，使用短 timeout；
3. 解析 binary major；
4. 比较 binary major、metadata major 和 `BUNDLED_PG_MAJOR`；
5. 任意不一致抛 `BundledPostgresMetadataError`；
6. 此前不读取、创建或修改 PGDATA。

成功结果可按进程缓存；测试必须可 monkeypatch/清除缓存。

### 5.10 PGDATA detection algorithm

将 `ensure_pgdata_inited()` 重排为。先完成 bundle metadata/binary validation，再只读检查 `PG_VERSION`；两者都成功前，禁止 `ensure_user_exists`、`ensure_prefix_permissions`、`ensure_folder_permissions`、`chmod`/`chown`、创建 `DiskList`/`.handle_pids.json`、写 `_instances`、注册 `atexit`、修改 config、启动或停止 server。root 路径也遵守同一顺序，不能为了准备运行用户或权限先修改 PGDATA。

1. **`PG_VERSION` 存在：**
   - 严格读取并保留原始文本；
   - 解析第一个 numeric component 为 major（`17`→17，`9.6`→9）；
   - 不可读、为空或格式无效时抛 inspection error；
   - major 不等于 bundled 18 时抛 version error；
   - 在此之前不调用 `initdb`、preload config、`pg_ctl`，不扫描/停止同路径 process，也不创建 `.handle_pids.json` 记录。
2. **`PG_VERSION` 不存在：**
   - 目录为空才允许视为 fresh target；
   - 非空目录 fail closed，抛 inspection error；
   - 只有 fresh target 才执行既有权限准备、stale-process 安全逻辑和 bundled PG18 `initdb`；
   - initdb 后重读 `PG_VERSION` 并断言 major 18。
3. **通过检测后：**才允许 preload 配置与启动。

特殊行为：

- 仍在运行的 PG17 server使用该目录时，只读版本后报错，不停止旧 server。
- PG18 PGDATA已有 running server时，继续复用当前 `PostmasterInfo` 路径。
- mismatch 重复调用每次产生相同错误且磁盘不变。
- PG18 wheel不会把未知非空目录自动重初始化。

### 5.11 Constructor transaction and rollback

重构初始化生命周期而不改变 constructor签名。当前顺序是 `atexit.register(self._cleanup)` → `_instances[self.pgdata] = self` → init/start，因此异常会留下可复用的坏对象；新流程必须把这些动作纳入一笔显式 transaction：

- bundle validation与PG_VERSION major compare先完成，随后才允许root权限准备、`DiskList`、注册和启动副作用。
- 最晚在全部初始化成功后才把 instance视为 `_instances` 中可复用对象。
- 若因现有结构暂时提前注册，任何 failure cleanup 都在 `_lock` 持有期间执行 `PostgresServer._instances.pop(pgdata, None)`，不依赖 `DiskList` refcount决定是否移除。
- 在成功启动并加入 `DiskList` 后才保留 atexit registration；失败时显式 `atexit.unregister(self._cleanup)`，并使进程退出时不会二次 cleanup 或触发 `KeyError`。
- major mismatch不创建 `.handle_pids.json` 条目。
- startup early exit/timeout/cancellation时 best-effort回收可能启动的 postmaster，清除 `_instances`、atexit、当前进程 PID entry和 process handle状态。
- failed startup即使 `cleanup_mode="delete"` 也不删除 PGDATA或log，以保留诊断证据。
- 测试必须证明 constructor失败后再次 `get_server(same_pgdata)` 不会返回partial object，且解释器退出不会重复清理。

### 5.12 Bounded startup, readiness and cleanup

保留 public signatures，新增内部 constants：

- `PG_CTL_START_TIMEOUT_SECONDS`：约 10 秒，保持现有语义。
- `POSTMASTER_READY_TIMEOUT_SECONDS`：明确上限，初始建议 30 秒。
- `FAILED_STARTUP_CLEANUP_TIMEOUT_SECONDS`：独立短上限。

启动流程：

1. 使用 `time.monotonic()` 计算统一 deadline。
2. 调用 `pg_ctl -w start`。
3. `CalledProcessError` 时读取 capped log tail（例如 ≤64 KiB且≤200行），抛 `PostgresStartupError`。
4. `TimeoutExpired` 时读取 `postmaster.pid`，best-effort stop/terminate/kill，抛 timeout subtype。
5. readiness loop容忍 `postmaster.pid` 写入中的短暂 parse failure；ready+running成功，已知 PID早退则立即失败，deadline到期则回收并报 timeout。
6. readiness 被 `KeyboardInterrupt` 或其他异常打断时，走与 timeout 相同的 best-effort postmaster 回收和 constructor rollback；保留原异常，或用明确的 exception chaining 包装，禁止吞掉取消信号。
7. `_cleanup()` 当前已有 process `terminate → wait(2) → kill` fallback；只需给无 subprocess timeout 的 `pg_ctl -w stop` 调用增加上限，并保留现有 bounded 回收链，不重写整段 cleanup。
8. 异常不嵌入无限增长的完整 log。

preload missing library、PG17 `.so`、undefined symbol等启动期错误通过 PostgreSQL log + structured startup error暴露。catalog/SQL API错误保持现有 `CalledProcessError` 边界并附带stderr/log测试上下文。修复 bundle/config后可重试；PGDATA与log默认保留。

## 6. Extension compatibility and runtime acceptance

### 6.1 Final extension matrix

已选择 compatibility-only：AGE 与 pg_cron 做 PG18 必需升级，其他扩展保持当前版本；psql_bm25s 冻结已验证 commit，VectorChord 固定 toolchain。下列行为门禁对全部组件生效：

| 组件 | PG18 plan | Runtime acceptance |
|---|---|---|
| pgvector | 保持 `v0.8.2` | `CREATE EXTENSION vector`；vector type；HNSW/IVFFlat创建；至少一个 index-backed `EXPLAIN` |
| pg_duckdb | 保持 `v1.1.1` | preload；catalog；`duckdb.time_bucket`；与 Timescale ordering/planner/restart |
| VectorChord | 保持 `1.1.1`，固定 exact toolchain | preload `vchord`；依赖 vector；`vchordg`/`vchordrq`真实 Index Scan；musl排除规则 |
| AGE | `release/PG18/1.8.0` | `CREATE EXTENSION age`；`agtype`；create graph/openCypher；不默认 preload |
| psql_bm25s | 冻结已验证 probe commit | access method/result type；index build；真实 BM25 query与顺序；ICU linkage；不默认 preload |
| TimescaleDB | 保持 `2.27.1` | preload；hypertable；insert/query/time bucket；restart；default index catalog |
| pg_cron | `v1.6.7` | preload；extension catalog；schedule/unschedule smoke；background worker restart |
| pg_net | 保持 `v0.20.5` | preload；libcurl load；catalog smoke；不发公网请求；restart |
| TigerFS | 默认保持 `v0.7.0` | binary/version；native uuidv7；file-first history/savepoint/undo；cleanup order |

release glibc/macOS builds期望完整 extension set。musl继续排除 VectorChord；缺 libcurl的非 release host可跳过 pg_net，但 metadata必须写 skip reason且确认无 stale artifact。

### 6.2 Unified preload fixture

在 `tests/test_pgembed.py` 复用 `_require_extension()` 并增加 metadata-driven全扩展 fixture。full release bundle预期 preload顺序：

`vchord,timescaledb,pg_duckdb,pg_cron,pg_net`

- 只请求 metadata 中 `built: true` 且声明需要 preload的组件。
- `ensure_shared_preload_libraries()` 继续去重并保留已有配置。
- 通过 `SHOW shared_preload_libraries` 解析集合和顺序，不依赖空格格式。
- AGE和psql_bm25s不加入默认 preload。

### 6.3 Catalog and index acceptance

按依赖顺序创建：

1. `vector`
2. `vchord`
3. `age`
4. `psql_bm25s`
5. `timescaledb`
6. `pg_duckdb`
7. `pg_cron`
8. `pg_net`

验证：

- `pg_extension.extname/extversion` 与 metadata expected set一致。
- `pg_available_extensions` 包含 control metadata。
- `pg_am` 包含 `hnsw`、`ivfflat`、`vchordg`、`vchordrq`、`psql_bm25s`。
- extension-owned object/library不引用 PG17 build tree。
- `server_version_num` 属于 PG18。
- `pg_config --pkglibdir` 不含 metadata未声明的 stale extension library。
- pgvector HNSW/IVFFlat、VectorChord两种 AM、psql_bm25s真实 query和Timescale hypertable均验证功能，而非只检查创建成功。

### 6.4 Restart acceptance

同一 PG18 PGDATA：

1. 用完整 preload set启动；
2. 创建 extensions和测试对象；
3. `cleanup_mode="stop"` 正常退出；
4. 用相同 preload set重新打开；
5. 断言 config中 preload不重复、catalog仍在、vector/BM25/Timescale/AGE查询仍工作、pg_cron/pg_net workers不阻止启动，并证明 postmaster确实重启。

### 6.5 TimescaleDB ↔ pg_duckdb regression

保持 `EXTENSION_PRECEDENCE` 不变，直到测试证明 upstream冲突已消失：

- `create_extension("pg_duckdb")` 在 TimescaleDB available时先创建 TimescaleDB。
- 最终同时存在 TimescaleDB-owned `public.time_bucket`、`duckdb.time_bucket` fallback和两个 extension rows。
- raw SQL反向顺序负测试：先 pg_duckdb、后 TimescaleDB；记录当前 duplicate-function结果，以检测 upstream行为变化。
- planner crash probe在独立 PGDATA/测试进程执行 investigation和 upstream issue的最小复现 SQL。
- query failure可记录为已知 limitation；postmaster崩溃、连接断开、PID消失或 probe后 `SELECT 1` 失败必须阻断发布。
- compatibility-only 保持当前 precedence；未来单独升级扩展时，只有测试证明冲突已消失后才能修改该规则。
- 上游证据固定为 pg_duckdb planner issues #845/#963，以及已合入当前版本的 collision guard PR #747；实现与发布审查以 repository investigation 为入口，不把基本 smoke 未复现误写成风险已消失。

## 7. TigerFS and PostgreSQL 18 UUIDv7

### 7.1 Fresh PG18

fresh cluster不再创建 PG17-era `public.uuidv7()` compatibility shim。验证：

- `to_regprocedure('pg_catalog.uuidv7()')` 非空。
- `to_regprocedure('public.uuidv7()')` 为空。
- 在 TigerFS使用的 role/search_path下，unqualified `uuidv7()` 成功。
- 结果 UUID version nibble为7。
- TimescaleDB启用前后解析和结果一致。

`docs/tigerfs.md` 中 PG17.10 + `generate_uuidv7()` shim说明改为历史迁移分支，不能继续指导fresh PG18用户创建 shim。

### 7.2 Migrated PG17 database

迁移前 inventory：

- `pg_catalog.uuidv7()`
- `public.uuidv7()`
- `public.generate_uuidv7()`

旧 `public.uuidv7()` 只有在 owner、language和definition精确匹配 pgembed文档曾建议的 wrapper（调用 `public.generate_uuidv7()`）时，迁移流程才可建议删除。未知实现不得自动覆盖或删除；保留时必须测试实际 function resolution，并在 report中标为“保留、删除或人工审查”。

### 7.3 TigerFS functional gate

- 启动 PG18 + TimescaleDB，不创建 compatibility shim。
- 使用 bundled TigerFS v0.7.0 挂载最小 file-first workspace。
- 触发 history/savepoint/undo，并断言无 uuidv7 undefined/ambiguous。
- SQL确认生成 UUID为version 7。
- cleanup顺序保持 `unmount → reclaim daemon → stop PostgreSQL`。
- 普通 manylinux container只跑 SQL resolution与binary tests；真实 mount在 macOS arm64和显式配置 `/dev/fuse` 的 Linux runner执行。
- fixture的精确 CLI/path通过 bundled `tigerfs --help` 和已有文档固化，不形成新的产品设计决策。

## 8. Confirmed decisions

2026-08-07 的中途确认已收敛四个会改变实现范围的决策。

### D1 — PG17 PGDATA: fail-fast + external migration documentation

**选择：** 只提供结构化 fail-fast 和外部迁移文档。

- PG18 wheel 遇到 PG17 PGDATA 时只抛 `PostgresDataDirectoryVersionError`；不启动旧 cluster、不复制数据、不自动迁移。
- `docs/migrations/postgresql-17-to-18.md` 说明外部 dump/restore、外部 `pg_upgrade` 和 logical migration，并明确这些流程由用户或外部工具执行。
- pgembed 不承诺在没有 PG17 binary 时仅凭旧 PGDATA 自动迁移，也不新增 `migration.py` 或 public migration API。
- rollback 边界是：原 PG17 PGDATA 未被修改时可回退到 PG17 wheel；任何新建或外部迁移后的 PG18 PGDATA 都不能由 PG17 直接读取。

**未选方案：** dump/restore helper会引入roles、多数据库、large objects、权限、磁盘和中断恢复的长期 public API支持面；双 binaries + `pg_upgrade` 还会近乎翻倍wheel、CI、安全更新和两套extension兼容矩阵。两者均超出本次major compatibility升级所需的最小安全边界。

### D2 — PostgreSQL source: exact tag and commit

**选择：** 首个 PG18 candidate 固定 PostgreSQL 18.4：

- tag：`REL_18_4`
- commit：`f5cc81719e6da4cbdb1f797c48b693e91018153a`
- PostgreSQL 18.4 官方发布日期：2026-05-14。

release recipe必须校验tag解析到该commit，并在metadata中同时记录两者。后续point/security升级必须显式改pin、提升bundle/cache identity并重跑完整gates。

**未选方案：** `REL_18_STABLE` 自动吸收修复，但同一ref会随时间变化，只能重现“当次记录的commit”，不能从branch label重建同一artifact，因此不用于正式release source identity。

### D3 — Extensions: compatibility-only

**选择：** 只做PG18兼容性所需升级：

- AGE：`release/PG18/1.8.0`
- pg_cron：`v1.6.7`
- pgvector `v0.8.2`、pg_duckdb `v1.1.1`、VectorChord `1.1.1`、TimescaleDB `2.27.1`、pg_net `v0.20.5`、TigerFS `v0.7.0` 保持。
- psql_bm25s 从 mutable `main` 冻结为已通过PG18 probe的exact commit。
- VectorChord Rust toolchain和cargo-pgrx固定为已验证版本。

**未选方案：** 全量刷新可能获得upstream fixes，但会把PG major、SQL API、planner/index行为、native dependencies和TigerFS变化合并进同一次release，显著削弱回归归因与rollback能力。已知pg_duckdb/Timescale风险通过专门runtime gate管理，而不靠无证据刷新全部组件。

### D4 — Release: pre-release/RC first

**选择：** PG18 首先进入opt-in pre-release/RC channel，PG17 stable说明在观察期内保留。

- pre-release本身必须通过Gate 0–5；它不是降低质量门槛的测试artifact。
- 观察期收集真实PGDATA boundary、extension、动态依赖和平台反馈。
- 切换stable default前必须有独立确认，并从clean workspace再次运行完整Gate 0–5。
- README、package version和publish workflow必须让用户明确区分PG17 stable与PG18 candidate。

**未选方案：** 直接默认PG18会让所有普通升级用户立即遇到PG17 PGDATA breaking boundary，缺少真实迁移与平台反馈窗口；在本次首次major切换中不接受该blast radius。

## 9. CI and release gates

### Gate 0 — static and build-rule tests

无需编译 PostgreSQL：

- `tests/test_postgres_build.py`：通过可覆盖的 `POSTGRES_BUNDLE_CONFIG_STAMP`/`INSTALL_PREFIX` 和 fake-prefix/dummy-`postgres` fixture，独立执行 stamp target；覆盖 initial invalidation、idempotent mtime、major/ref/configure/extension set变化、A→B→A、adversarial mtimes、partial prefix，不编译真实 PostgreSQL。
- macOS job 增加 Darwin-only GNU Make 3.81 case：`@pytest.mark.skipif(platform.system() != "Darwin")`，先断言 `make --version`，再运行同一 stamp target 行为测试。
- `tests/test_bundle_metadata.py`：schema、malformed/missing metadata、binary-major mismatch、extension-major mismatch、skipped stale artifact、atomic failure。
- `tests/test_commands.py`：TigerFS仍不生成wrapper，`POSTGRES_BIN_PATH`不变。
- `tests/test_tigerfs_build.py`：保留TigerFS stamp、matrix、deployment target和GNU Make 3.81断言。
- fake `PG_VERSION` tests：wrong major、invalid text、nonempty/no-version；不启动server。
- CI校验 `CIBW_BUILD` 精确包含 cp312/cp313/cp314 且不再生成 cp310/cp311；cp314 smoke 继续保留。

### Gate 1 — per-platform clean native build

目标：macOS arm64（deployment target 26.0）、Linux x86_64、Linux aarch64。

- release/tag build不得信任PG17 cache；使用全新workspace/prefix或`clean-all`等价边界。
- 构建完整matrix extension set。
- 生成并验证metadata。
- 执行binary/library dependency audit。
- 归档source commits、digests、toolchain、extension versions、configure identity。
- musl不是release matrix，仅通过规则测试验证VectorChord exclusion。

### Gate 2 — installed-wheel smoke

`cibuildwheel_test.bash` 在安装后的wheel中验证：

- `postgres --version`、`pg_config --version`和major一致。
- `BUNDLED_PG_MAJOR == 18`、metadata存在。
- TigerFS executable/version。
- command wrapper exclusion。
- wrong-major PG_VERSION fail-fast路径。
- wheel内没有明显PG17 source/build metadata或未声明artifact。
- Linux普通container不要求FUSE mount。

### Gate 3 — runnable PG18 + extensions integration

在对应native runner安装Gate 2 wheel：

- fresh server lifecycle。
- full extension catalog、preload、indexes、restart。
- TimescaleDB/pg_duckdb order和isolated planner survival。
- invalid preload产生bounded structured error。
- TigerFS native UUIDv7 SQL semantics。
- 三种release architectures全部通过；不能用单机probe替代。

### Gate 4 — migration boundary

fail-fast 产品承诺必须验证：

- PG17 `PG_VERSION`被PG18 wheel fail-fast。
- 无 `ensure_user_exists`、无system-user创建、无permission fix、无`chmod`/`chown`、无`pg_ctl start`、无config mutation、无`.handle_pids.json`、无`_instances`/atexit残留、无旧server stop。
- directory内容、ownership和mode保持不变。
- error含expected/found major稳定字段。


### Gate 5 — real TigerFS mount

- macOS arm64必跑。
- Linux仅在专用`/dev/fuse` runner必跑。
- manylinux wheel smoke不因缺FUSE失败。
- 验证UUIDv7、savepoint/undo、unmount/daemon/server cleanup。

### Gate 6 — publish

- `publish-to-pypi` 必须依赖所有适用gates，而不是只依赖`build_wheels`。
- 只下载和发布这些gates实际验证过的artifact。
- 首轮只允许发布 PG18 pre-release/RC；默认 stable 切换需要观察期结束后的独立确认与一次完整 Gate 0–5 重跑。
- release workflow记录wheel hashes、metadata、source lock表和测试报告。

## 10. Cache strategy

在 workflow 中新增 tracked cache epoch，例如 `PG_BUILD_CACHE_EPOCH=pg18-bundle-v1`。它只控制 CI cache namespace，不进入 `build-metadata.json`；runtime bundle identity由source/recipe metadata定义，stamp由该bundle identity驱动。cache key至少包含：

- epoch；
- OS/architecture；
- extension profile；
- workflow hash；
- tracked build recipe hash。

规则：

1. PG17→PG18必须提升epoch。
2. content stamp是restore stale cache后的correctness boundary。
3. release/tag build不以cache为可信输入。
4. cache不定义source identity。
5. extension set缩减时删除whole prefix，避免旧`.so`被graft。
6. metadata generation和tests成功后才save cache。
7. 不以解除`pgbuild/` ignore作为correctness方案；无论cache hash是否覆盖所有untracked生成物，stamp和clean release gate都必须独立成立。

## 11. File-by-file impact

### 11.1 Unconditional changes

#### `pgbuild/Makefile`

- PostgreSQL `REL_18_4`/exact commit、AGE PG18 branch、pg_cron v1.6.7和其余已确认compatibility-only pins。
- exact source/digest/toolchain locks。
- `POSTGRES_BUNDLE_CONFIG_STAMP`、whole-prefix invalidation、metadata target、artifact verification。
- 保持GNU Make 3.81和现有extension selection规则。

#### `tools/generate_bundle_metadata.py` — new

- 验证binary/config/artifact/source identity并原子生成schema v1 JSON。

#### `src/pgembed/_bundle_metadata.py` — new

- immutable schema records、fail-closed loader和可测试cache。

#### `src/pgembed/errors.py` — new

- metadata、PGDATA和startup structured errors。

#### `src/pgembed/__init__.py`

- re-export errors/major constants。
- metadata-aware extension detection和package-local major attestation。
- 保持registry、precedence和public signatures。

#### `src/pgembed/postgres_server.py`

- binary/metadata validation、PG_VERSION检测、constructor rollback、bounded startup/cleanup和structured errors。

#### `tests/test_postgres_build.py` — new

- PostgreSQL bundle stamp行为，不编译PG。

#### `tests/test_bundle_metadata.py` — new

- generator/loader/corruption/atomicity测试。

#### `tests/test_pgembed.py`

- PGDATA、startup、catalog、index、restart、Timescale/pg_duckdb和worker tests。

#### `tests/test_bundled_tools.py`

- postgres/pg_config/metadata major smoke，保留TigerFS tests。

#### `tests/test_tigerfs_pg18.py` — new

- native UUIDv7、legacy shim inventory和marked mount integration。

#### `.github/workflows/build-and-test.yml`

- 独立tracked cache epoch、clean release、toolchain lock、cp312/cp313/cp314 wheel matrix、metadata/audit artifacts。
- 停止生成不符合 `requires-python >=3.12` 的 cp310/cp311 artifacts，保留cp314。
- 拆分build、wheel smoke、runtime、migration、TigerFS和publish jobs。
- 保持三种architecture、macOS target 26.0、无Windows/universal2。

#### `cibuildwheel_test.bash`

- 增加PG18 binary/metadata/fail-fast smoke；Linux不要求FUSE。

#### `pyproject.toml`

- 注册`integration`、`tigerfs_mount` markers。
- 保持`requires-python >=3.12`；版本采用明确的PG18 pre-release/RC标识。

#### `README.md`

- PG18 badge、PG17 PGDATA fail-fast warning、bundled major API、extension/preload表和pre-release channel说明。
- 记录wheel matrix为cp312/cp313/cp314，并说明cp310/cp311停止生成是与既有`requires-python >=3.12`声明对齐。

#### `docs/tigerfs.md`

- fresh PG18 native uuidv7、legacy shim inventory、tested versions和savepoint/undo acceptance。

#### `docs/migrations/postgresql-17-to-18.md` — new

- PG_VERSION fail-fast、backup、extension/collation/checksum/UUID shim inventory、外部迁移选项和rollback。

### 11.2 Standalone extension package compatibility

#### `src/pgembed_pgvector/__init__.py`, `src/pgembed_pgduckdb/__init__.py`

- 当前 runtime 会优先探测 package-local native artifact，因此这些包必须提供 `built_for_postgres_major=18` metadata；缺失时 PG18 base fail closed。

#### `src/pgembed_pgvector/pyproject.toml`, `src/pgembed_pgduckdb/pyproject.toml`

- 若现有 standalone release path 继续发布，更新 package version 和 `pgembed` dependency range，禁止 PG17 artifact 覆盖 PG18 base；若不发布，也必须测试旧 package 安装时的 fail-closed 行为。

### 11.3 Explicitly unchanged, regression-tested

- `src/pgembed/_commands.py`：保留TigerFS exclusion、动态PostgreSQL command wrappers和`POSTGRES_BIN_PATH`。
- `MANIFEST.in`：继续graft `src/pgembed/pginstall`。
- `setup.py`、`src/pgembed/_build.py`：保留dummy CFFI hook。
- top-level `Makefile`：保留单一build入口。
- `EXTENSION_PRECEDENCE`：除非PG18 runtime测试证明冲突已消失，否则不改。

## 12. Execution index

### WI-1 — Freeze PG17 baseline

- **Goal:** 建立升级前可对比的wheel、configure、extension和runtime基线。
- **Done when:** 当前tests、artifact list、extension versions、preload和`pg_config --configure`归档。
- **Key files:** `tests/test_pgembed.py`, `tests/test_commands.py`, `tests/test_tigerfs_build.py`。
- **Dependencies:** none。
- **Size:** S。

### WI-2 — Lock PostgreSQL 18.4 source

- **Goal:** 将 release source identity 固定到 `REL_18_4` 与 commit `f5cc81719e6da4cbdb1f797c48b693e91018153a`。
- **Done when:** Makefile在clone后验证exact commit，metadata和artifact报告同一identity。
- **Key files:** `pgbuild/Makefile`, `.github/workflows/build-and-test.yml`。
- **Dependencies:** WI-1。
- **Size:** S。

### WI-3 — Add bundle stamp and source verification

- **Goal:** 让任意ABI-relevant配置变化失效whole prefix。
- **Done when:** fake-prefix stamp tests覆盖idempotence、A→B→A、stale/partial cache，首次PG18 core clean build成功。
- **Key files:** `pgbuild/Makefile`, `tests/test_postgres_build.py`。
- **Dependencies:** WI-2。
- **Size:** L。

### WI-4 — Generate and package build metadata

- **Goal:** 给wheel提供可审计的binary/source/extension身份。
- **Done when:** schema v1原子生成、corruption tests通过、wheel包含JSON。
- **Key files:** `tools/generate_bundle_metadata.py`, `src/pgembed/_bundle_metadata.py`, `tests/test_bundle_metadata.py`, `MANIFEST.in`。
- **Dependencies:** WI-3。
- **Size:** L。

### WI-5 — Implement runtime bundle and PGDATA fail-fast

- **Goal:** 在PGDATA mutation之前验证bundle和cluster major。
- **Done when:** wrong/malformed/nonempty-no-version全部结构化失败，无system-user、ownership/mode、start/config/pid/instance/atexit副作用。
- **Key files:** `src/pgembed/errors.py`, `src/pgembed/__init__.py`, `src/pgembed/postgres_server.py`, `tests/test_pgembed.py`。
- **Dependencies:** WI-4。
- **Size:** L。

### WI-6 — Bound startup and cleanup lifecycle

- **Goal:** 消除readiness与cleanup无界等待，保证constructor rollback。
- **Done when:** early exit、timeout、`KeyboardInterrupt`、invalid preload在固定时间内回收partial postmaster，产生或保留正确异常且无坏instance/atexit/PID残留；`pg_ctl -w stop`有subprocess timeout并保留现有terminate/kill fallback。
- **Key files:** `src/pgembed/postgres_server.py`, `tests/test_pgembed.py`。
- **Dependencies:** WI-5。
- **Size:** M。

### WI-7 — Lock compatibility-only extension set

- **Goal:** 固化已确认的最小PG18 extension source matrix。
- **Done when:** AGE/pg_cron使用必需PG18版本，其他组件保持当前版本，psql_bm25s commit和VectorChord toolchain精确锁定。
- **Key files:** `pgbuild/Makefile`, build metadata source-lock table。
- **Dependencies:** PG18 core clean build。
- **Size:** M。

### WI-8 — Complete PG18 extension recipes

- **Goal:** 在所有目标平台构建完整PG18-compatible native payload。
- **Done when:** AGE/pg_cron必需升级完成，其余按compatibility-only locks构建；每项clean build和单项runtime smoke通过，无silent skip。
- **Key files:** `pgbuild/Makefile`, `.github/workflows/build-and-test.yml`, metadata generator。
- **Dependencies:** WI-3, WI-4, WI-7。
- **Size:** XL。

### WI-9 — Add catalog/index/preload/restart integration

- **Goal:** 证明完整bundle可运行而不仅是可编译。
- **Done when:** extension catalog、AM/index、preload顺序、restart和workers通过。
- **Key files:** `tests/test_pgembed.py`, `tests/test_bundled_tools.py`。
- **Dependencies:** WI-6, WI-8。
- **Size:** XL。

### WI-10 — Close TimescaleDB/pg_duckdb regression

- **Goal:** 保持创建顺序并阻断planner/postmaster crash。
- **Done when:**正向顺序、反向负测试、isolated planner survival和post-probe health check通过。
- **Key files:** `src/pgembed/__init__.py`, `src/pgembed/postgres_server.py`, `tests/test_pgembed.py`, investigation doc。
- **Dependencies:** WI-9。
- **Size:** M。

### WI-11 — Validate TigerFS on PG18

- **Goal:** 移除fresh shim依赖并验证native uuidv7及mount工作流。
- **Done when:** SQL semantics、legacy inventory、macOS mount和可用Linux FUSE gate通过。
- **Key files:** `tests/test_tigerfs_pg18.py`, `docs/tigerfs.md`。
- **Dependencies:** WI-8, WI-9。
- **Size:** L。

### WI-12 — Build CI Gates 0–3

- **Goal:** 将static、clean build、wheel smoke和native integration分离。
- **Done when:** 三architecture无cache候选artifact完成metadata和dependency audit，wheel matrix精确为cp312/cp313/cp314，README/release notes记录cp310/cp311停止生成。
- **Key files:** `.github/workflows/build-and-test.yml`, `cibuildwheel_test.bash`, `pyproject.toml`。
- **Dependencies:** WI-3 through WI-11。
- **Size:** XL。

### WI-13 — Deliver fail-fast migration boundary and guide

- **Goal:** 交付明确且可测试的PG17→PG18外部迁移边界。
- **Done when:** PG17 PGDATA无mutation fail-fast；migration guide覆盖backup、inventory、外部dump/restore、外部pg_upgrade、logical migration和rollback。
- **Key files:** `docs/migrations/postgresql-17-to-18.md`, `tests/test_pgembed.py`。
- **Dependencies:** WI-5, WI-12。
- **Size:** M。

### WI-14 — Complete migration and rollback gate

- **Goal:** 证明PG17 boundary、interruption和rollback承诺。
- **Done when:** Gate 4证明PG17 source directory的内容/ownership/mode、system-user状态、config、running process、instance registry与atexit均未被PG18启动路径修改；文档中的external migration preflight可人工执行。
- **Key files:** `tests/test_pgembed.py`, `docs/migrations/postgresql-17-to-18.md`。
- **Dependencies:** WI-13。
- **Size:** M–XL。

### WI-15 — Update public docs and package compatibility

- **Goal:** 让用户在安装前知道major、PGDATA和extension/channel边界。
- **Done when:** README、migration、TigerFS和standalone package metadata与fail-fast、compatibility-only和pre-release决策一致。
- **Key files:** `README.md`, `docs/tigerfs.md`, `docs/migrations/postgresql-17-to-18.md`, standalone extension package files。
- **Dependencies:** WI-11, WI-14。
- **Size:** M。

### WI-16 — Publish the PG18 pre-release candidate

- **Goal:** 只把完整验证的PG18 artifacts发布到明确的opt-in channel。
- **Done when:** Gate 0–5从clean workspace通过；publish依赖完整；pre-release version/channel生效；metadata、commits、digests、wheel hashes和reports归档。
- **Key files:** `.github/workflows/build-and-test.yml`, `pyproject.toml`, release docs。
- **Dependencies:** WI-12 through WI-15。
- **Size:** L。

## 13. Dependency-ordered implementation sequence

1. 冻结PG17 baseline。
2. 锁定 PostgreSQL `REL_18_4` 和 exact commit。
3. 原子实现bundle stamp + source verification +首次PG18 core clean build。
4. 实现metadata生成、runtime loader与wheel inclusion。
5. 实现bundle binary验证、PGDATA fail-fast和constructor rollback。
6. 实现bounded startup/readiness/cleanup。
7. 固化compatibility-only extension source matrix。
8. 先迁AGE→PG18 branch、pg_cron→v1.6.7，再按既定pins逐项完成其他extension recipe。
9. 完成catalog/index/preload/restart与TimescaleDB↔pg_duckdb tests。
10. 完成TigerFS native UUIDv7 SQL和mount gates。
11. 三平台完成Gate 0–3，形成candidate compatibility matrix。
12. 交付fail-fast migration guide，不新增migration runtime API。
13. 完成Gate 4与rollback evidence。
14. 更新README、migration guide、TigerFS和standalone package兼容声明。
15. 配置PG18 opt-in pre-release/RC channel。
16. 从无cache workspace运行Gate 0–5，归档证据后才允许Gate 6 publish。

## 14. Test inventory and commands

### 14.1 Required tests

`tests/test_pgembed.py`：

- `test_pgdata_major_mismatch_fails_before_start`
- `test_invalid_pg_version_fails_closed`
- `test_nonempty_directory_without_pg_version_fails_closed`
- `test_fresh_init_reports_bundled_major`
- `test_startup_readiness_timeout_is_bounded`
- `test_startup_keyboard_interrupt_rolls_back_and_reaps_postmaster`
- `test_constructor_failure_allows_clean_retry_same_pgdata`
- `test_constructor_failure_unregisters_atexit_cleanup`
- `test_missing_preload_library_returns_structured_startup_error`
- `test_all_preload_extensions_restart`
- `test_pg_duckdb_creates_timescaledb_first_on_pg18`
- `test_pg_duckdb_timescaledb_planner_probe_survives`
- pgvector index、pg_cron/pg_net catalog smoke和constructor rollback assertions。

`tests/test_bundled_tools.py`：

- `test_bundled_postgres_major_matches_metadata`
- `test_pg_config_major_matches_postgres`
- existing TigerFS tests。

`tests/test_postgres_build.py`：使用可覆盖 stamp/prefix 与 fake-prefix fixture覆盖 invalidation/idempotence/A→B→A/adversarial mtime/partial prefix；Darwin-only case 在真实 GNU Make 3.81 下运行同一 target。
`tests/test_bundle_metadata.py`：schema、major mismatch、stale artifact、atomic generation。  
`tests/test_tigerfs_pg18.py`：native UUIDv7、migrated shim inventory、marked mount。  
本次不新增 `tests/test_migration.py`；真实数据迁移由外部工具负责，pgembed测试只证明fail-fast边界和文档preflight。

### 14.2 Validation commands

Cold build：

```bash
make clean
make EXTENSIONS="pgvector vectorchord pg_duckdb age psql_bm25s timescaledb pg_cron pg_net tigerfs" build
```

Binary and metadata：

```bash
src/pgembed/pginstall/bin/postgres --version
src/pgembed/pginstall/bin/pg_config --version
src/pgembed/pginstall/bin/pg_config --configure
python -c 'import pgembed; print(pgembed.BUNDLED_PG_MAJOR, pgembed.BUNDLED_POSTGRES_VERSION); print(pgembed.list_extensions())'
```

Static/build tests：

```bash
pytest -q \
  tests/test_postgres_build.py \
  tests/test_bundle_metadata.py \
  tests/test_commands.py \
  tests/test_tigerfs_build.py
```

Runtime tests：

```bash
pytest -q tests/test_pgembed.py tests/test_bundled_tools.py
```

TigerFS：

```bash
pytest -q tests/test_tigerfs_pg18.py -m "not tigerfs_mount"
pytest -q tests/test_tigerfs_pg18.py -m tigerfs_mount
```

Wheel build/audit：

```bash
python -m build --wheel
python -m zipfile -l dist/*.whl
python -m cibuildwheel --output-dir wheelhouse
auditwheel show wheelhouse/*.whl
```

macOS使用`otool -L`、Linux使用`ldd`枚举`postgres`和shared libraries；shell glob不可靠时由Python test枚举文件。外部迁移文档单独列出`pg_dumpall`/`pg_dump`/`pg_restore`与`pg_upgrade --check`示例，但这些命令不是pgembed自动化API。

## 15. Risks, failure recovery and rollback

### 15.1 Risk matrix

| Risk | Impact | Mitigation |
|---|---|---|
| stale PG17 `.so`进入PG18 wheel | undefined symbol、startup crash、数据风险 | whole-prefix stamp、metadata major、release clean build |
| source/toolchain identity漂移 | 无代码变化回归、不可复现 | exact PG tag/commit、digests、exact psql_bm25s/Rust locks |
| PG17 PGDATA被PG18路径修改 | cluster不可用或诊断复杂 | bundle/major validation先于system-user、ownership/mode、registry与server副作用；Gate 4审计 |
| startup/readiness无界 | CI/用户进程hang | monotonic deadline、early-exit、bounded cleanup |
| constructor留下坏instance | 后续调用复用partial state | transactional registration/rollback tests |
| extension只编译不运行 | catalog/worker/index失败 | Gate 3 runtime integration |
| Timescale/pg_duckdb planner crash | postmaster crash | isolated probe + liveness check |
| PG18 checksum/collation变化 | migration性能/index correctness | inventory、upstream checklist、REINDEX/restore guidance；不无证据自动处理 |
| TigerFS shim与native uuidv7冲突 | history/savepoint失败 | fresh不建shim、definition inventory、functional gate |
| Homebrew/host动态依赖泄漏 | 用户机器无法load | otool/ldd/auditwheel、三平台native smoke |
| libcurl/ICU差异 | pg_net/psql_bm25s load失败 | dependency audit、无外网runtime smoke |

### 15.2 Failure recovery

- stamp invalidation后build失败：prefix视为incomplete；下次make按缺失目标重建，不把旧prefix恢复为可信状态。
- metadata generation失败：不留下final JSON，wheel gate失败。
- major mismatch：不启动、不配置、不停止旧server；使用正确wheel或执行文档迁移。
- preload failure、startup timeout或调用方取消：保留PGDATA/log，bounded回收partial postmaster，移除instance/atexit/PID残留；取消保留原异常；修复bundle/config后重试。
- `CREATE EXTENSION`失败：依赖PostgreSQL事务回滚；已创建predecessor可保留，后续继续`IF NOT EXISTS`。
- TigerFS mount失败：按实际mount state unmount/reclaim daemon，再stop PostgreSQL；本次不新增mount manager。

### 15.3 Data rollback table

| Scenario | PG18 behavior | Mutation | Rollback |
|---|---|---|---|
| PG18 wheel读取PG17 PGDATA | pre-start structured fail-fast | none | 回到PG17 wheel继续原PGDATA |
| malformed/nonempty-no-version | inspection error | none | 恢复目录/backup后重试 |
| fresh PG18 PGDATA | PG18 initdb | new cluster | PG17不能读取；删除或恢复PG17 backup |
| 用户执行外部dump/restore | 写新destination | pgembed不修改source | 保留PG17 source即可回切 |
| 用户执行外部`pg_upgrade` | 新cluster/copy或link | 取决于外部命令模式 | 保留旧cluster且避免默认link |
| application package回退PG17 | PG17拒绝PG18 PGDATA | no conversion | 恢复升级前PG17 backup |

迁移前必须记录PG_VERSION、full server version、databases、roles、tablespaces、installed extensions、preload、collations、`public.uuidv7()` definition、backup路径和恢复命令。

## 16. Open questions

没有仍会改变本计划架构或实施顺序的产品决策。实现阶段仍需记录但不需要重新设计的证据项包括：

- psql_bm25s 已验证 probe commit的确切SHA；若原probe workspace已不可恢复，则在相同PG18.4配置下重新clean-build并固定新SHA。
- VectorChord exact Rust toolchain和cargo-pgrx组合；以三平台通过的同一组合为准。
- PG18 configure是否继续`--without-icu`；选择必须以功能需求和dependency audit为证据，并写入bundle identity，不能静默漂移。
- pre-release观察期的长度和stable promotion日期；它们影响运营排期，但不改变本计划要求stable promotion前重跑Gate 0–5。

## 17. References

### Repository

- `pgbuild/Makefile`
- `.github/workflows/build-and-test.yml`
- `src/pgembed/postgres_server.py`
- `src/pgembed/__init__.py`
- `src/pgembed/_commands.py`
- `tests/test_pgembed.py`
- `tests/test_tigerfs_build.py`
- `cibuildwheel_test.bash`
- `MANIFEST.in`
- `docs/investigations/embedded-postgres-plugin-installation-2026-05-15.md`
- `docs/investigations/pg_duckdb-timescaledb-time-bucket-collision-2026-07-28.md`
- Git commits `8ea2c87`, `b96704b`, `c000063`

### PostgreSQL 18

- Upgrade overview: <https://www.postgresql.org/docs/18/upgrading.html>
- `pg_upgrade`: <https://www.postgresql.org/docs/18/pgupgrade.html>
- PostgreSQL 18 release notes: <https://www.postgresql.org/docs/18/release-18.html>
- UUID functions: <https://www.postgresql.org/docs/18/functions-uuid.html>
