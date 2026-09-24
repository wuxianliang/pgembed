"""stannum observability surfaces through a bundled server (P0-4 smoke).

A light end-to-end check of the 0.3.0 observability features behind the
pgembed bundle: the `index_stats`/`index_health` SQL surface, the EXPLAIN
counter properties, and the CREATE INDEX progress view are exercised only as
installed behavior; contract depth lives in stannum's own test suite
(`postgres/tests/observability.py`).
"""

from __future__ import annotations

import subprocess
import tempfile
from typing import Iterator

import pytest

import pgembed

DOCS_SQL = """
CREATE TABLE docs (id int PRIMARY KEY, body text);
INSERT INTO docs
SELECT n, 'common ' || CASE WHEN n % 2 = 0 THEN 'beer' ELSE 'wine' END
FROM generate_series(1, 20) n;
SET stannum.build_segment_docs = 10;
CREATE INDEX docs_idx ON docs USING stannum (body);
DELETE FROM docs WHERE id IN (2, 4);
VACUUM docs;
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


def test_index_stats_reports_directory_health(stannum_server: pgembed.PostgresServer) -> None:
    assert scalar(stannum_server, """
        SELECT segments || '/' || immutable_segments || '/' || dead_documents
        FROM stannum.index_stats('docs_idx');
    """) == "2/2/2"
    assert scalar(stannum_server, """
        SELECT count(*) FROM stannum.index_health
        WHERE index = 'docs_idx'::regclass AND dead_ratio > 0;
    """) == "1"
    # The unicode tokenizer is never stamped: analysis columns stay NULL.
    assert scalar(stannum_server, """
        SELECT (analysis_matches IS NULL) AND (analysis_detail IS NULL)
        FROM stannum.index_stats('docs_idx');
    """) == "t"


def test_explain_reports_counter_properties(stannum_server: pgembed.PostgresServer) -> None:
    plan = stannum_server.psql(
        "SET enable_seqscan = off;"
        " EXPLAIN (ANALYZE, FORMAT TEXT) SELECT count(*) FROM docs"
        " WHERE body ==> 'beer';")
    for line in plan.splitlines():
        if "Segments Visited:" in line:
            assert int(line.split(":")[1]) >= 1
            break
    else:
        raise AssertionError(f"no Segments Visited property:\n{plan}")
    assert "Heap Fetches:" in plan, plan


def test_index_stats_rejects_non_stannum_index(stannum_server: pgembed.PostgresServer) -> None:
    stannum_server.psql("CREATE INDEX docs_id ON docs (id);")
    with pytest.raises(subprocess.CalledProcessError):
        stannum_server.psql("SELECT * FROM stannum.index_stats('docs_id');")
