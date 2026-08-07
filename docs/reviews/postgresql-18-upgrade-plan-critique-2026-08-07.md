# 评审：PostgreSQL 18 升级计划批注

**日期：** 2026-08-07
**评审对象：** `docs/plans/postgresql-18-upgrade-2026-08-07.md`（下称"plan"）
**基线：** `prompt-exports/oracle-plan-2026-08-07-134044-postgresql-18-upgrad-e94a.md` 的 **Generated Plan** 响应（下称"export"）；其开头的 composed prompt / selected-file dump 仅作为上下文，不作为 plan 内容。
**用户已确认决策（不重开）：** D1 fail-fast + 外部迁移文档；D2 固定 PG18.4 tag/commit；D3 compatibility-only；D4 pre-release/RC 先行。
**范围约束：** 本批注只覆盖五类问题，不重开四项决策、不主张删除"准确但具体"的内容、不扩大 scope、不重写 plan。除已核对的少数 seam 外不做广泛代码探索。

**核实方式：** 以下每条都对照当前工作树核实。引用形如 `file:line` 已直接读盘确认。

---

## 摘要

plan 是对 export 的一次忠实的 D1–D4 收敛，结构清晰、决策边界明确。它的主要弱点不在"决策"，而在**两个结构性 seam 上对一个共同的、未被任一文档识别的实现约束**：`PostgresServer.__init__` 在持锁状态下调用 `ensure_pgdata_inited()` 与 `ensure_postgres_running()`，且这些方法对 **running-as-root** 的调用者执行 `ensure_user_exists`/`chown`/permission 调整。把 plan 设想的 PG_VERSION major 检测、metadata/binary 校验和 bounded startup 放进这条链路而不先处理"锁 + root side-effect"这两点，会把 fail-fast 承诺削弱为"只在非 root 且无并发时成立"。export 同样未识别这两点，所以这是**双方共同遗漏**（第 4 节）。

其次是若干被弱化的可测试性/语义细节（第 1、2 节）、少数被代码直接证伪的陈述（第 3 节），以及一组会改变实现顺序的待答问题（第 5 节）。

---

## 1. Export 中有实现价值、但在 plan 中被弱化/泛化/遗漏的内容

### 1.1 【弱化】`BUNDLED_PG_MAJOR` 的 `None`/editable-tree 语义被丢成无条件常量
- **Export（§3.2, lines 772–779）：** `BUNDLED_PG_MAJOR: int | None`、`BUNDLED_POSTGRES_VERSION: str | None`；**`None` 只允许出现在尚未跑过 build 的 editable/source 树**；此时 `get_server()` 必须以结构化 metadata/binary-unavailable error 失败，而不是猜版本。
- **Plan（§5.6, line 201–205）：** 只 re-export `BUNDLED_PG_MAJOR` / `BUNDLED_POSTGRES_VERSION` 两个常量，完全没有 `None`/开发树分支。
- **影响：** plan §5.6 同时说"metadata 缺失、损坏或和 binary 不一致时，不把 extension 标为 available，也不触碰 PGDATA"。但在 editable 树里 metadata 必然缺失，开发者日常 `pytest`（非 wheel）运行时 `_detect_extensions()` 会在 import 期就 fail-closed。plan 没有回答"开发树里 `get_server()` 应该 fail 还是允许在没有 metadata 时继续"。这是会在 WI-5 立刻撞到的决策，export 给了答案，plan 把它泛化没了。
- **建议：** 恢复 export 的三分支语义：wheel（metadata 在且一致）→ 正常；editable 无 metadata → extension 全 `False` 且 `get_server()` 抛 metadata-unavailable；editable 但 metadata 与 binary 不一致 → `BundledPostgresMetadataError`。

### 1.2 【弱化】package-local attestation 的"双向"处理被简化
- **Export（§3.3, lines 802–807）：** 明确三件事——(a) **优先使用 bundle 内经 metadata attest 的 artifact**；(b) package-local 仅在提供 `built_for_postgres_major=18` attestation 时才覆盖；(c) 旧 standalone package 无 major metadata 时 fail closed + warning。
- **Plan（§5.7, lines 214–216 / §11.2, 640–646）：** 覆盖了 (b)(c)，但把 (a) "优先 bundled、package-local 是覆盖项"这一**优先级方向**讲成了 package-local 优先（`__init__.py:117-122` 当前确实是 package-local 优先检测）。plan 没点明"要反转/约束当前 `__init__.py` 的 package-local 优先行为"，读者可能误以为只需在现有优先级上加一个 major 检查。
- **建议：** 显式声明这是一个对 `__init__.py:117-122` 现有检测顺序的**有意收紧**，并说明 bundled-first 的判定顺序。

### 1.3 【遗漏】TimescaleDB↔pg_duckdb 的 upstream issue 编号未进入 plan
- **Export（§8, line 1039）与 investigation（`pg_duckdb-timescaledb-time-bucket-collision-2026-07-28.md:24,40`）：** planner segfault 对应 upstream **pg_duckdb #845 / #963**；name-collision guard 对应 **PR #747**（已并入 v1.1.x）。
- **Plan（§6.5, lines 372–381）：** 只说"investigation 和 upstream issue 的最小复现 SQL"，**未点名 #845/#963/#747**。
- **影响：** plan §6.5 要求"直到测试证明 upstream 冲突已消失才可改 `EXTENSION_PRECEDENCE`"。没有 issue 编号，实现者无法定位要监视哪个 upstream fix 来决定何时能安全放开 precedence。这是 attribution 的具体性，属于"准确且具体"的信息，应当保留。
- **建议：** 在 §6.5 直接引用 `#845/#963`（planner）与 `#747`（collision guard），与 investigation doc 对齐。

### 1.4 【弱化】`get_extension_path()` 的诊断语义与签名保留说明被丢
- **Export（§3.3, lines 788–800）：** 明确 `get_extension_path(name) -> Optional[Path]` **保留其签名**，并特别说明"返回 path 不代表 `has_extension()` 为 true"。这防止实现者把"文件在"误当"可用"。
- **Plan：** §5.7 未提 `get_extension_path()`；§3.2 non-goals 只列了 `has_extension()`/`list_extensions()`/`create_extension()`，**漏了 `get_extension_path()`**。
- **建议：** 把 `get_extension_path()` 列入"签名保持不变"清单，并保留 export 的"path 存在 ≠ available"告诫。

### 1.5 【弱化】Gate 0 的 "GNU Make 3.81 间接证明" 手段未交代
- **Export（§12.1, line 1234）：** `tests/test_postgres_build.py` 需"在 macOS runner 间接证明 GNU Make 3.81 compatibility"。
- **Plan（§9 Gate 0, line 473）：** 列了 stamp 行为测试，但没说明 3.81 兼容性**如何被测试**。
- **补充（代码已证）：** 现有 `tests/test_tigerfs_build.py:336-343` 用 `@pytest.mark.skipif(platform.system() != "Darwin")` + `make --version == "GNU Make 3.81"` 证明。plan 应点名复用同一 skipif 模式，否则 Gate 0 在非 macOS runner 上对 3.81 是静默放过的。

---

## 2. 欠规约的 seam、未决的实质决策、矛盾、错误引用、缺失依赖

### 2.1 【矛盾 / 错误引用】plan 引用了不存在的 `pgbuild/Makefile` 行号
- plan §4 多处引用 `pgbuild/Makefile:2 / 50-61 / 64-66 / 214-218 / 144-147 / 260-317`。**该文件被 `.gitignore:136` 忽略，工作树中不存在**，只能从 export 的 `<non_selectable_file>` verbatim 重建。重建后行号对不上：例如 export 内嵌 Makefile 里 `POSTGRES_VERSION := 17-stable` 在其第 111 行（export line 111），`POSTGRES_SRC := REL_17_STABLE` 第 112 行，而 plan §4.3（line 65）写作 `pgbuild/Makefile:64-66`。`AGE_TAG := release/PG17/1.7.0` 在 export line 199，plan §4.6 表格未给行号但 §4 其它处给的行号同样偏小。
- **影响：** 这些行号引用**全部无法核对**，且很可能错误。对一个 gitignored 的核心 artifact 引用精确行号本身就是脆弱的。
- **建议：** 对 gitignored 的 `pgbuild/Makefile` 改用"变量名/目标名"引用（如 `POSTGRES_SRC`、`AGE_TAG`、`psql_bm25s` target），不要引用行号；或先落地一份 tracked 的 build-recipe 镜像再引用。

### 2.2 【欠规约】metadata 的 "cache epoch" 与 CI "cache epoch" 是两套还是一套
- plan §5.5（line 170）在 metadata schema 里要"bundle schema/recipe/**cache epoch**"；§10（line 540）又要 workflow 里新增 `PG_BUILD_CACHE_EPOCH=pg18-bundle-v1`。
- **问题：** 这两个 epoch 的关系没说清。是 metadata 内嵌一个 epoch、CI key 另有一个 epoch，还是同一来源？若同一来源，谁来生成、如何保持一致（gitignored Makefile 不能作为 tracked source）？export §3.1/§11 同样有两处 epoch 但也没对齐。
- **建议：** 明确：CI cache epoch 是 tracked workflow 常量；metadata 内只记录 build-time identity，不复制 CI epoch。删除 metadata 里的 "cache epoch" 字段或定义其与 workflow epoch 的单向派生关系。

### 2.3 【欠规约】Wheel matrix 收敛到 cp312+ 的落地位置
- plan §4.7（line 108）与 §11.1 `build-and-test.yml`（line 613）都说收敛到 cp312+。代码已证当前 `CIBW_BUILD: cp310-* cp311-* cp312-* cp313-* cp314-*`（`build-and-test.yml:112`），而 `pyproject.toml:6` 是 `requires-python >=3.12`。
- **欠规约点：** 收敛 cp310/cp311 是**减少**已发布的 wheel，这本身是一次面向用户的兼容性变化，plan 未把它列入 user-facing 变更（README/release note），也未说明 cp314 是否保留。这是比"build 调参"更大的决定。
- **建议：** 在 §11.1 或 §15 显式记录"cp310/cp311 wheel 自本版本起停发"为 user-facing breaking note，并明确 cp314 保留与否。

### 2.4 【缺失依赖】`tests/test_postgres_build.py` 的 fake-prefix 依赖一个尚不存在的 stamp target
- plan §12 WI-3（line 674）要求 stamp 测试"不编译 PG"，并复用 `tests/test_tigerfs_build.py` 的 fake-prefix 思路。但 TigerFS 测试（`test_tigerfs_build.py:90-101`）是通过 `make -C pgbuild tigerfs TIGERFS_CONFIG_STAMP=... INSTALL_PREFIX=...` 直接驱动一个**已存在的、可被变量覆盖的 target**。
- **缺口：** plan 没有交代 `POSTGRES_BUNDLE_CONFIG_STAMP` target 会如何被独立调用（变量名、是否可被 `INSTALL_PREFIX=`/`..._STAMP=` 覆盖、是否需要 stub `postgres` binary）。没有这些，WI-3 的"fake-prefix 测试"无法落地。
- **建议：** 在 §5.3 增加一句：stamp target 必须像 `TIGERFS_CONFIG_STAMP` 一样支持 `INSTALL_PREFIX=` 与 stamp-path 覆盖，且不强制要求真实 `postgres` binary（TigerFS 测试用 `bin/postgres` 写了 dummy 字节即可，见 `test_tigerfs_build.py:52-53`）。

### 2.5 【欠规约】`BUNDLED_PG_MAJOR` 在 import 期计算与 `_detect_extensions()` 的循环/顺序
- `src/pgembed/__init__.py:222` 在模块底部直接调用 `_detect_extensions()`。若 detection 改为依赖 metadata（§5.7），则 `BUNDLED_PG_MAJOR` 必须在 `_detect_extensions()` **之前**就绪；而 `BUNDLED_PG_MAJOR` 又可能要求跑 `postgres --version`（§5.9）。在 **import 期**就 fork `postgres --version` 是有代价且可能失败的（开发树无 binary）。
- **缺口：** plan 没说 `BUNDLED_PG_MAJOR` 是 import-time 常量还是 lazy。export §3.2 用 `None` 规避了这点（见 1.1），plan 丢了 `None` 后这个顺序问题更突出。
- **建议：** 明确 `BUNDLED_PG_MAJOR` 为 lazy/缓存（首次 `get_server()` 时校验 binary），import-time detection 只读 metadata、不 fork binary；与 1.1 一并解决。

---

## 3. 被代码证伪、任务不需要、或被已命名的更简单设计完全取代的细节

### 3.1 【代码证伪】plan §4.4 说 `_cleanup()` 的 `pg_ctl stop` "没有 timeout" —— 表述过宽
- **Plan（line 78）：** "`_cleanup()` 的 `pg_ctl stop` 没有 timeout（`postgres_server.py:288-333`），失败恢复本身也可能阻塞。"
- **代码（`postgres_server.py:336-352`）：** `pg_ctl(["-w","stop"], ...)` 确实**没传 `timeout=`**（对），但其后 failure path 已有 bounded 回收：`process.terminate()` + `process.wait(2)` + `process.kill()`（lines 346-352）。即"stop 本身无 timeout"成立，但"失败恢复可能**无限**阻塞"不成立——terminate/kill 路径是有界的。
- **Correction：** 准确表述应为"`pg_ctl -w stop` 缺少 subprocess timeout，若 postmaster 拒绝响应 `-w` 会一直等到 `_get_command` 默认/无超时；但 terminate→wait(2)→kill 的回收链已存在且有界"。plan §5.12 新增的 `_cleanup()` timeout 是对**缺口的修补**，不是从零建立有界性。
- **Justification：** 这是精确性问题；若按 plan 字面理解，实现者可能重写整段 `_cleanup()` 回收逻辑，而实际只需给 `pg_ctl -w stop` 加 `timeout=` 并保留现有 terminate/kill。

### 3.2 【代码证伪】plan §4.4 暗示 constructor 异常会留下"partial object"，但漏了真正的失败模式
- **Plan（line 74 / §5.11）：** 担心"任一中间异常都可能留下 partial object 或注册状态"。
- **代码（`postgres_server.py:92-98`）：** 真实顺序是 `atexit.register(self._cleanup)` → `with self._lock:` → `self._instances[self.pgdata] = self` → `ensure_pgdata_inited()` → `ensure_shared_preload_libraries()` → `ensure_postgres_running()` → `global_process_id_list.get_and_add(...)`。**关键点：若 `ensure_pgdata_inited()` 抛异常，`atexit.register` 已生效且 `_instances[pgdata]` 已写入，但 `get_and_add` 未执行。** 于是之后 GC/`_cleanup` 被 atexit 触发时，`get_and_remove(os.getpid())`（line 320）会在一个从未 add 过的 pid 上操作，且 `del self._instances[self.pgdata]`（line 327）依赖 entry 仍存在。这是 plan §5.11 想修的，但 plan 没把 **"atexit 注册发生在任何校验之前"** 这个最早的副作用点讲清楚。
- **Correction：** §5.11 的 rollback 设计必须以"**`atexit.register` 与 `_instances` 写入都早于所有校验**"为前提，而不是笼统的"中间异常"。这决定了 rollback 必须同时撤销 atexit（`atexit.unregister(self._cleanup)`）与 `_instances` entry，且要在持锁状态下做。
- **Justification：** 不带这个精确顺序，rollback 很容易漏掉 atexit 注销，导致进程退出时对已失败 server 再次 `_cleanup`，二次触发 `del self._instances[...]` 的 `KeyError`。

### 3.3 【被更简单设计取代 / 任务不需要】plan §5.9 "第一次创建 server 前跑 `postgres --version`" 与 §5.6 metadata 校验重复
- plan §5.9（lines 240–249）要求每次首建 server 前 fork `postgres --version` 并比对 binary/metadata/`BUNDLED_PG_MAJOR` 三者；§5.6/§5.5 的 metadata 生成器（`tools/generate_bundle_metadata.py`）在 **build 期**已经跑过 `postgres --version` 与 `pg_config --version` 并断言 major=18（§5.5 lines 181–183）。
- **更简单设计：** wheel 是只读的、build 期已校验 binary↔metadata 一致，runtime 只需 **fail-closed 读取 metadata**（§5.6 已要求）并在 PGDATA 检测时比对 `PG_VERSION` major 与 metadata major。运行期再 fork 一次 `postgres --version` 是冗余的——它能发现的"binary 与 metadata 不一致"在只读 wheel 里只可能由打包错误造成，而那正是 build gate（§5.5 步骤 2 / Gate 2）的职责。
- **Correction：** 将 §5.9 的运行期 binary fork 降级为 **可选诊断/测试钩子**，而非常驻启动路径；常驻路径以 metadata 为权威。这同时消解 2.5 的 import 期 fork 问题。
- **Justification：** 减少启动开销、消除 root 下 fork 的复杂度（见 4.1），并把一致性证明收敛到 build gate——正是 plan §4.3/§9 已确立的"build 期证明 + runtime 读 metadata"边界。

---

## 4. 双方共同遗漏的需求 / 边界 / 依赖 / 架构问题（ownership · lifecycle · failure · cancellation · testability）

### 4.1 【架构 · 双方遗漏】持锁 `__init__` 内的 root side-effect 与 fail-fast 的顺序冲突
- **代码（`postgres_server.py:76-98, 159-211`）：** `__init__` 在 `with self._lock:` 内调用 `ensure_pgdata_inited()`；后者对 **root** 调用者先执行 `ensure_prefix_permissions` / `ensure_folder_permissions` / `os.chown(self.pgdata, ...)`（lines 161-179），**在检查 `PG_VERSION` 之前**。`__init__` 顶部还在 root 下调用 `ensure_user_exists`（line 80）。
- **问题：** plan §5.10 要求"在读/改 PGDATA 前先证明 bundle/binary 一致，且 PG_VERSION mismatch 在 config 写入、process 扫描、`_instances` 注册之前失败"。但当前 root 路径会在**任何 major 检测之前**就 `chown` PGDATA、调整 permission、甚至创建系统用户。对一个 PG17 PGDATA，这意味着 fail-fast 触发时 PGDATA 的 **ownership 已被修改**——直接违反 plan §9 Gate 4（"directory 内容保持不变"）与 §2 "no mutation before fail-fast" 的承诺。
- **Ownership/lifecycle 影响：** rollback（§5.11）还必须能**还原**这些 root side-effect 或保证其幂等，否则"磁盘不变"不成立。
- **建议：** plan 必须显式新增一条不变量——"root permission/user 准备必须推迟到 PG_VERSION major 校验通过之后"，并在 §5.10 检测算法中把 root 准备排在版本校验之后。export 同样未识别，属共同遗漏。

### 4.2 【架构 · 双方遗漏】`_cleanup()` 的 `del self._instances[...]` 与 refcount 在失败路径不健壮
- **代码（`postgres_server.py:318-327, 454-465`）：** `__exit__` 仅当 `self._count <= 0` 才 `_cleanup()`；`_cleanup` 里 `del self._instances[self.pgdata]`（line 327）在 `get_and_remove` 返回非单元素时已被 `return`（line 322-323）跳过。若 constructor 失败且从未 `get_and_add`，之后任何路径触发 `_cleanup` 都会走 `get_and_remove` → 可能不等于 `[pid]` → `return`，于是 `_instances` 里的 partial entry **永不被删**。
- **Failure 影响：** 后续 `get_server(pgdata)` 会因 `pgdata in _instances`（line 498）直接返回这个 partial/失败对象——正是 plan §2 Done-when 与 §5.11 想杜绝的"复用 partial state"。
- **建议：** §5.11 的 rollback 必须**无条件**从 `_instances` 摘除（在持锁下 `pop(pgdata, None)`），不依赖 `get_and_remove` 的 refcount 结果；并加测试覆盖"constructor 失败后再次 `get_server` 同 pgdata 不返回坏对象"。

### 4.3 【testability · 双方遗漏】`psql()` 用 `shell=True` 拼接 URI，会污染 structured error 的可注入性
- **代码（`postgres_server.py:361-367`）：** `psql()` 用 `subprocess.check_output(f"{executable} {self.get_uri()}", shell=True)`。`create_extension()`（line 415/418）经 `psql` 执行 `CREATE EXTENSION`。
- **问题：** plan §6 catalog/index/preload 验收、`EXTENSION_PRECEDENCE` 顺序、Timescale/pg_duckdb 回归全部经 `create_extension`→`psql`→shell。shell 拼接使这些路径的错误（libpq/psql 非零退出）以 `CalledProcessError` 的 `output` 形态抛出，与 §5.8 想建立的 structured startup/PGDATA error 体系是**两套**。export/plan 都未说明 catalog 阶段的失败是否/如何纳入结构化模型。
- **建议：** 明确 catalog/`create_extension` 阶段的错误是否纳入 `errors.py`；至少说明这些保持现状（`CalledProcessError`）以避免 scope 蔓延，并在 README 记录。

### 4.4 【cancellation · 双方遗漏】bounded startup 的 cancellation 语义未定义
- plan §5.12 定义了 monotonic deadline 与 timeout subtype，但**没有**定义"等待期间收到取消信号（KeyboardInterrupt / pytest timeout / atexit）"的行为。
- **代码现状（`postgres_server.py:299-311`）：** readiness 是 `while True: ... time.sleep(1.0)`，对 SIGINT 的响应取决于 Python 默认；改成 deadline 循环后，cancellation 时是否回收已启动的 postmaster、是否清 `_instances`/atexit，未规约。
- **建议：** 在 §5.12 增加一条："deadline 循环被异常（含 KeyboardInterrupt）打断时，走与 timeout 相同的 best-effort postmaster 回收 + `_instances`/atexit 清理路径"，并明确这是 §5.11 rollback 的同一实现。

---

## 5. 回答后会实质改变设计或实现顺序的问题

1. **`BUNDLED_PG_MAJOR` 在 editable/source 树（无 metadata）下的行为是什么？**（关联 1.1/2.5）——决定 `_detect_extensions()` 是否 fail-closed、`get_server()` 是否抛 metadata-unavailable，直接影响 WI-5 的第一步和开发者能否在非 wheel 环境跑 `pytest`。**应先于 WI-5 回答。**

2. **root 下的 permission/user 准备能否安全推迟到 PG_VERSION 校验之后？**（关联 4.1）——若不能（例如 cibuildwheel docker 必须先 chown 才能读 `PG_VERSION`），plan 的"no mutation before fail-fast"承诺需要重写为"仅 ownership/permission 元数据可变，内容不变"。**这会改动 §5.10 算法与 Gate 4 断言，应先于 WI-5 回答。**

3. **运行期是否还需要常驻 fork `postgres --version`？**（关联 3.3）——若采纳"build 期证明 + runtime 读 metadata"，可删掉 §5.9 的 fork，简化 root 路径并消解 import 期 fork。**影响 WI-5/WI-6 的工作量，应在 WI-5 前回答。**

4. **两个 cache epoch（metadata schema §5.5 vs workflow §10）是同一来源还是两套？**（关联 2.2）——决定 `tools/generate_bundle_metadata.py` 是否需要读取 workflow 常量，以及 gitignored Makefile 能否作为 epoch 的 tracked source。**影响 WI-3/WI-4 的 schema 设计，应在 WI-4 前回答。**

5. **cp310/cp311 wheel 停发是否作为 user-facing breaking 记录，cp314 是否保留？**（关联 2.3）——影响 README/release note 与 D4 pre-release 的兼容性表述。**影响 WI-15/WI-16，应在 WI-15 前回答。**

---

## 附：评审覆盖与未覆盖说明

- **已核对 seam（读盘确认）：** `postgres_server.py`（constructor 顺序、root side-effect、unbounded readiness、`_cleanup` 回收链）、`__init__.py`（detection、precedence、import-time `_detect_extensions()`）、`build-and-test.yml`（cache key、`CIBW_BUILD`、Rust/cargo-pgrx pin、publish 仅依赖 `build_wheels`）、`cibuildwheel_test.bash`（Linux 仅跑 `test_bundled_tools.py`）、`pyproject.toml`（`requires-python`）、`pgembed_pgvector/pgduckdb`（package-local 优先 + 无 major attestation）、`tests/test_tigerfs_build.py`（stamp 模板 + 3.81 skipif）、`.gitignore`（`pgbuild/` 忽略 → cache-key 风险）、`docs/tigerfs.md`（uuidv7 shim）、collision investigation（#845/#963/#747）。
- **未做广泛探索：** 遵循范围约束，未全面扫 `tests/test_pgembed.py` 全部 fixture、`utils.py`、`README.md` 全文；其中 `utils.PostmasterInfo` 的 readiness/parse 语义若在实现期与 §5.12 deadline 循环冲突，需按 4.4 一并处理。
- **未重开 D1–D4；未建议删除准确的具体内容；未扩大用户 scope。**
