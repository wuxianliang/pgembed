"""stannum full-text search with the jieba tokenizer (mixed Chinese/English).

These tests exercise the `tokenizer = 'jieba'` index option end to end through
a bundled server: word-level Chinese queries, unchanged English behavior, the
`stannum.tokenize` UDF, highlighting, and BM25 scoring. The default `unicode`
tokenizer (per-character Han analysis) is exercised in parallel on a second
column to pin the behavioral difference: word boundaries respected under
jieba, character adjacency under the default.
"""

from __future__ import annotations

import tempfile
from typing import Iterator

import pytest

import pgembed

DOCS_SQL = """
CREATE TABLE docs (id int PRIMARY KEY, body_jieba text, body_default text);
INSERT INTO docs VALUES
 (1, 'PostgreSQL supports full text search',
     'PostgreSQL supports full text search'),
 (2, 'PostgreSQL 是一个强大的开源数据库',
     'PostgreSQL 是一个强大的开源数据库'),
 (3, 'PostgreSQL数据库内核与查询优化',
     'PostgreSQL数据库内核与查询优化'),
 (4, '中文分词 让数 据库更懂中文',
     '中文分词 让数 据库更懂中文'),
 (5, 'Database kernel and query optimization',
     'Database kernel and query optimization');
CREATE INDEX docs_jieba ON docs USING stannum (body_jieba)
  WITH (tokenizer = 'jieba');
CREATE INDEX docs_default ON docs USING stannum (body_default);
ANALYZE docs;
"""


@pytest.fixture
def stannum_server() -> Iterator[pgembed.PostgresServer]:
    if not pgembed.has_extension("stannum"):
        pytest.skip("stannum is not installed in this build")
    tmpdir = tempfile.mkdtemp()
    with pgembed.get_server(tmpdir, cleanup_mode="delete") as pg:
        pg.psql("CREATE EXTENSION stannum;")
        pg.psql(DOCS_SQL)
        yield pg


def scalar(pg: pgembed.PostgresServer, sql: str) -> str:
    """Single value of a one-row psql result (headers stripped)."""
    return pg.psql(sql).splitlines()[2].strip()


def matching_ids(pg: pgembed.PostgresServer, column: str, query: str) -> str:
    return scalar(
        pg,
        f"SELECT string_agg(id::text, ',' ORDER BY id) FROM docs"
        f" WHERE {column} ==> '{query}';",
    )


def test_tokenize_udf_accepts_jieba(stannum_server: pgembed.PostgresServer) -> None:
    # Word segmentation with case folding: dictionary words stay whole.
    assert scalar(stannum_server, """
        SELECT string_agg(tok, '/') FROM stannum.tokenize(
            'PostgreSQL 是开源数据库', tokenizer => 'jieba') AS t(tok);
    """) == "postgresql/是/开源/数据库"


def test_jieba_matches_chinese_words(stannum_server: pgembed.PostgresServer) -> None:
    # 数据库 is one dictionary word: rows 2 and 3 contain it as a word.
    assert matching_ids(stannum_server, "body_jieba", "数据库") == "2,3"


def test_jieba_respects_word_boundaries(stannum_server: pgembed.PostgresServer) -> None:
    # Row 4 has a space inside 数据库 (让数 据库). Word-level analysis does not
    # match it, while per-character analysis (default tokenizer) does.
    assert "4" not in matching_ids(stannum_server, "body_jieba", "数据库").split(",")
    assert matching_ids(stannum_server, "body_default", "数据库") == "2,3,4"


def test_jieba_word_sequence_queries_rewrite_to_phrases(
    stannum_server: pgembed.PostgresServer,
) -> None:
    # A query spanning two dictionary words matches their adjacent occurrence.
    assert matching_ids(stannum_server, "body_jieba", "开源数据库") == "2"


def test_jieba_english_queries_and_case_folding(
    stannum_server: pgembed.PostgresServer,
) -> None:
    assert matching_ids(stannum_server, "body_jieba", "search") == "1"
    assert matching_ids(stannum_server, "body_jieba", "postgresql") == "1,2,3"
    # A Chinese/English term spanning a word boundary matches the adjacent
    # occurrence in row 3 (PostgreSQL数据库内核...).
    assert matching_ids(stannum_server, "body_jieba", "PostgreSQL数据库") == "3"


def test_jieba_highlight_marks_whole_words(
    stannum_server: pgembed.PostgresServer,
) -> None:
    highlighted = scalar(stannum_server, """
        SELECT stannum.highlight(body_jieba, '<mark>', '</mark>',
                                 query => '数据库')
        FROM docs WHERE id = 2;
    """)
    assert "<mark>数据库</mark>" in highlighted


def test_jieba_full_score_ranks_matches(
    stannum_server: pgembed.PostgresServer,
) -> None:
    scored = stannum_server.psql("""
        SELECT id, stannum.full_score(ctid) AS score FROM docs
        WHERE body_jieba ==> '数据库' ORDER BY score DESC;
    """)
    lines = scored.splitlines()
    ids = {line.split()[0] for line in lines if line.strip()[:1].isdigit()}
    assert ids == {"2", "3"}
    for line in lines:
        if line.strip()[:1].isdigit():
            fields = [field for field in line.split() if field != "|"]
            assert float(fields[1]) > 0.0


def test_dictionary_drift_reindex_and_presets(
    stannum_server: pgembed.PostgresServer,
) -> None:
    pg = stannum_server
    assert scalar(pg, "SELECT extversion FROM pg_extension WHERE extname='stannum';") == "0.4.0"
    assert scalar(pg, "SELECT matches FROM stannum.index_analysis('docs_jieba');") == "t"
    before = scalar(pg, "SELECT stannum.jieba_dict_version();")
    pg.psql("SELECT stannum.jieba_add_word('星河数据库协议', 1000000, 'n');")
    assert scalar(pg, "SELECT stannum.jieba_dict_version();") != before
    assert scalar(pg, """
        SELECT string_agg(tok, '/') FROM stannum.tokenize(
            '星河数据库协议', tokenizer => 'jieba') AS t(tok);
    """) == "星河数据库协议"
    assert scalar(pg, "SELECT matches FROM stannum.index_analysis('docs_jieba');") == "f"
    assert scalar(pg, """
        SELECT matches IS NULL AND status = 'not applicable'
        FROM stannum.index_analysis('docs_default');
    """) == "t"
    pg.psql("REINDEX INDEX docs_jieba;")
    assert scalar(pg, "SELECT matches FROM stannum.index_analysis('docs_jieba');") == "t"
    assert scalar(pg, """
        SELECT count(*) BETWEEN 150 AND 200
        FROM stannum.builtin_stop_words('zh');
    """) == "t"
    pg.psql("""
        ALTER INDEX docs_jieba SET (score_stop_words = 'auto:zh');
        INSERT INTO docs VALUES (6, '的 数据库', '的 数据库');
    """)
    assert scalar(pg, """
        SELECT count(*) FROM stannum.score_inspect('docs_jieba', '的', 1.1);
    """) == "0"
    assert scalar(pg, """
        SELECT bool_and(stannum.score(ctid, dense_ratio => 1.1) = 0
                        AND stannum.full_score(ctid) > 0)
        FROM docs WHERE body_jieba ==> '的';
    """) == "t"
    pg.psql("SELECT stannum.jieba_delete_word('星河数据库协议');")
    assert scalar(pg, "SELECT stannum.jieba_dict_version();") == before
