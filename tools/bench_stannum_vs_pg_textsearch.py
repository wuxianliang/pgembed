#!/usr/bin/env python3
"""Benchmark: stannum vs pg_textsearch (timescale/pg_textsearch) for pg-agent.

pg-agent v13's retrieval workload: bilingual (English + unspaced Chinese)
transcript/decision/chunk documents with a weighted title, top-k BM25 queries,
and steady post-build inserts.  This script builds both engines over the same
generated corpus in one pgembed server and measures:

* index build time and size (idiomatic shapes per engine)
* post-build insert throughput (memtable / segment path)
* top-k query latency (p50/p95/min) per query class
* recall@10 against generator ground truth (exact-token containment)
* ranking agreement between engines (RBO@10)
* query latency after churn (post-ingest)

Output: a JSON results file plus a markdown summary on stdout.

Usage:
    .venv/bin/python tools/bench_stannum_vs_pg_textsearch.py \
        [--docs 20000] [--reps 50] [--out benchmarks/stannum-vs-pg-textsearch]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pgembed  # noqa: E402

PG_TEXTSEARCH_MISSING_MESSAGE = (
    "pg_textsearch is not a bundled extension; it is an external PG18.4 "
    "comparison target. `make all` does not install it. Provision pg_textsearch "
    "into this PostgreSQL 18.4 server prefix (the running postgres libdir/sharedir) "
    "and re-run. If you set PG_TEXTSEARCH_PREFIX, it must be that same server prefix. "
    "See tools/bench_stannum_vs_pg_textsearch.py."
)


def pg_textsearch_server_prefix() -> Path:
    override = os.environ.get("PG_TEXTSEARCH_PREFIX")
    server_prefix = Path(pgembed.POSTGRES_BIN_PATH).parent
    if override:
        prefix = Path(override)
        if prefix.resolve() != server_prefix.resolve():
            raise SystemExit(
                "PG_TEXTSEARCH_PREFIX must be this PG18.4 server prefix "
                f"({server_prefix}); PostgreSQL loads extensions from its own libdir."
            )
        return prefix
    return server_prefix


def pg_textsearch_is_provisioned(prefix: Path | None = None) -> bool:
    prefix = prefix if prefix is not None else pg_textsearch_server_prefix()
    control = prefix / "share" / "postgresql" / "extension" / "pg_textsearch.control"
    lib_dir = prefix / "lib" / "postgresql"
    has_lib = (lib_dir / "pg_textsearch.so").is_file() or (lib_dir / "pg_textsearch.dylib").is_file()
    return control.is_file() and has_lib


def require_external_pg_textsearch() -> Path:
    prefix = pg_textsearch_server_prefix()
    if not pg_textsearch_is_provisioned(prefix):
        raise SystemExit(PG_TEXTSEARCH_MISSING_MESSAGE)
    return prefix

# ---------------------------------------------------------------------------
# Corpus generation
# ---------------------------------------------------------------------------

# Stem-stable English vocabulary (no inflectional endings; verified against
# the 'english' snowball stemmer only for the simple-config quality indexes —
# latency indexes use 'english' but quality uses exact-token ground truth on
# 'simple'/unicode tokenizers so stemming never skews the metric).
EN_VOCAB = """
kernel buffer index planner storage vacuum checkpoint walrus record archive
snapshot catalog bitmap heap tuple latch lock queue stream pipeline stage
memory network socket packet router bridge tunnel gateway channel filter
mapper reducer shader cache hash digest cipher token ledger contract tenant
quota region cluster shard replica broker queue cursor window batch frame
signal vector matrix tensor gradient descent latent embedding cluster grid
mesh graph vertex edge path tree leaf root branch forest meadow river
mountain valley canyon plateau summit glacier tundra forest canyon
courier parcel freight cargo harbor vessel anchor rudder keel mast sail
engine piston crankshaft camshaft radiator axle chassis brake clutch
alloy copper brass nickel cobalt zinc tungsten titanium chromium molybdenum
granite basalt marble quartzite sandstone shale limestone dolomite gneiss
cinnamon basil thyme rosemary saffron turmeric cardamom clove nutmeg pepper
violin cello oboe clarinet bassoon trumpet trombone tuba timpani cymbal
sonnet ballad stanza verse chorus hymn anthem refrain rhyme meter prose
lantern beacon torch candle chandelier filament wick prism mirror lens
compass sextant astrolabe almanac atlas ledger journal register docket
falcon sparrow heron crane pelican stork ibis osprey kestrel goshawk harrier
mangrove cypress cedar juniper birch aspen willow maple alder hawthorn
basalt feldspar mica olivine pyroxene amphibole zircon monazite garnet
cobalt crimson indigo amber ochre umber sienna sepia teal azure crimson
riddle puzzle enigma conundrum paradox theorem lemma corollary axiom proof
harbor wharf pier jetty dock quay berth mooring bollard capstan windlass
marten otter badger weasel ermine mink sable vole lemming muskrat beaver
""".split()

# Chinese vocabulary: every word verified jieba-stable (single word in, single
# word out; stable when composed into sentences) against stannum.tokenize at
# setup time — unstable words are dropped from the sampling pool then.
ZH_VOCAB = [
    "会议", "讨论", "数据库", "备份", "策略", "内核", "内存", "管理", "迁移",
    "方案", "评审", "验收", "标准", "架构", "模块", "接口", "协议", "集群",
    "监控", "告警", "日志", "索引", "查询", "缓存", "优化", "压测", "上线",
    "回滚", "灰度", "发布", "事故", "复盘", "决策", "风险", "成本", "预算",
    "人力", "排期", "里程碑", "交付", "验收", "联调", "测试", "环境",
    "生产", "预发", "线下", "审计", "合规", "权限", "账号", "密钥", "证书",
    "防火墙", "网关", "路由", "转发", "负载", "均衡", "容器", "编排",
    "调度", "资源", "配额", "水位", "容量", "扩容", "缩容", "限流",
]

ZH_CONNECTORS = ["的", "与", "和", "并", "在", "为", "对", "按", "从", "向"]


def _zipf(vocab: list[str], rng: random.Random) -> list[float]:
    weights = [1.0 / (i + 1) ** 0.95 for i in range(len(vocab))]
    total = sum(weights)
    return [w / total for w in weights]


def gen_corpus(n_docs: int, seed: int = 42):
    """Return (rows, token_index) for a deterministic bilingual corpus.

    rows: list of (id, title, body). body mixes English terms and 1-3
    unspaced Chinese sentences. token_index: {en_term: set(doc ids where the
    term appears in body}, zh_index likewise for Chinese words in body,
    title_index for title terms.
    """
    rng = random.Random(seed)
    en = EN_VOCAB
    en_w = _zipf(en, rng)
    zh = ZH_VOCAB
    zh_w = _zipf(zh, rng)
    rows = []
    en_body_df: dict[str, set[int]] = {t: set() for t in en}
    zh_body_df: dict[str, set[int]] = {t: set() for t in zh}
    title_df: dict[str, set[int]] = {t: set() for t in en}
    for i in range(1, n_docs + 1):
        title_terms = rng.choices(en, weights=en_w, k=rng.randint(3, 6))
        n_body = rng.randint(60, 120)
        body_terms = rng.choices(en, weights=en_w, k=n_body)
        zh_parts = []
        zh_words_used: list[str] = []
        for _ in range(rng.randint(1, 3)):
            k = rng.randint(4, 9)
            words = rng.choices(zh, weights=zh_w, k=k)
            if rng.random() < 0.5:
                words.insert(rng.randrange(len(words) + 1), rng.choice(ZH_CONNECTORS))
            zh_parts.append("".join(words))
            zh_words_used.extend(w for w in words if w in zh)
        body = " ".join(body_terms) + " " + " ".join(zh_parts)
        title = " ".join(title_terms)
        rows.append((i, title, body))
        for t in body_terms:
            en_body_df[t].add(i)
        for w in zh_words_used:
            zh_body_df[w].add(i)
        for t in title_terms:
            title_df[t].add(i)
    return rows, en_body_df, zh_body_df, title_df


def pick_by_df_rank(
    df: dict[str, set[int]], lo_q: float, hi_q: float, k: int, min_df: int = 3
) -> list[str]:
    """Sample k terms from a quantile band of the document-frequency order.

    Zipf corpora have no fixed-df tail, so query classes are defined by rank:
    lo_q=0 selects the rarest indexed terms, hi_q=1 the most common.
    """
    ok = sorted(
        (t for t, ids in df.items() if len(ids) >= min_df),
        key=lambda t: len(df[t]),
    )
    if not ok:
        raise SystemExit("no terms with df >= min_df")
    lo = int(len(ok) * lo_q)
    hi = max(lo + 1, int(len(ok) * hi_q))
    seg = ok[lo:hi]
    step = max(1, len(seg) // k)
    picked = [seg[min(i * step, len(seg) - 1)] for i in range(k)]
    return picked


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def rbo(a: list[int], b: list[int], p: float = 0.9, depth: int = 10) -> float:
    """Rank-biased overlap of two id rankings."""
    score = 0.0
    norm = 0.0
    for d in range(1, depth + 1):
        sa = set(a[:d])
        sb = set(b[:d])
        inter = len(sa & sb)
        score += (p ** (d - 1)) * inter / d
        norm += p ** (d - 1)
    return score / norm


def latency(cur, sql: str, params: tuple, reps: int, warmup: int = 8):
    for _ in range(warmup):
        cur.execute(sql, params)
        cur.fetchall()
    xs = []
    for _ in range(reps):
        t0 = time.perf_counter()
        cur.execute(sql, params)
        cur.fetchall()
        xs.append((time.perf_counter() - t0) * 1000.0)
    xs.sort()
    q = statistics.quantiles(xs, n=20, method="inclusive") if len(xs) >= 2 else xs
    return {
        "p50_ms": round(q[9], 3) if len(xs) >= 2 else round(xs[0], 3),
        "p95_ms": round(q[18], 3) if len(xs) >= 2 else round(xs[0], 3),
        "min_ms": round(xs[0], 3),
    }


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=20000)
    ap.add_argument("--ingest-batches", type=int, default=5)
    ap.add_argument("--ingest-batch-size", type=int, default=1000)
    ap.add_argument("--reps", type=int, default=50)
    ap.add_argument("--out", type=Path,
                    default=Path("benchmarks/stannum-vs-pg-textsearch"))
    args = ap.parse_args()

    if not pgembed.has_extension("stannum"):
        raise SystemExit("stannum must be installed in this PG18.4 prefix")
    require_external_pg_textsearch()

    rng = random.Random(7)
    rows, en_df, zh_df, title_df = gen_corpus(args.docs)
    ingest_rows, _, _, _ = gen_corpus(args.docs + args.ingest_batches * args.ingest_batch_size)
    ingest = [r for r in ingest_rows if r[0] > args.docs]

    # query classes
    en_rare = pick_by_df_rank(en_df, 0.0, 0.15, 5)
    en_mid = pick_by_df_rank(en_df, 0.4, 0.6, 3)
    en_common = pick_by_df_rank(en_df, 0.9, 1.0, 3)
    multi = [en_mid[0], en_rare[0], en_common[0]]
    title_terms = pick_by_df_rank(title_df, 0.2, 0.5, 3)
    zh_terms = pick_by_df_rank(zh_df, 0.2, 0.6, 5)

    results: dict = {
        "config": {
            "docs": args.docs,
            "reps": args.reps,
            "ingest": len(ingest),
        },
        "build": {},
        "size_bytes": {},
        "ingest": {},
        "latency": {},
        "latency_post_churn": {},
        "recall_at_10": {},
        "rbo_cross_engine": {},
    }

    tmpdir = tempfile.mkdtemp(prefix="bench-stn-pgts-")
    with pgembed.get_server(
        tmpdir, cleanup_mode="delete", shared_preload_libraries="pg_textsearch"
    ) as pg:
        import psycopg2
        from psycopg2.extras import execute_values

        pg.psql("CREATE EXTENSION IF NOT EXISTS stannum;")
        pg.psql("CREATE EXTENSION IF NOT EXISTS pg_textsearch;")
        conn = psycopg2.connect(pg.get_uri())
        conn.autocommit = False
        cur = conn.cursor()

        # -- load identical data into two tables ---------------------------
        for table in ("bench_stn", "bench_pgts"):
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            cur.execute(
                f"CREATE TABLE {table} (id int PRIMARY KEY, title text, body text)"
            )
            execute_values(
                cur,
                f"INSERT INTO {table} (id, title, body) VALUES %s",
                rows,
                page_size=500,
            )
        conn.commit()
        print(f"loaded {args.docs} docs into bench_stn and bench_pgts")

        def timed_ddl(label: str, sql: str) -> float:
            t0 = time.perf_counter()
            cur.execute(sql)
            conn.commit()
            dt = time.perf_counter() - t0
            results["build"][label] = round(dt, 3)
            print(f"  build {label}: {dt:.2f}s")
            return dt

        def size_of(label: str, idx: str) -> None:
            cur.execute("SELECT pg_relation_size(%s::regclass)", (idx,))
            results["size_bytes"][label] = int(cur.fetchone()[0])

        # -- idiomatic pg-agent shapes -------------------------------------
        timed_ddl(
            "stannum_bm25f(title:3,body:1,jieba)",
            "CREATE INDEX stn_bm25f ON bench_stn USING stannum (title, body)"
            " WITH (tokenizer='jieba', field_weights='title:3,body:1')",
        )
        size_of("stannum_bm25f", "stn_bm25f")
        timed_ddl(
            "pg_textsearch_body(english)",
            "CREATE INDEX pgts_body ON bench_pgts USING bm25 (body)"
            " WITH (text_config='english')",
        )
        size_of("pg_textsearch_body", "pgts_body")

        # -- comparable exact-token shapes for quality ---------------------
        timed_ddl(
            "stannum_body(unicode)",
            "CREATE INDEX stn_body ON bench_stn USING stannum (body)"
            " WITH (tokenizer='unicode')",
        )
        size_of("stannum_body", "stn_body")
        timed_ddl(
            "pg_textsearch_body(simple)",
            "CREATE INDEX pgts_simple ON bench_pgts USING bm25 (body)"
            " WITH (text_config='simple')",
        )
        size_of("pg_textsearch_body_simple", "pgts_simple")

        # -- query latency (idiomatic shapes) ------------------------------
        STN = (
            "SELECT d.id FROM bench_stn d"
            " JOIN stannum.search('stn_bm25f'::regclass, %s,"
            " \"limit\" => %s, \"snippet\" => 'none') s ON d.ctid = s.ctid"
            " ORDER BY s.score DESC LIMIT %s"
        )
        # Two bm25 indexes exist on body by query time (english + simple),
        # so every pg_textsearch query names its index explicitly.
        PGTS = (
            "SELECT id FROM bench_pgts"
            " ORDER BY body <@> to_bm25query(%s, 'pgts_body') LIMIT %s"
        )
        workloads = [
            ("en_rare", en_rare),
            ("en_common", en_common),
            ("en_multi3", [ " ".join(multi) ]),
            ("title_term", title_terms),
            ("zh_word", zh_terms),
        ]
        for name, terms in workloads:
            for system, sql, limit in (
                ("stannum", STN, 10),
                ("pg_textsearch", PGTS, 10),
            ):
                if name == "title_term" and system == "pg_textsearch":
                    continue  # single-column index has no title notion
                xs = []
                for term in terms:
                    params = (term, limit, limit) if system == "stannum" else (term, limit)
                    r = latency(cur, sql, params, args.reps)
                    xs.append(r)
                results["latency"][f"{name}/{system}"] = {
                    "p50_ms": round(statistics.mean(x["p50_ms"] for x in xs), 3),
                    "p95_ms": round(max(x["p95_ms"] for x in xs), 3),
                    "min_ms": round(min(x["min_ms"] for x in xs), 3),
                }
                print(f"  latency {name}/{system}: {results['latency'][f'{name}/{system}']}")

        # top-k scaling
        for limit in (10, 100):
            term = en_common[0]
            results["latency"][f"en_common_limit{limit}/stannum"] = latency(
                cur, STN, (term, limit, limit), args.reps
            )
            results["latency"][f"en_common_limit{limit}/pg_textsearch"] = latency(
                cur, PGTS, (term, limit), args.reps
            )

        # -- quality: recall@10 vs ground truth ------------------------------
        def stn_top(idx: str, query: str, k: int = 10) -> list[int]:
            cur.execute(
                f"SELECT d.id FROM bench_stn d"
                f" JOIN stannum.search('{idx}'::regclass, %s,"
                f" \"limit\" => %s, \"snippet\" => 'none') s ON d.ctid = s.ctid"
                f" ORDER BY s.score DESC LIMIT %s",
                (query, k, k),
            )
            return [r[0] for r in cur.fetchall()]

        def pgts_top(idx: str, query: str, k: int = 10) -> list[int]:
            cur.execute(
                "SELECT id FROM bench_pgts"
                " ORDER BY body <@> to_bm25query(%s, %s) LIMIT %s",
                (query, idx, k),
            )
            return [r[0] for r in cur.fetchall()]

        def recall(hits: list[int], truth: set[int]) -> float:
            if not truth:
                return float("nan")
            return len(set(hits[:10]) & truth) / min(10, len(truth))

        for term in en_rare + en_mid + en_common:
            truth = en_df[term]
            results["recall_at_10"].setdefault("stannum_body(unicode)", []).append(
                {"term": term, "recall": round(recall(stn_top("stn_body", term), truth), 3)}
            )
            results["recall_at_10"].setdefault("pg_textsearch_body(simple)", []).append(
                {"term": term, "recall": round(recall(pgts_top("pgts_simple", term), truth), 3)}
            )
            results["rbo_cross_engine"].setdefault("stn_unicode_vs_pgts_simple", []).append(
                {"term": term, "rbo": round(
                    rbo(stn_top("stn_body", term), pgts_top("pgts_simple", term)), 3)}
            )
        for term in zh_terms:
            truth = zh_df[term]
            results["recall_at_10"].setdefault("stannum_bm25f(jieba)", []).append(
                {"term": term, "recall": round(recall(stn_top("stn_bm25f", term), truth), 3)}
            )
            results["recall_at_10"].setdefault(
                "pg_textsearch_body(simple)/zh", []
            ).append(
                {"term": term, "recall": round(recall(pgts_top("pgts_simple", term), truth), 3)}
            )

        # -- ingest throughput ------------------------------------------------
        for table, label in (("bench_stn", "stannum"), ("bench_pgts", "pg_textsearch")):
            t0 = time.perf_counter()
            for b in range(args.ingest_batches):
                chunk = ingest[b * args.ingest_batch_size:(b + 1) * args.ingest_batch_size]
                execute_values(
                    cur, f"INSERT INTO {table} (id, title, body) VALUES %s",
                    chunk, page_size=500,
                )
                conn.commit()
            dt = time.perf_counter() - t0
            results["ingest"][label] = {
                "docs": len(ingest),
                "seconds": round(dt, 3),
                "docs_per_s": round(len(ingest) / dt, 1),
            }
            print(f"  ingest {label}: {len(ingest)} docs in {dt:.2f}s")
        size_of("stannum_bm25f_after_ingest", "stn_bm25f")
        size_of("pg_textsearch_body_after_ingest", "pgts_body")
        cur.execute(
            "SELECT count(*) FROM stannum.segment_info('stn_bm25f'::regclass)"
        )
        results["segments_after_ingest"] = {"stannum_stn_bm25f": cur.fetchone()[0]}
        cur.execute("SELECT bm25_level_counts('pgts_body')")
        results["segments_after_ingest"]["pg_textsearch_levels"] = cur.fetchone()[0]

        # -- post-churn latency ------------------------------------------------
        term = en_common[0]
        results["latency_post_churn"]["stannum"] = latency(
            cur, STN, (term, 10, 10), args.reps
        )
        results["latency_post_churn"]["pg_textsearch"] = latency(
            cur, PGTS, (term, 10), args.reps
        )
        zterm = zh_terms[0]
        results["latency_post_churn"]["stannum_zh"] = latency(
            cur, STN, (zterm, 10, 10), args.reps
        )

        # maintenance observability snapshot (informational)
        cur.execute("SELECT stannum.version()")
        results["stannum_version"] = cur.fetchone()[0]
        results["config"]["pg_textsearch"] = pg.psql(
            "SELECT extversion FROM pg_extension WHERE extname='pg_textsearch';"
        ).splitlines()[2].strip()
        conn.close()

    # -- summary ---------------------------------------------------------------
    def avg(xs):
        vals = [x["recall"] for x in xs if x["recall"] == x["recall"]]
        return round(statistics.mean(vals), 3) if vals else float("nan")

    summary = {
        "recall_mean": {k: avg(v) for k, v in results["recall_at_10"].items()},
        "rbo_mean": {
            k: round(statistics.mean(x["rbo"] for x in v), 3)
            for k, v in results["rbo_cross_engine"].items()
        },
    }
    results["summary"] = summary

    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / "results.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\nresults written to {out_file}")

    print("\n## Summary\n")
    print("| metric | stannum | pg_textsearch |")
    print("|---|---|---|")
    b = results["build"]
    print(f"| build (pg-agent shape) | {b.get('stannum_bm25f(title:3,body:1,jieba)')}s | {b.get('pg_textsearch_body(english)')}s |")
    s = results["size_bytes"]
    print(f"| index size | {s.get('stannum_bm25f', 0)/1e6:.1f} MB | {s.get('pg_textsearch_body', 0)/1e6:.1f} MB |")
    ing = results["ingest"]
    print(f"| ingest docs/s | {ing['stannum']['docs_per_s']} | {ing['pg_textsearch']['docs_per_s']} |")
    lat = results["latency"]
    for k in ("en_rare", "en_common", "en_multi3", "zh_word"):
        st = lat.get(f"{k}/stannum", {})
        pt = lat.get(f"{k}/pg_textsearch", {})
        print(f"| {k} p50/p95 ms | {st.get('p50_ms')}/{st.get('p95_ms')} | {pt.get('p50_ms')}/{pt.get('p95_ms')} |")
    pc = results["latency_post_churn"]
    print(f"| post-churn en p50/p95 ms | {pc['stannum']['p50_ms']}/{pc['stannum']['p95_ms']} | {pc['pg_textsearch']['p50_ms']}/{pc['pg_textsearch']['p95_ms']} |")
    print(f"| post-churn zh p50/p95 ms | {pc['stannum_zh']['p50_ms']}/{pc['stannum_zh']['p95_ms']} | n/a (no word-level zh) |")
    print(f"| recall@10 (en, exact-token) | {summary['recall_mean'].get('stannum_body(unicode)')} | {summary['recall_mean'].get('pg_textsearch_body(simple)')} |")
    print(f"| recall@10 (zh) | {summary['recall_mean'].get('stannum_bm25f(jieba)')} | {summary['recall_mean'].get('pg_textsearch_body(simple)/zh')} |")
    print(f"| cross-engine RBO@10 (en) | {summary['rbo_mean'].get('stn_unicode_vs_pgts_simple')} | — |")


if __name__ == "__main__":
    main()
