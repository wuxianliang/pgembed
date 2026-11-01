"""Functional comparison: stannum vs pg_textsearch (timescale/pg_textsearch).

pg-agent v13 (~/Projects/pg-agent) is stannum's primary downstream consumer:
its production corpus is bilingual (unspaced Chinese transcripts/decisions)
and it relies on ``tokenizer=jieba``, multi-column ``field_weights``, phrase
queries, ``highlight``, and the agent-facing tool layer (``verify_index``,
``index_stats``, ``segment_info``, ``bind_query``, ...).  These tests probe
each capability axis on both engines in the same server and assert the
behavior that decides fitness for pg-agent v13.

pg_textsearch requires ``shared_preload_libraries``; the fixture server sets
it.  stannum needs no preload (it loads via CREATE EXTENSION in the same
server).

Every assertion reflects verified behavior of the pinned builds:
pg_textsearch 1.5.1 and stannum stn3 (see the benchmark report for exact
commits).  All shared tables are created by the module ``cmp_server`` fixture
so each test is order-independent.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterator

import pytest

import pgembed

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from bench_stannum_vs_pg_textsearch import (  # noqa: E402
    PG_TEXTSEARCH_MISSING_MESSAGE,
    pg_textsearch_is_provisioned,
)

# jieba segmentations verified against the installed tokenizer:
#   会议讨论了数据库的备份策略 -> 会议/讨论/了/数据库/的/备份/策略
#   数据库迁移方案的评审     -> 数据库/迁移/方案/的/评审
#   数据库系统的重要决策     -> 数据库系统/的/重要/决策   (merged by the dict)

CORPUS_SQL = """
DROP TABLE IF EXISTS en_docs, zh_docs, zh_pgts, fw, ph, bs, stem, blob CASCADE;

CREATE TABLE en_docs (id int PRIMARY KEY, body text);
INSERT INTO en_docs VALUES
 (1, 'postgres kernel memory buffer checkpoint internals'),
 (2, 'postgres kernel buffer manager'),
 (3, 'vacuum freeze and the transaction id wraparound'),
 (4, 'kernel scheduler notes');
CREATE INDEX en_docs_stannum_idx ON en_docs USING stannum (body)
 WITH (tokenizer='unicode');
CREATE INDEX en_docs_pgts_idx ON en_docs USING bm25 (body)
 WITH (text_config='english');

CREATE TABLE zh_docs (id int PRIMARY KEY, body text);
INSERT INTO zh_docs VALUES
 (1, '会议讨论了数据库的备份策略'),
 (2, '内核的内存管理'),
 (3, '数据库迁移方案的评审');
CREATE INDEX zh_docs_stannum_idx ON zh_docs USING stannum (body)
 WITH (tokenizer='jieba');

CREATE TABLE zh_pgts (id int PRIMARY KEY, body text);
INSERT INTO zh_pgts VALUES
 (1, '会议讨论了数据库的备份策略'),
 (2, '内核的内存管理');
CREATE INDEX zh_pgts_idx ON zh_pgts USING bm25 (body)
 WITH (text_config='simple');

CREATE TABLE fw (id int PRIMARY KEY, title text, body text);
INSERT INTO fw VALUES
 (1, '例行周报', '本周讨论了数据库的备份策略'),
 (2, '数据库迁移方案', '详见附录');
CREATE INDEX fw_title_idx ON fw USING stannum (title, body)
 WITH (tokenizer='jieba', field_weights='title:5,body:1');
CREATE INDEX fw_body_idx ON fw USING stannum (title, body)
 WITH (tokenizer='jieba', field_weights='title:1,body:5');

CREATE TABLE ph (id int PRIMARY KEY, body text);
INSERT INTO ph VALUES
 (1, 'postgres index internals'),
 (2, 'index postgres internals'),
 (3, 'postgres has deep internals');
CREATE INDEX ph_stannum_idx ON ph USING stannum (body)
 WITH (tokenizer='unicode');
CREATE INDEX ph_pgts_idx ON ph USING bm25 (body)
 WITH (text_config='english');

CREATE TABLE bs (id int PRIMARY KEY, body text);
INSERT INTO bs VALUES
 (1, 'postgres index internals'),
 (2, 'index postgres internals'),
 (3, 'postgres deep storage notes'),
 (4, 'postgres postgres postgres postgres postgres'),
 (5, 'internals internals internals'),
 (6, 'postgres vacuum'),
 (7, 'postgres recovery');
CREATE INDEX bs_stannum_idx ON bs USING stannum (body)
 WITH (tokenizer='unicode');

CREATE TABLE stem (id int PRIMARY KEY, body text);
INSERT INTO stem VALUES (1, 'running vacuum and wrapping');
CREATE INDEX stem_stannum_idx ON stem USING stannum (body)
 WITH (tokenizer='unicode');
CREATE INDEX stem_pgts_idx ON stem USING bm25 (body)
 WITH (text_config='english');
"""


@pytest.fixture(scope="module")
def cmp_server() -> Iterator[pgembed.PostgresServer]:
    if not pgembed.has_extension("stannum"):
        pytest.skip("stannum is not installed in this build")
    if not pg_textsearch_is_provisioned():
        pytest.skip(PG_TEXTSEARCH_MISSING_MESSAGE)
    import tempfile

    tmpdir = tempfile.mkdtemp()
    with pgembed.get_server(
        tmpdir, cleanup_mode="delete", shared_preload_libraries="pg_textsearch"
    ) as pg:
        pg.psql("CREATE EXTENSION IF NOT EXISTS stannum;")
        pg.psql("CREATE EXTENSION IF NOT EXISTS pg_textsearch;")
        pg.psql(CORPUS_SQL)
        yield pg


def rows(pg: pgembed.PostgresServer, sql: str) -> list[list[str]]:
    """Parse a psql result body into fields (headers/row-count stripped)."""
    lines = [ln for ln in pg.psql(sql).splitlines() if ln.strip()]
    body = lines[2:]  # header + underline
    out: list[list[str]] = []
    for ln in body:
        if re.match(r"^\(\d+ rows?\)$", ln.strip()):
            continue
        out.append([f.strip() for f in ln.split("|")])
    return out


def scalar(pg: pgembed.PostgresServer, sql: str) -> str:
    return rows(pg, sql)[0][0]


def raises(pg: pgembed.PostgresServer, sql: str) -> str:
    """Run sql via psycopg2 and return the server's error message.

    ``psql()``'s CalledProcessError carries no stderr text, so the direct
    connection is the honest way to read the actual error.
    """
    import psycopg2

    conn = psycopg2.connect(pg.get_uri())
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    except psycopg2.errors.DatabaseError as exc:
        return str(exc).strip()
    finally:
        conn.close()
    raise AssertionError(f"expected an error, query succeeded: {sql}")


# -- 1. deployment and versions ---------------------------------------------


def test_versions_and_deployment_constraints(cmp_server) -> None:
    pg = cmp_server
    # pg_textsearch must be preloaded; stannum is dynamically loaded and is
    # NOT in shared_preload_libraries.
    assert "pg_textsearch" in scalar(pg, "show shared_preload_libraries;")
    assert "stannum" not in scalar(pg, "show shared_preload_libraries;")
    assert scalar(
        pg, "SELECT extversion FROM pg_extension WHERE extname='pg_textsearch';"
    ).startswith("1.5")
    assert scalar(
        pg, "SELECT extversion FROM pg_extension WHERE extname='stannum';"
    ).startswith("0.5")
    # stannum reports its own build identity (agent-facing version surface).
    assert re.match(r"^\d+\.\d+", scalar(pg, "SELECT stannum.version();"))


# -- 2. English BM25 top-k on both engines -----------------------------------


def test_english_topk_ranking(cmp_server) -> None:
    pg = cmp_server
    # stannum: search() SRF ordered by BM25.  Doc 2 contains both query
    # terms and is the shortest of the matching docs.
    stn = rows(
        pg,
        "SELECT d.id FROM en_docs d"
        " JOIN stannum.search('en_docs_stannum_idx'::regclass,"
        " 'kernel buffer',\"limit\" => 3,\"snippet\" => 'none') s"
        " ON d.ctid=s.ctid ORDER BY s.score DESC, d.id;",
    )
    assert stn[0][0] == "2"
    # pg_textsearch: ORDER BY <@> (negative scores, ascending scan).
    pgt = rows(
        pg,
        "SELECT id FROM en_docs ORDER BY body <@> 'kernel buffer' LIMIT 3;",
    )
    assert pgt[0][0] == "2"


def test_query_plans(cmp_server) -> None:
    pg = cmp_server
    # stannum plans a Custom Scan over the stannum AM when the ==> predicate
    # cannot be served by a seq scan.
    plan = pg.psql(
        "SET enable_seqscan=off; EXPLAIN (COSTS OFF) SELECT id FROM en_docs"
        " WHERE body ==> 'kernel buffer' LIMIT 3;"
    )
    assert "Custom Scan (Stannum" in plan
    # pg_textsearch plans a bm25 index scan for the top-k shape.
    plan = pg.psql(
        "EXPLAIN (COSTS OFF) SELECT id FROM en_docs"
        " ORDER BY body <@> 'kernel buffer' LIMIT 3;"
    )
    assert "bm25" in plan


# -- 3. Chinese: unspaced word-level search ----------------------------------
#
# Natural Chinese text has no spaces.  PostgreSQL's built-in parsers treat a
# whole CJK run as one token, so pg_textsearch (any built-in text_config)
# cannot match individual words without an external parser such as zhparser
# (not bundled).  stannum's jieba tokenizer segments the run into words.


def test_stannum_jieba_segments_unspaced_chinese(cmp_server) -> None:
    pg = cmp_server
    tokens = rows(
        pg,
        "SELECT tokenize FROM stannum.tokenize("
        "'会议讨论了数据库的备份策略', 'jieba');",
    )
    flat = [t[0] for t in tokens]
    assert "数据库" in flat and "备份" in flat and "策略" in flat


def test_stannum_finds_chinese_word_in_unspaced_text(cmp_server) -> None:
    pg = cmp_server
    hits = rows(
        pg,
        "SELECT d.id FROM zh_docs d JOIN stannum.search("
        "'zh_docs_stannum_idx'::regclass, '数据库',"
        "\"limit\" => 5,\"snippet\" => 'none') s"
        " ON d.ctid=s.ctid ORDER BY d.id;",
    )
    assert {h[0] for h in hits} == {"1", "3"}


def test_pg_textsearch_cannot_match_chinese_word_without_parser(cmp_server) -> None:
    pg = cmp_server
    # The word '数据库' does not match: the parser emitted the whole CJK run
    # as one lexeme, so every score is the standalone 0 (no hit).
    scored = rows(
        pg,
        "SELECT id, (body <@> '数据库')::text FROM zh_pgts ORDER BY id;",
    )
    assert scored and all(r[1] == "0" for r in scored)
    # Only the exact full run scores — useless for retrieval.
    full = rows(
        pg,
        "SELECT id, (body <@> '会议讨论了数据库的备份策略')::text"
        " FROM zh_pgts ORDER BY id;",
    )
    assert full[0][1].startswith("-") and full[1][1] == "0"
    # And no word-level Chinese parser ships in the bundle.
    assert "zhparser" not in pg.psql("SELECT cfgname FROM pg_ts_config;")


# -- 4. Multi-column BM25F field weights --------------------------------------


def test_stannum_field_weights_reorder_hits(cmp_server) -> None:
    pg = cmp_server
    # Same term in body (doc 1) vs title (doc 2); the weighting decides rank.
    top_title = rows(
        pg,
        "SELECT d.id FROM fw d JOIN stannum.search('fw_title_idx'::regclass,"
        "'数据库',\"limit\" => 2,\"snippet\" => 'none') s ON d.ctid=s.ctid"
        " ORDER BY s.score DESC, d.id;",
    )
    assert top_title[0][0] == "2"  # title hit outranks body hit at 5:1
    top_body = rows(
        pg,
        "SELECT d.id FROM fw d JOIN stannum.search('fw_body_idx'::regclass,"
        "'数据库',\"limit\" => 2,\"snippet\" => 'none') s ON d.ctid=s.ctid"
        " ORDER BY s.score DESC, d.id;",
    )
    assert top_body[0][0] == "1"  # body hit outranks title hit at 1:5


def test_pg_textsearch_has_no_multicolumn_or_field_weights(cmp_server) -> None:
    pg = cmp_server
    err = raises(
        pg,
        "CREATE INDEX fw_pgts_bad ON fw USING bm25 (title, body)"
        " WITH (text_config='simple');",
    )
    assert "column" in err.lower()
    # An expression index over concatenated columns is the documented
    # workaround: it has no per-field weights.
    err = raises(
        pg,
        "CREATE INDEX fw_pgts_w ON fw USING bm25 ((title || ' ' || body))"
        " WITH (text_config='simple', field_weights='title:3');",
    )
    assert "field_weights" in err.lower()


# -- 5. Phrases and word order -------------------------------------------------
#
# pg_textsearch's <@> is bag-of-words: word order is invisible to the score.
# Phrases need a separate @@ to_tsquery filter, which PostgreSQL evaluates
# outside the BM25 index scan.  stannum TINQL ranks with the phrase constraint
# inside the index query.


def test_stannum_phrase_is_order_and_adjacency_sensitive(cmp_server) -> None:
    pg = cmp_server
    hits = rows(
        pg,
        "SELECT d.id FROM ph d JOIN stannum.search('ph_stannum_idx'::regclass,"
        "'\"postgres index\"',\"limit\" => 5,\"snippet\" => 'none') s"
        " ON d.ctid=s.ctid ORDER BY d.id;",
    )
    assert {h[0] for h in hits} == {"1"}


def test_pg_textsearch_scores_word_orders_identically(cmp_server) -> None:
    pg = cmp_server
    scored = rows(
        pg,
        "SELECT id, (body <@> 'postgres index')::text FROM ph ORDER BY id;",
    )
    by_id = {r[0]: r[1] for r in scored}
    assert by_id["1"] == by_id["2"]  # identical bags of words, identical score
    assert by_id["1"].startswith("-")
    # The phrase workaround works as a *filter* (correct result set) ...
    filtered = rows(
        pg,
        "SELECT id FROM ph WHERE body @@ to_tsquery('english',"
        " 'postgres <-> index') ORDER BY id;",
    )
    assert [r[0] for r in filtered] == ["1"]
    # ... but it is not part of the ranked index scan (README: "not yet
    # optimized as a single index scan").
    plan = pg.psql(
        "EXPLAIN (COSTS OFF) SELECT id FROM ph"
        " WHERE body @@ to_tsquery('english','postgres <-> index')"
        " ORDER BY body <@> 'postgres index' LIMIT 5;"
    )
    assert "Sort" in plan  # filter-then-score-then-sort shape


# -- 6. Boolean and boost semantics -------------------------------------------


def test_stannum_boolean_not_and_boost(cmp_server) -> None:
    pg = cmp_server
    hits = rows(
        pg,
        "SELECT d.id FROM ph d JOIN stannum.search('ph_stannum_idx'::regclass,"
        "'internals AND NOT index',\"limit\" => 5,\"snippet\" => 'none') s"
        " ON d.ctid=s.ctid ORDER BY d.id;",
    )
    assert {h[0] for h in hits} == {"3"}
    # 'internals' (df=3) is rarer than 'postgres' (df=6): unboosted, the
    # internals-heavy doc 5 wins on IDF; a large postgres boost flips the
    # winner to doc 4 without changing the match set (OR keeps the union).
    plain = [
        r[0]
        for r in rows(
            pg,
            "SELECT d.id FROM bs d JOIN stannum.search("
            "'bs_stannum_idx'::regclass, 'postgres OR internals',"
            "\"limit\" => 7,\"snippet\" => 'none') s"
            " ON d.ctid=s.ctid ORDER BY s.score DESC, d.id;",
        )
    ]
    boosted = [
        r[0]
        for r in rows(
            pg,
            "SELECT d.id FROM bs d JOIN stannum.search("
            "'bs_stannum_idx'::regclass, 'postgres^50 OR internals',"
            "\"limit\" => 7,\"snippet\" => 'none') s"
            " ON d.ctid=s.ctid ORDER BY s.score DESC, d.id;",
        )
    ]
    assert plain[0] == "5"
    assert boosted[0] == "4"
    assert sorted(plain) == sorted(boosted)


def test_pg_textsearch_ranking_cannot_exclude_terms(cmp_server) -> None:
    pg = cmp_server
    # <@> has no NOT: ranking alone still returns docs containing 'index'.
    scored = rows(
        pg,
        "SELECT id FROM ph ORDER BY body <@> 'internals' LIMIT 5;",
    )
    assert {r[0] for r in scored} >= {"1", "3"}
    # Exclusion requires the @@ filter path.
    filtered = rows(
        pg,
        "SELECT id FROM ph WHERE body @@ to_tsquery('english',"
        " 'internals & !index') ORDER BY id;",
    )
    assert [r[0] for r in filtered] == ["3"]


# -- 7. Highlighting and snippets ----------------------------------------------


def test_stannum_snippets_and_highlight(cmp_server) -> None:
    pg = cmp_server
    hits = rows(
        pg,
        "SELECT d.id, s.snippet FROM zh_docs d JOIN stannum.search("
        "'zh_docs_stannum_idx'::regclass, '数据库',"
        "\"limit\" => 5,\"snippet\" => 'html') s"
        " ON d.ctid=s.ctid ORDER BY d.id;",
    )
    assert hits and all("<mark>" in h[1] for h in hits)
    hl = scalar(
        pg,
        "SELECT stannum.highlight('postgres kernel internals',"
        " '<b>', '</b>', 'kernel');",
    )
    assert "<b>kernel</b>" in hl


def test_pg_textsearch_has_no_highlight(cmp_server) -> None:
    pg = cmp_server
    # No snippet/highlight surface in the extension's SQL.
    n = scalar(
        pg,
        "SELECT count(*) FROM pg_proc p JOIN pg_depend d ON d.objid=p.oid"
        " JOIN pg_extension e ON e.oid=d.refobjid"
        " WHERE e.extname='pg_textsearch' AND p.proname ~ 'highlight|snippet';",
    )
    assert n == "0"
    # Core ts_headline can highlight English lexemes ...
    hl = scalar(
        pg,
        "SELECT ts_headline('english', 'the database kernel crash',"
        " to_tsquery('english','database'));",
    )
    assert "<b>" in hl  # default StartSel/EndSel
    # ... but with no Chinese word parser it cannot see inside a CJK run:
    # the whole run is one lexeme, so a word query highlights nothing.
    zh = scalar(
        pg,
        "SELECT ts_headline('simple', '会议讨论了数据库的备份策略',"
        " to_tsquery('simple','数据库'));",
    )
    assert zh == "会议讨论了数据库的备份策略"


# -- 8. Agent tool layer inventory ----------------------------------------------
#
# pg-agent v13 greps call: ==> (78), tokenize, score_bound(_indexed),
# verify_index, full_score, highlight, segment_info, index_stats,
# bind_query, indexed_query, search, search_count.


STANNUM_AGENT_FACES = (
    "tokenize",
    "verify_index",
    "index_stats",
    "index_analysis",
    "segment_info",
    "search",
    "search_count",
    "bind_query",
    "indexed_query_in",
    "indexed_query_out",
    "score_bound",
    "score_bound_indexed",
    "full_score",
    "max_score",
    "ql_parse",
    "capabilities",
    "highlight",
)


def test_stannum_agent_tool_layer_present_and_callable(cmp_server) -> None:
    pg = cmp_server
    present = {
        r[0]
        for r in rows(
            pg,
            "SELECT p.proname FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid=p.pronamespace"
            " WHERE n.nspname='stannum';",
        )
    }
    missing = [f for f in STANNUM_AGENT_FACES if f not in present]
    assert not missing, f"stannum tool layer missing: {missing}"
    # Spot-check the diagnostics the agent relies on: verify_index returns
    # a findings table (severity, location, message) — empty means healthy.
    findings = rows(
        pg,
        "SELECT severity, message FROM stannum.verify_index("
        "'en_docs_stannum_idx'::regclass);",
    )
    assert all(r[0] != "error" for r in findings)
    stats = scalar(
        pg,
        "SELECT s::text FROM stannum.index_stats("
        "'en_docs_stannum_idx'::regclass) s",
    )
    assert stats  # composite renders non-empty
    segs = rows(
        pg,
        "SELECT count(*) FROM stannum.segment_info("
        "'en_docs_stannum_idx'::regclass);",
    )
    assert int(segs[0][0]) >= 1
    # bind_query/ql_parse surface: parse-only validation is agent-testable.
    parsed = scalar(pg, "SELECT stannum.ql_parse('kernel AND buffer');")
    assert parsed and "ERROR" not in parsed.upper()


def test_pg_textsearch_surface_is_indexer_oriented(cmp_server) -> None:
    pg = cmp_server
    present = {
        r[0]
        for r in rows(
            pg,
            "SELECT p.proname FROM pg_proc p"
            " JOIN pg_depend d ON d.objid=p.oid"
            " JOIN pg_extension e ON e.oid=d.refobjid"
            " WHERE e.extname='pg_textsearch';",
        )
    }
    # Maintenance/LSM observability exists...
    assert {"bm25_level_counts", "bm25_needs_compaction"} <= present
    assert "bm25_compact" in present
    # ...while agent-facing retrieval diagnostics do not.
    missing = [
        f
        for f in ("tokenize", "verify_index", "segment_info", "bind_query")
        if f not in present
    ]
    assert missing == ["tokenize", "verify_index", "segment_info", "bind_query"]
    levels = scalar(pg, "SELECT bm25_level_counts('en_docs_pgts_idx'::regclass);")
    assert levels.startswith("{")


# -- 9. Write visibility and segment growth -------------------------------------


def test_post_build_writes_are_visible_and_segments_grow(cmp_server) -> None:
    pg = cmp_server
    try:
        before = int(
            rows(
                pg,
                "SELECT count(*) FROM stannum.segment_info("
                "'en_docs_stannum_idx'::regclass);",
            )[0][0]
        )
        assert scalar(
            pg,
            "SELECT stannum.search_count('en_docs_stannum_idx'::regclass,"
            " 'wraparound');",
        ) == "1"
        pg.psql(
            "INSERT INTO en_docs VALUES"
            " (5, 'transaction id wraparound protection'),"
            " (6, 'kernel panic notes');"
        )
        assert scalar(
            pg,
            "SELECT stannum.search_count('en_docs_stannum_idx'::regclass,"
            " 'wraparound');",
        ) == "2"
        after = int(
            rows(
                pg,
                "SELECT count(*) FROM stannum.segment_info("
                "'en_docs_stannum_idx'::regclass);",
            )[0][0]
        )
        assert after > before  # a new segment appears for the post-build rows
        pg.psql("DELETE FROM en_docs WHERE id = 5;")
        assert scalar(
            pg,
            "SELECT stannum.search_count('en_docs_stannum_idx'::regclass,"
            " 'wraparound');",
        ) == "1"
    finally:
        pg.psql("DELETE FROM en_docs WHERE id IN (5, 6);")


def test_pg_textsearch_memtable_serves_post_build_writes(cmp_server) -> None:
    pg = cmp_server
    try:
        pg.psql("INSERT INTO en_docs VALUES (7, 'late arrival buffer contents');")
        found = rows(
            pg,
            "SELECT id FROM en_docs ORDER BY body <@> 'late arrival' LIMIT 3;",
        )
        assert found and found[0][0] == "7"  # memtable hit, no spill needed
        spilled = scalar(
            pg, "SELECT bm25_spill_index('en_docs_pgts_idx');"
        )
        assert spilled.isdigit()
        pg.psql("DELETE FROM en_docs WHERE id = 7;")
        after = rows(
            pg,
            "SELECT (body <@> 'late arrival')::text FROM en_docs WHERE id = 1;",
        )
        assert after and all(r[0] == "0" for r in after)
    finally:
        pg.psql("DELETE FROM en_docs WHERE id = 7;")


# -- 10. Tokenization defaults: stemming ----------------------------------------


def test_stemming_defaults_differ(cmp_server) -> None:
    pg = cmp_server
    # pg_textsearch matches across inflection (english snowball).
    pgt = rows(pg, "SELECT id FROM stem ORDER BY body <@> 'runs' LIMIT 3;")
    assert {r[0] for r in pgt} == {"1"}
    # stannum's unicode tokenizer is exact-match by default.
    stn = rows(
        pg,
        "SELECT d.id FROM stem d JOIN stannum.search("
        "'stem_stannum_idx'::regclass, 'runs',"
        "\"limit\" => 3,\"snippet\" => 'none') s ON d.ctid=s.ctid;",
    )
    assert not stn
    stn_exact = rows(
        pg,
        "SELECT d.id FROM stem d JOIN stannum.search("
        "'stem_stannum_idx'::regclass, 'running',"
        "\"limit\" => 3,\"snippet\" => 'none') s ON d.ctid=s.ctid;",
    )
    assert {r[0] for r in stn_exact} == {"1"}


# -- 11. Long tokens -------------------------------------------------------------
#
# pg-agent sets long_tokens='split' + max_token_bytes; agent corpora contain
# base64/hex/URL blobs.  PostgreSQL ignores lexemes longer than 2047 bytes.


def test_long_token_splitting_vs_dropping(cmp_server) -> None:
    pg = cmp_server
    blob = "a1b2" * 600  # 2400 hex chars > 2047
    short = "a1b2" * 300  # 1200 chars, kept by PG but split by stannum
    pg.psql(
        f"CREATE TABLE blob (id int PRIMARY KEY, body text);"
        f"INSERT INTO blob VALUES"
        f" (1, $b${blob}$b$),"
        f" (2, 'ordinary text with hex $b${short}$b$ inside');"
    )
    pg.psql(
        "CREATE INDEX blob_stannum_idx ON blob USING stannum (body)"
        " WITH (tokenizer='unicode', long_tokens='split',"
        " max_token_bytes='8');"
    )
    # stannum split both long runs into 8-byte chunks ('a1b2a1b2'), so the
    # interior of the blob is searchable.
    stn = rows(
        pg,
        "SELECT d.id FROM blob d JOIN stannum.search("
        "'blob_stannum_idx'::regclass, 'a1b2a1b2',"
        "\"limit\" => 3,\"snippet\" => 'none') s"
        " ON d.ctid=s.ctid ORDER BY d.id;",
    )
    assert {r[0] for r in stn} == {"1", "2"}
    # PostgreSQL kept the 1200-byte run as one lexeme (doc 2) and dropped the
    # 2400-byte one (doc 1): only substring prefixes of doc 2's run exist.
    vec = scalar(
        pg,
        "SELECT count(*) FROM ts_stat("
        " 'SELECT to_tsvector(''simple'', body) FROM blob')"
        " WHERE word LIKE 'a1b2%%';",
    )
    assert vec == "1"
