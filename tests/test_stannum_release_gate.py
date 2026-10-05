"""The STN4 0.5.0 release gate, measured against the *installed* pgembed wheel.

pgembed ships its PostgreSQL 18 prefix, every bundled extension and a
``build-metadata.json`` *inside* the wheel, so a release gate is answerable
from a clean environment rather than from a developer's build prefix. These
tests therefore assert three things about the artifact under test:

1. the ``pgembed`` that is imported is the installed wheel's, and the bundle
   it serves comes from inside that same wheel -- never a source checkout and
   never an external prefix quietly substituting its own ``stannum`` library;
2. the bundle's recorded provenance names the commit ``pgbuild/Makefile``
   actually pins, so the measurement is tied to a candidate that can be named;
3. a live server on that bundle exposes the 0.5.0 surface -- extension
   version, ``stannum.capabilities()``, the function census, the absence of
   ``pg_test``-only internals -- and still answers a query.

The query semantics themselves are not re-measured here:
``tests/test_pgembed_stannum.py`` and ``tests/test_stannum_jieba.py`` exercise
the same installed bundle.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

import pgembed
import pgembed._commands as commands
from pgembed._bundle_metadata import require_bundle_metadata


pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "pgbuild" / "Makefile"

# The 0.4.0 census recorded 41 functions in the `stannum` schema; 0.5.0 adds
# stannum.capabilities(). The count is taken from the live catalog, not from a
# name-prefix guess (only four pg_proc names actually start with "stannum").
STANNUM_SCHEMA_FUNCTION_COUNT = 42

# Functions gated behind the pgrx `pg_test` feature. They must never ship.
PG_TEST_ONLY_FUNCTIONS = ("corrupt_index_page", "index_page_kinds")

DOCS_SQL = """
CREATE TABLE release_gate_docs (id int PRIMARY KEY, body text);
INSERT INTO release_gate_docs VALUES
 (1, 'PostgreSQL database kernel internals'),
 (2, 'Stannum release gate smoke test');
CREATE INDEX release_gate_docs_idx ON release_gate_docs USING stannum (body);
ANALYZE release_gate_docs;
"""


def makefile_pin(name: str) -> str:
    """A ``NAME := value`` source pin read from ``pgbuild/Makefile``.

    The gate must prove the installed bundle was built from the commit the
    Makefile pins, so the pin is read rather than copied. A copied literal
    drifts, and a drifted literal measures the wrong candidate while still
    passing.
    """
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if ":=" not in stripped or stripped.startswith("\t"):
            continue
        key, _, value = stripped.partition(":=")
        if key.strip() == name:
            pinned = value.strip()
            assert pinned, f"{name} is pinned to an empty value in {MAKEFILE}"
            return pinned
    raise AssertionError(f"{name} is not pinned in {MAKEFILE}")


# psql prints a `(N rows)` footer under every result; it is not a value.
PSQL_FOOTER = re.compile(r"^\(\d+ rows?\)$")


def scalar(pg: pgembed.PostgresServer, sql: str) -> str:
    """The single value of a one-row, one-column psql result.

    psql prints the column header, a dashed rule, the row and a row-count
    footer, so the footer and rule are dropped and exactly one data row is
    required: a query that silently returns more would otherwise be measured
    by its last line.
    """
    lines = [line.strip() for line in pg.psql(sql).splitlines() if line.strip()]
    assert lines, f"no output for {sql!r}"
    data = [
        line
        for line in lines
        if not set(line) <= {"-", "+"} and not PSQL_FOOTER.match(line)
    ]
    assert len(data) == 2, f"expected a header and exactly one row, got {data!r}"
    return data[1]


@pytest.fixture
def release_server() -> Iterator[pgembed.PostgresServer]:
    if not pgembed.has_extension("stannum"):
        pytest.fail(
            "the installed bundle does not ship stannum; the release gate cannot "
            "be measured against this wheel"
        )
    with tempfile.TemporaryDirectory() as tmpdir:
        data = Path(tmpdir) / "pgdata"
        data.mkdir()
        with pgembed.get_server(data, cleanup_mode="delete") as pg:
            pg.psql("CREATE EXTENSION stannum;")
            pg.psql(DOCS_SQL)
            yield pg


def test_loaded_pgembed_is_the_installed_wheel_bundle() -> None:
    package = Path(pgembed.__file__).resolve().parent
    assert package != REPO_ROOT / "src" / "pgembed", (
        f"the source checkout at {REPO_ROOT / 'src' / 'pgembed'} is shadowing the "
        "installed pgembed wheel; the measurement would test the wrong artifact"
    )
    assert package != REPO_ROOT / "pgembed", (
        f"the repository root package at {REPO_ROOT / 'pgembed'} is shadowing the "
        "installed pgembed wheel"
    )
    # The bundle the commands run is the one inside the imported package.
    assert Path(commands.POSTGRES_BIN_PATH) == package / "pginstall" / "bin"
    assert (package / "pginstall" / "bin" / "postgres").is_file()


def test_bundle_metadata_names_the_pinned_candidate() -> None:
    # Strict by design: absent metadata is a failed gate, not a skipped one.
    metadata = require_bundle_metadata()
    assert metadata.postgres_major == 18
    assert metadata.postgres_version == "18.4"

    stannum = metadata.extensions["stannum"]
    assert stannum.built is True
    assert stannum.skipped is False
    expected = makefile_pin("STANNUM_VERSION")
    assert stannum.version == expected, (
        f"stannum ships {stannum.version}, the Makefile declares {expected}: "
        "update STANNUM_VERSION when the pinned repository ships a release"
    )
    assert stannum.create_name == "stannum"
    assert stannum.has_library is True
    assert stannum.built_for_postgres_major == metadata.postgres_major == 18
    # The candidate identity: the commit the Makefile pins is the commit the
    # shipped library was built from.
    assert stannum.source_commit == makefile_pin("STANNUM_COMMIT")
    assert stannum.source_sha256 is None

    bundle_root = package_bundle_root()
    for relative in (stannum.library, stannum.control, stannum.install_sql):
        assert relative, f"stannum metadata records no path for {relative}"
        assert (bundle_root / relative).is_file(), f"missing bundled file: {relative}"
    for relative in stannum.update_sql:
        assert (bundle_root / relative).is_file(), f"missing bundled file: {relative}"


def package_bundle_root() -> Path:
    return Path(pgembed.__file__).resolve().parent / "pginstall"


def test_live_server_exposes_the_050_release_surface(
    release_server: pgembed.PostgresServer,
) -> None:
    pg = release_server

    assert scalar(pg, "SELECT current_setting('server_version_num')::int / 10000") == "18"
    expected = makefile_pin("STANNUM_VERSION")
    extversion = scalar(pg, "SELECT extversion FROM pg_extension WHERE extname = 'stannum';")
    assert extversion == expected, (
        f"installed extversion is {extversion}, the Makefile declares {expected}: "
        "update STANNUM_VERSION when the pinned repository ships a release"
    )
    assert (
        scalar(
            pg,
            "SELECT count(*) FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'stannum';",
        )
        == str(STANNUM_SCHEMA_FUNCTION_COUNT)
    )
    pg_test_functions = ", ".join(f"'{name}'" for name in PG_TEST_ONLY_FUNCTIONS)
    assert scalar(pg, f"SELECT count(*) FROM pg_proc WHERE proname IN ({pg_test_functions});") == "0"

    capabilities = json.loads(scalar(pg, "SELECT stannum.capabilities()::text;"))
    assert capabilities["contract_version"] == 1
    assert capabilities["engine"] == {
        "name": "stannum",
        "format": "STN3",
        "version": "0.5.0",
    }
    assert capabilities["features"]["jieba_ddl"] is True
    assert capabilities["features"]["tokenizers"] == ["unicode", "whitespace", "jieba"]

    # The search path still answers on the installed build.
    assert (
        scalar(
            pg,
            "SELECT string_agg(id::text, ',' ORDER BY id) FROM release_gate_docs"
            " WHERE body ==> 'postgresql';",
        )
        == "1"
    )
