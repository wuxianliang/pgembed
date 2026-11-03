"""Tests for the zero-configuration base entry point (``pgembed.bootstrap``)."""

import pytest

import pgembed
from pgembed.base import BASE_PROFILE, PROFILES, bootstrap
from pgembed.errors import PreloadRequiredError

integration = pytest.mark.integration


def test_default_pgdata_is_a_per_user_path():
    path = pgembed.default_pgdata()
    assert path.is_absolute()
    assert path.name == "pgdata"
    assert path.parent.name == "pgembed"


def test_resolve_extension_name_accepts_both_spellings():
    assert pgembed.resolve_extension_name("vector") == "pgvector"
    assert pgembed.resolve_extension_name("pgvector") == "pgvector"
    assert pgembed.resolve_extension_name("vchord") == "vectorchord"
    assert pgembed.resolve_extension_name("typesafe") == "pg_typesafe"
    # A PostgreSQL-shipped extension keeps its own name.
    assert pgembed.resolve_extension_name("ltree") == "ltree"


def test_base_profile_preloads_every_extension_that_needs_it():
    """A preload-requiring extension in the profile must be preloaded up front.

    Preload cannot be repaired after start, so a profile that lists a
    preload-requiring extension without listing its library would fail on the
    first ``CREATE EXTENSION``.
    """
    for name in BASE_PROFILE.extensions:
        metadata = pgembed.get_extension_metadata(pgembed.resolve_extension_name(name))
        if metadata is None or not metadata.requires_preload:
            continue
        assert metadata.preload_name in BASE_PROFILE.preload, (
            f"{name} requires {metadata.preload_name!r} in shared_preload_libraries"
        )


def test_base_profile_is_registered():
    assert PROFILES["base"] is BASE_PROFILE
    assert BASE_PROFILE.extensions


def test_base_profile_steers_jev_at_the_gateway():
    """The provider is configuration; the pinned extension source stays untouched."""
    from pgembed.base import DEFAULT_JEV_ENDPOINT, DEFAULT_JEV_MODEL

    assert BASE_PROFILE.settings["jev.api_url"] == DEFAULT_JEV_ENDPOINT
    assert BASE_PROFILE.settings["jev.model"] == DEFAULT_JEV_MODEL
    assert DEFAULT_JEV_ENDPOINT.startswith("https://api.tu-zi.com/")


def test_conf_literal_escapes_quotes_and_backslashes():
    from pgembed.postgres_server import _conf_literal

    assert _conf_literal("plain") == "'plain'"
    assert _conf_literal("it's") == "'it''s'"
    assert _conf_literal("a\\b") == "'a\\\\b'"


def test_unknown_profile_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown profile"):
        bootstrap(tmp_path / "data", profile="does-not-exist")


@integration
def test_bootstrap_creates_the_base_extension_set(tmp_path):
    server = bootstrap(tmp_path / "data", cleanup_mode="delete")
    report = server.bootstrap_report

    assert report.ok, report.summary()
    assert "vchord" in server.running_preload_libraries()

    installed = server.psql("SELECT extname FROM pg_extension ORDER BY 1;")
    for create_name in ("vector", "vchord", "stannum", "typesafe", "pgmq", "ltree"):
        assert create_name in installed, f"{create_name} missing from {installed}"


@integration
def test_bootstrap_is_idempotent(tmp_path):
    first = bootstrap(tmp_path / "data", cleanup_mode="stop")
    second = bootstrap(tmp_path / "data", cleanup_mode="stop")
    assert first is second
    assert second.bootstrap_report.ok, second.bootstrap_report.summary()


@integration
def test_bootstrap_applies_profile_settings_before_start(tmp_path):
    """jev.api_url/model must be in postgresql.conf, not SET after the fact."""
    server = bootstrap(tmp_path / "data", cleanup_mode="delete")
    from pgembed.base import DEFAULT_JEV_ENDPOINT, DEFAULT_JEV_MODEL

    conf = (server.pgdata / "postgresql.conf").read_text(encoding="utf-8")
    assert f"jev.api_url = '{DEFAULT_JEV_ENDPOINT}'" in conf
    assert server._psql_scalar("SHOW jev.api_url") == DEFAULT_JEV_ENDPOINT
    assert server._psql_scalar("SHOW jev.model") == DEFAULT_JEV_MODEL


@integration
def test_bootstrap_settings_override_wins(tmp_path):
    server = bootstrap(
        tmp_path / "data",
        cleanup_mode="delete",
        settings={"jev.model": "jev-1.13"},
    )
    assert server._psql_scalar("SHOW jev.model") == "jev-1.13"


@integration
def test_create_extension_reports_a_missing_preload(tmp_path):
    """Without the preload the old code surfaced a raw psql error; now it is typed."""
    server = pgembed.get_server(
        tmp_path / "data",
        cleanup_mode="delete",
        shared_preload_libraries=(),
    )
    with pytest.raises(PreloadRequiredError) as excinfo:
        server.create_extension("vchord")
    assert excinfo.value.preload_name == "vchord"
    assert excinfo.value.create_name == "vchord"


@integration
def test_bundled_typesafe_exposes_jev_predicates(tmp_path):
    server = pgembed.get_server(tmp_path / "data", cleanup_mode="delete")
    server.create_extension("typesafe")

    version = server._psql_scalar(
        "SELECT extversion FROM pg_extension WHERE extname = 'typesafe'"
    )
    assert version == "0.1.0", f"expected the 0.1.0 overlay, found {version!r}"

    functions = server.psql(
        "SELECT proname FROM pg_proc WHERE proname LIKE 'jev%' ORDER BY 1;"
    )
    for name in ("jev_prob", "jev_choice", "jev_score", "jev_version"):
        assert name in functions, f"{name} missing from pg_proc"


@integration
def test_typesafe_upgrade_path_from_0_0_1(tmp_path):
    """A database created with the old 0.0.1 catalog must reach 0.1.0."""
    server = pgembed.get_server(tmp_path / "data", cleanup_mode="delete")
    server.psql("CREATE EXTENSION typesafe VERSION '0.0.1';")
    assert "0.0.1" in server._psql_scalar(
        "SELECT extversion FROM pg_extension WHERE extname = 'typesafe'"
    )

    server.psql("ALTER EXTENSION typesafe UPDATE;")
    assert server._psql_scalar(
        "SELECT extversion FROM pg_extension WHERE extname = 'typesafe'"
    ) == "0.1.0"
