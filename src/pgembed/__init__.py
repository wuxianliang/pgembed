"""Public API for pgembed's attested bundled PostgreSQL runtime."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Optional
import warnings

from ._bundle_metadata import (
    BUNDLED_PG_MAJOR,
    BUNDLED_POSTGRES_VERSION,
    BundleMetadata,
    ExtensionMetadata,
    load_bundle_metadata,
)
from ._commands import *
from ._commands import POSTGRES_BIN_PATH
from .errors import (
    PgEmbedError,
    BundledPostgresMetadataError,
    PostgresDataDirectoryInspectionError,
    PostgresDataDirectoryVersionError,
    PostgresStartupError,
    PostgresStartupTimeoutError,
)

_logger = logging.getLogger("pgembed")


def _get_pkg_path() -> Path:
    spec = importlib.util.find_spec("pgembed")
    if spec and spec.submodule_search_locations:
        return Path(spec.submodule_search_locations[0])
    return Path(__file__).parent


PACKAGE_PATH = _get_pkg_path()
INSTALL_PATH = PACKAGE_PATH / "pginstall"
EXTENSION_LIB_PATH = INSTALL_PATH / "lib"
EXTENSION_POSTGRES_LIB_PATH = EXTENSION_LIB_PATH / "postgresql"
EXTENSION_SHARE_PATH = INSTALL_PATH / "share" / "postgresql" / "extension"

EXTENSION_PACKAGES = {
    "pgvector": "pgembed_pgvector",
    "vectorchord": None,
    "age": None,
    "psql_bm25s": None,
    "timescaledb": None,
    "pg_cron": None,
    "pg_net": None,
}

EXTENSION_ARTIFACT_STEMS = {
    "pgvector": "vector",
    "vectorchord": "vchord",
}

EXTENSION_SO_FILES = {
    "pgvector": ("vector.dylib", "vector.so", "vector.dll"),
    "vectorchord": ("vchord.dylib", "vchord.so", "vchord.dll"),
    "age": ("age.dylib", "age.so", "age.dll"),
    "psql_bm25s": ("psql_bm25s.dylib", "psql_bm25s.so", "psql_bm25s.dll"),
    "timescaledb": ("timescaledb.dylib", "timescaledb.so", "timescaledb.dll"),
    "pg_cron": ("pg_cron.dylib", "pg_cron.so", "pg_cron.dll"),
    "pg_net": ("pg_net.dylib", "pg_net.so", "pg_net.dll"),
}

EXTENSION_PRECEDENCE: dict[str, tuple[str, ...]] = {}
EXTENSION_NAMES = tuple(EXTENSION_PACKAGES)
AVAILABLE_EXTENSIONS: dict[str, bool] = {name: False for name in EXTENSION_NAMES}
_EXTENSION_PATHS: dict[str, Path] = {}
_BUNDLE_METADATA: Optional[BundleMetadata] = None


def _read_extension_default_version(stem: str, share_path: Path = EXTENSION_SHARE_PATH) -> Optional[str]:
    control_path = share_path / f"{stem}.control"
    try:
        control_text = control_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in control_text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "default_version":
            return value.strip().strip("'\"")
    return None


def get_extension_install_sql_path(name: str) -> Optional[Path]:
    """Return the direct or base install SQL for a bundled extension."""
    metadata = _BUNDLE_METADATA
    if metadata is not None:
        ext = metadata.extensions.get(name)
        if ext is not None and ext.install_sql:
            path = INSTALL_PATH / ext.install_sql
            return path if path.is_file() else None
    stem = EXTENSION_ARTIFACT_STEMS.get(name, name)
    default_version = _read_extension_default_version(stem)
    if not default_version:
        return None
    path = EXTENSION_SHARE_PATH / f"{stem}--{default_version}.sql"
    return path if path.is_file() else None


def _bundled_artifacts_are_complete(extension: ExtensionMetadata) -> tuple[bool, Optional[Path]]:
    if extension.built_for_postgres_major != BUNDLED_PG_MAJOR:
        return False, None
    required = (
        extension.library,
        extension.control,
        extension.install_sql,
        *extension.update_sql,
    )
    if not all(required):
        return False, None
    paths = tuple(INSTALL_PATH / value for value in required if value is not None)
    if not all(path.is_file() for path in paths):
        return False, None
    return True, paths[0]


def _legacy_artifacts_exist(name: str) -> bool:
    stem = EXTENSION_ARTIFACT_STEMS.get(name, name)
    if (EXTENSION_SHARE_PATH / f"{stem}.control").exists():
        return True
    if any(EXTENSION_SHARE_PATH.glob(f"{stem}--*.sql")):
        return True
    return any((EXTENSION_POSTGRES_LIB_PATH / filename).exists() for filename in EXTENSION_SO_FILES[name])


def _standalone_extension_path(name: str) -> Optional[Path]:
    package_name = EXTENSION_PACKAGES.get(name)
    if not package_name:
        return None
    try:
        package = __import__(package_name)
    except ImportError:
        return None
    attested_major = getattr(
        package,
        "BUILT_FOR_POSTGRES_MAJOR",
        getattr(package, "built_for_postgres_major", None),
    )
    if attested_major != BUNDLED_PG_MAJOR or BUNDLED_PG_MAJOR is None:
        warnings.warn(
            f"Ignoring {package_name}: it does not attest compatibility with bundled "
            f"PostgreSQL major {BUNDLED_PG_MAJOR!r}.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    path = package.get_extension_path()
    return Path(path) if path and Path(path).is_file() else None


def _detect_extensions() -> None:
    global _BUNDLE_METADATA
    AVAILABLE_EXTENSIONS.update({name: False for name in EXTENSION_NAMES})
    _EXTENSION_PATHS.clear()
    try:
        _BUNDLE_METADATA = load_bundle_metadata()
    except BundledPostgresMetadataError as exc:
        _BUNDLE_METADATA = None
        _logger.warning("Bundled PostgreSQL metadata is invalid: %s", exc)
        return
    if _BUNDLE_METADATA is None:
        _logger.info("No bundled PostgreSQL metadata; extensions remain unavailable")
        return

    for name in EXTENSION_NAMES:
        extension = _BUNDLE_METADATA.extensions.get(name)
        if extension is None:
            continue
        if not extension.built and _legacy_artifacts_exist(name):
            state = "skipped" if extension.skipped else "not selected"
            _logger.warning(
                "Extension %s is %s but stale bundled artifacts are present; refusing it",
                name,
                state,
            )
            continue
        if extension.skipped:
            continue
        if extension.built:
            complete, library = _bundled_artifacts_are_complete(extension)
            if complete and library is not None:
                AVAILABLE_EXTENSIONS[name] = True
                _EXTENSION_PATHS[name] = library
                continue
            _logger.warning("Metadata marks %s built, but required artifacts are incomplete", name)
            continue

        standalone_path = _standalone_extension_path(name)
        if standalone_path is not None:
            AVAILABLE_EXTENSIONS[name] = True
            _EXTENSION_PATHS[name] = standalone_path


def has_extension(name: str) -> bool:
    """Return whether an extension is metadata-attested and complete."""
    return AVAILABLE_EXTENSIONS.get(name, False)


def list_extensions() -> dict[str, bool]:
    return AVAILABLE_EXTENSIONS.copy()


def get_extension_create_name(name: str) -> str:
    if _BUNDLE_METADATA is not None:
        extension = _BUNDLE_METADATA.extensions.get(name)
        if extension is not None:
            return extension.create_name
    return {
        "pgvector": "vector",
        "vectorchord": "vchord",
        "age": "age",
        "psql_bm25s": "psql_bm25s",
        "timescaledb": "timescaledb",
        "pg_cron": "pg_cron",
        "pg_net": "pg_net",
    }.get(name, name)


def get_extension_path(name: str) -> Optional[Path]:
    """Return the attested extension library path without implying availability by existence alone."""
    return _EXTENSION_PATHS.get(name)


_detect_extensions()

# Imported last to avoid a circular import while postgres_server imports package metadata.
from .postgres_server import PostgresServer, get_server
