# stannum vs pg_textsearch：pg-agent v13 选型评测报告

日期：2026-10-02 · 环境：macOS (Apple Silicon, arm64) · PostgreSQL 18.4 (pgembed bundle)

## 结论（TL;DR）

**pg-agent v13 应继续使用 stannum。** 决定性因素不是性能，而是检索能力：

| 维度 | stannum | pg_textsearch 1.5.1 |
|---|---|---|
| 中文词级检索（无空格语料） | ✅ recall@10 = 1.00（jieba 内置） | ❌ recall@10 = 0.00（PG 内建 parser 把整段 CJK 当一个 token；需外部 zhparser，pgembed 未捆绑） |
| 多列 BM25F 字段权重（`field_weights='title:3,body:1'`） | ✅（加权后排序实测翻转） | ❌（bm25 索引单列；表达式拼接无权重） |
| 短语/顺序敏感的**排序内**查询 | ✅（TINQL `"a b"`/`THEN/NEAR`/`AND NOT`/`^boost`） | ❌（`<@>` 为词袋：顺序无关、同分；短语只能走 `@@ to_tsquery` 过滤，非索引内排序） |
| 高亮/片段 | ✅（`search(snippet=>)`、`highlight`） | ❌（无；ts_headline 对 CJK 词同样失效） |
| Agent 工具层（tokenize/verify_index/index_stats/segment_info/bind_query/search_count…） | ✅ 全部在 | ❌（面向 DBA：level_counts/compact/spill） |
| 部署 | 无需 preload | 必须 `shared_preload_libraries`；RLS 统计泄露注意事项 |
| 英文 top-k 延迟（20k docs） | 慢（见下，路径依赖） | **快 5–100×（Block-Max WAND，C 实现）** |

pg_textsearch 是更快的英文 BM25 引擎；但它无法回答 pg-agent v13 生产负载里的任何一类中文查询——不是慢，是**零召回**。

## 被测版本

- **pg_textsearch** `v1.5.1`（timescale/pg_textsearch @ `fa142d049d08eac45a263f5abc250c8877ae076e`）。**仅用于本次评测，不随 pgembed 发布**：`pgbuild/Makefile` 把它列为 stannum 的基准对照目标，明确不进 `EXTENSIONS`、bundle stamp 和 wheel 元数据。
- **stannum** `0.5.0` stn3 尖端（wuxianliang/stannum @ `d895c8e98d89bcb40dcf374799f2e58df801c7ae`，"kind-5 envelope unlocks multi-column"，已含 BM25F 多列）。评测时 pgbuild 的 pin 还是 `a963080`（无多列），所以对比构建取自 `d895c8e`；**多列是 v13 field_weights 契约的前置**，发布 pin 现已推进到 0.5.1（`3d9e96b`）。
- 复现：`tools/bench_stannum_vs_pg_textsearch.py`（20k docs）、`benchmarks/stannum-vs-pg-textsearch/query_path_probe.py`（路径对比）、`tests/test_stannum_vs_pg_textsearch.py`（20 项功能断言，全部通过）。

## 方法

双语文料 20,000 docs（英文 Zipf 词 + 1–3 句**无空格**中文句子，中文词表先经 `stannum.tokenize` 验证 jieba 稳定），同库双表，各自建本引擎的惯用索引：

- stannum：`(title, body) tokenizer=jieba field_weights='title:3,body:1'`（pg-agent 形态）+ 单列 unicode（质量对照）
- pg_textsearch：`bm25(body) text_config='english'`（推荐形态）+ `bm25(body) simple`（质量对照）

延迟：psycopg2 预热 8 次 + 50 次计时，LIMIT 10。质量：recall@10 对生成器 ground truth（精确 token 包含）；跨引擎排序一致性用 RBO@10。注意公平性差异：stannum bm25f 索引**两列**、pgts 索引**单列**——这是各自对 pg-agent 需求的惯用解，体量对比见原始 JSON。

## 性能结果（20k docs）

### 构建与体量

| 指标 | stannum | pg_textsearch |
|---|---|---|
| build（惯用形态） | 1.87 s（bm25f 两列+jieba） | 1.26 s（english 单列） |
| build（同列对照） | **0.42 s**（unicode 单列） | 0.74 s（simple 单列） |
| 索引大小（惯用） | 9.8 MB（两列） | 7.2 MB（单列） |
| 索引大小（同列） | 7.9 MB | 7.2 MB |
| 摄入 5000 docs | 4,675 docs/s | **6,665 docs/s** |

### 查询延迟 p50/p95（ms）

| 查询类 | stannum（search SRF） | pg_textsearch |
|---|---|---|
| en 稀有词 | 2.72 / 3.54 | **0.06 / 0.08** |
| en 高频词 | 13.30 / 20.73 | **0.13 / 0.19** |
| en 3 词组合 | 10.70 / 11.69 | **0.14 / 0.19** |
| 中文词 | 2.45 / 2.93（**有结果**） | 0.04 / 0.06（**零召回**，量的是"快速返回空"） |
| 高频词 LIMIT 100 | 9.45 / 9.91 | **0.17 / 0.20** |
| 摄入后（churn 后） | 14.29 / 15.04 | **0.14 / 0.17** |

churn 后 stannum `stn_bm25f` 4 个段、pgts levels `[1,2,0,…]`；体量各自涨到 15.0 / 12.3 MB。

### 关键补充：stannum 的延迟取决于查询路径

`query_path_probe.py`（同列 unicode vs simple，20k docs，LIMIT 10 p50）：

| 词类 | search() SRF | `==>` CustomScan | pg_textsearch |
|---|---|---|---|
| 高频 `socket` | 4.87 ms | **0.21 ms** | 0.11 ms |
| 高频 `catalog` | 6.25 ms | **0.20 ms** | 0.15 ms |
| 中频 `refrain` | 2.61 ms | 1.33 ms | 0.07 ms |
| 中频 `gneiss` | 2.62 ms | 0.53 ms | 0.07 ms |

`==>` CustomScan（fused WAND）在高频词上已接近 pgts 同速（~2×差距）；**慢 20–30× 的是 `search()` SRF 路径**（ranked+snippet），这正是 stn3 计划中 step 4.6「latency gate」要解决的（`prompt-exports/oracle-plan-2026-10-02-step-4.6-latency-gate.md`）。另注：SRF 14ms 的绝对值对 agent 交互（>100ms 预算）仍可用，但批量/循环调用会痛。

## 质量

| 指标 | stannum | pg_textsearch |
|---|---|---|
| recall@10（英文，精确 token） | 1.00 | 1.00 |
| recall@10（中文词，无空格） | **1.00** | **0.00** |
| 跨引擎 RBO@10（英文） | 0.617（两引擎 BM25 变体排序明显不同，属预期） | — |

功能细节（20 项断言全过）见 `tests/test_stannum_vs_pg_textsearch.py`；要点：jieba 对无空格句子的词级切分与索引/查询两侧一致；pgts 词袋对 "postgres index" vs "index postgres" 给出完全相同分数；stannum 短语 `"postgres index"` 只命中顺序相邻的文档；field_weights 5:1 vs 1:5 实测互换 top-1；英文词干（running/runs）pgts 默认可跨形态匹配、stannum unicode 精确匹配（可用 TINQL alternatives/`long_tokens='split'` 等补偿）；>2047 字节 lexeme PG 直接丢弃，stannum `long_tokens='split'` 把长 token 切成可检索块。

## 对 pg-agent v13 的建议

1. **继续 stannum**。中文语料（transcript/decisions/chunks）词级召回是硬需求，`field_weights`、短语、highlight、工具层（`verify_index`/`segment_info`/`bind_query`/`search_count`）在 v13 有 78 处 `==>` 调用点承重。
2. **把 stn3 的 4.6 延迟门当作切换条件跟踪**：交互热路径优先走 `==>` CustomScan（已 ~pgts 级），`search()` SRF 用于需要 snippet 的场景；4.6 完成前避免在高频词上循环调 SRF。
3. **pgbuild pin 已升级到含多列的 0.5.1**（`3d9e96b`；评测数据仍来自 `d895c8e` 的 stn3 构建），v13 的 field_weights 契约现在有发布 pin 可依赖。
4. **pg_textsearch 留作观察项**：若未来出现英文专用、超低延迟需求的场景（或其增加字段权重/CJK 支持），值得重测。其并行构建、后台 compaction、memtable 缓存设计对 stannum 的 4.6 优化也有借鉴价值。
5. 遗留提醒：`tests/test_stannum_jieba.py::test_dictionary_drift_reindex_and_presets` 原先硬编码 `extversion == '0.4.0'`，现已改为从 Makefile 读取期望版本；新增扩展进入 bundle stamp 后，下次 `make` 会触发全量重建（bundle identity 语义，预期行为）。

## 附录：Postgres 生态中英文双语搜索项目调研（2026-10-02）

沿用户问题「还有哪些项目能提供等同于 stannum 的中英文搜索能力」，逐仓库核实（GitHub API + 官方文档）。以 stannum 的承重面为基准：

| 能力 | stannum | ParadeDB pg_search v0.25.11 | PGroonga 4.x | zhparser (amutu) | pg_jieba (jaiminpan) | pg_bigm / pg_trgm | rum (postgrespro) |
|---|---|---|---|---|---|---|---|
| BM25 相关性排序 | ✅ BM25F | ✅ Tantivy BM25（per-field k1/b） | ❌ TF（`pgroonga_score`，文档明示不支持自定义打分） | ❌ ts_rank | ❌ ts_rank | ❌ 无排序模型 | ❌ ts_rank（但支持索引内 ORDER BY） |
| 中文词级分词 | ✅ jieba（词典可插） | ✅ **原生 `pdb.jieba`**（search_mode 默认开、多粒度、繁简转换 t2s/s2t）+ lindera + chinese_compatible（单字） | ✅ TokenBigram 等（词级靠词典 token，主要是 bigram 子串） | ✅ SCWS 词级 | ✅ cppjieba 词级 | bigram/trigram 子串 | 继承 parser |
| 英文处理 | unicode/精确 + 词干可选 | en_stem/ICU 等 13 种 tokenizer | ✅ | simple/english mapping | 同左 | ✅ | 同 parser |
| 多列字段权重 | ✅ `field_weights`（索引时 BM25F） | ⚠️ 查询时 `::pdb.boost(N)` 逐字段（表达力等价，无索引时静态权重） | ❌ | ⚠️ setweight A/B/C/D（4 档粗粒度） | 同左 | ❌ | 同 tsvector |
| 排序内短语 | ✅ TINQL `".."`/THEN/NEAR | ✅ phrase/phrase-prefix/proximity | ✅ | ✅ phraseto_tsquery | 同左 | ❌ | ✅（索引内 distance） |
| highlight | ✅ 四参 highlight | ✅ pdb.snippet(s) | ✅ highlight_html/snippet_html | ts_headline | ts_headline | ❌ | ts_headline |
| top-k 索引扫描 | ✅ `==>`/search | ✅ LIMIT pushdown | ✅ | 依赖 GIN | 依赖 GIN | ✅ 子串 | ✅（特色） |
| PG18 | ✅ | ✅（PG15–18，pgrx 0.19.2） | ✅（官方 postgresql-18 包） | 未声明（Docker 到 16；parser 类一般可编） | 徽章到 PG17 | ✅ 内建/PGDG | 活跃（2026-09），未验证 18 |
| 打包/部署 | pgrx Rust，preload | **最重**：preload + **必须 pgvector** + 大体积 Rust | PGDG 全面打包，最成熟 | 需 SCWS 库 | 需 preload + cmake，2023-10 后低维护 | 内建（trgm）| PGDG |
| agent 工具层 | ✅ verify_index/index_stats/score_bound/bind_query… | ❌（自有 SQL 面：`\|\|\|`、pdb.score/snippet） | ❌（自有函数面） | ❌ | ❌ | ❌ | ❌ |

结论：

1. **能力上真正等同 stannum 的只有 ParadeDB pg_search**：BM25 + 原生 jieba 词级中文（search_mode 多粒度甚至比 stannum 的朴素 jieba 更强，繁简转换是独有优势）+ per-field BM25 参数 + 查询时字段 boost + 短语/highlight/top-k + PG18。代价：必须 preload、必须装 pgvector、二进制大（Tantivy 全家桶），对 pgembed 打包是三项里最重的。
2. **切换成本不止打包**：pg-agent v13 有 78 处 `==>` 调用与十几个工具函数承重，迁到 pg_search 需要重写整个 SQL 面（`|||` 操作符 + pdb.* 函数），且没有 verify_index/index_stats/score_bound 等运维工具层。
3. PGroonga 打包最成熟、CJK 覆盖最广，但 TF 打分、无字段权重，对以 BM25 相关性为核心的场景不等同。
4. zhparser/pg_jieba + 内建 FTS（+ rum 加速排序）是「纯 PG 栈」可行解，但 ts_rank + setweight 四档权重与 BM25F 差距明显；pg_jieba 维护活跃度低于 zhparser（2023-10 vs 2026-06）。
5. 对 pg-agent v13 的建议不变：**继续 stannum**；把 ParadeDB pg_search 列为头号对标（若 stannum 停更或需要 facets/hybrid search 时再评估），PGroonga 仅当需求转向「子串召回优先」时考虑。

核实来源：paradedb/paradedb（docs/reference/tokenizers/available-tokenizers/{jieba,lindera,chinese-compatible}.mdx、full-text/{relevance-tuning,score,highlight,top-k}.mdx、indexing/create-index.mdx BM25 Parameters、pg_search/README.md PG15–18）、pgroonga.github.io/reference（pgroonga-score「TF for now… doesn't support yet」）、仓库 packages/postgresql-18-* 包目录、amutu/zhparser、jaiminpan/pg_jieba、pgbigm/pg_bigm、postgrespro/rum。

### 附录补遗：pg_search vs pg_textsearch 正面对比（对 pg-agent 而言）

针对「pg_search 比 pg_textsearch 哪个更适合 pg-agent」补充核实（ParadeDB v0.25.11 文档 + 仓库）：

| 维度 | pg_search (ParadeDB) | pg_textsearch (timescale) |
|---|---|---|
| 中文词级召回 | ✅ 原生 `pdb.jieba`：search_mode 默认开（多粒度），繁简转换 t2s/s2t；另有 lindera/chinese_compatible | ❌ 实测 recall 0.0（需外挂 parser 才有解） |
| BM25 | ✅ Tantivy BM25，per-field k1/b | ✅ BM25，k1/b 可配（索引级） |
| 字段权重 | ✅ 查询时 `::pdb.boost(N)` 逐字段子句（BM25F 等价表达力） | ❌ 单列索引，无权重 |
| 短语/邻近 | ✅ `###` 短语+slop、`##`/`##>` 无序/有序邻近（比 stannum 的 NEAR/THEN 更细） | ❌ 无位置信息，词袋 |
| highlight | ✅ pdb.snippet/snippets | ❌ |
| 谓词操作符 | ✅ `@@@`/`|||`/`&&&`/`===`（WHERE 布尔谓词，`==>` 直接对应物）+ Lucene 风格 pdb.parse 查询语法 + fuzzy/regex/range | ⚠️ `<@>` 返回负分供 ORDER BY，查询面简单 |
| 一致性 | ✅ 新架构 ACID/MVCC 同步可见（guarantees.mdx；老版「后台队列最终一致」已成历史） | ✅ 同步（LSM memtable+compaction） |
| PG18 | ✅ 15–18 | ✅ |
| License | ⚠️ **AGPL-3.0** + open-core（物理复制/HA/读副本 = Enterprise） | ✅ PostgreSQL License |
| 打包 | ⚠️ 最重：preload + **pgvector 硬依赖** + pgrx 0.19.2 Rust 工具链；覆盖索引，加列需 REINDEX | ✅ C/PGXS 轻量（已入 pgembed，v1.5.1） |
| 实测延迟 | 未实测（Tantivy WAND，公开基准对标 ES） | ✅ 已测：p50 0.036–0.15ms（20k docs） |
| agent 工具层 | ❌（有面向编码 agent 的 skills/MCP 文档入口，但无 verify_index/index_stats 等 DBA 面） | ❌ |

**结论：对 pg-agent 而言 pg_search 明显更适合**——它补齐了 pg_textsearch 被否决的全部四个硬缺口（中文召回、字段权重、排序内短语、highlight），且 `|||`/`@@@` 谓词是 `==>` 的直接对应物，一致性模型是同步事务。pg_textsearch 仅剩运营性优势（轻量 C 构建、宽 license、实测亚毫秒延迟），这些都不构成对能力缺口的补偿。pg_search 的代价是 AGPL-3.0、最重的打包（preload+pgvector+Rust）、覆盖索引改列需 REINDEX，以及同样缺失 agent 工具层。

**替代排序更新：stannum（保留）≫ pg_search（唯一可行替代，代价为 AGPL/重打包/SQL 面重写）≫ pg_textsearch（仅英文+延迟极端敏感场景）**。若要量化 pg_search，可在现有 benchmark 矩阵加一列（pgembed 需先加 pgvector 依赖与 Rust 构建，工作量显著大于 pg_textsearch）。
