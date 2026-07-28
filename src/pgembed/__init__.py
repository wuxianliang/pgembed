from ._commands import *
from .postgres_server import PostgresServer, get_server
from pathlib import Path
from typing import Optional
import logging
import importlib.util

_logger = logging.getLogger("pgembed")


def _get_pkg_path():
    spec = importlib.util.find_spec("pgembed")
    if spec and spec.submodule_search_locations:
        return Path(spec.submodule_search_locations[0])
    return Path(__file__).parent


EXTENSION_LIB_PATH = _get_pkg_path() / "pginstall" / "lib"
EXTENSION_POSTGRES_LIB_PATH = EXTENSION_LIB_PATH / "postgresql"
EXTENSION_SHARE_PATH = _get_pkg_path() / "pginstall" / "share" / "postgresql" / "extension"

AVAILABLE_EXTENSIONS = {}

EXTENSION_PACKAGES = {
    "pgvector": "pgembed_pgvector",
    "pg_duckdb": "pgembed_pgduckdb",
    "vectorchord": None,
    "age": None,
    "psql_bm25s": None,
    "timescaledb": None,
    "pg_cron": None,
    "pg_net": None,
}

EXTENSION_SO_FILES = {
    "pgvector": "vector.dylib",
    "pg_duckdb": "pg_duckdb.dylib",
    "vectorchord": ("vchord.dylib", "vchord.so", "vchord.dll"),
    "age": "age.dylib",
    "psql_bm25s": "psql_bm25s.dylib",
    "timescaledb": ("timescaledb.dylib", "timescaledb.so", "timescaledb.dll"),
    "pg_cron": ("pg_cron.dylib", "pg_cron.so", "pg_cron.dll"),
    "pg_net": ("pg_net.dylib", "pg_net.so", "pg_net.dll"),
}

EXTENSION_ARTIFACT_STEMS = {
    "vectorchord": "vchord",
}

# Conflict-aware ordering: extension key -> predecessor keys that must be
# created FIRST (when available) to avoid SQL object-name collisions.
# pg_duckdb and timescaledb both ship public.time_bucket(...) with identical
# signatures; pg_duckdb's installer skips its own copy only if timescaledb
# already exists (its DO/EXCEPTION guard, upstream PR #747), so timescaledb
# must be created before pg_duckdb.
# See docs/investigations/pg_duckdb-timescaledb-time-bucket-collision-2026-07-28.md
EXTENSION_PRECEDENCE = {
    "pg_duckdb": ("timescaledb",),
}

EXTENSION_NAMES = (
    "pgvector",
    "pg_duckdb",
    "vectorchord",
    "age",
    "psql_bm25s",
    "timescaledb",
    "pg_cron",
    "pg_net",
)


def _read_extension_default_version(stem: str) -> Optional[str]:
    control_path = EXTENSION_SHARE_PATH / f"{stem}.control"
    try:
        control_text = control_path.read_text()
    except OSError:
        return None

    for line in control_text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "default_version":
            return value.strip().strip("'\"")
    return None


def get_extension_install_sql_path(name: str) -> Optional[Path]:
    """Get the exact install SQL script for an extension's default_version."""
    stem = EXTENSION_ARTIFACT_STEMS.get(name, name)
    default_version = _read_extension_default_version(stem)
    if not default_version:
        return None
    install_sql_path = EXTENSION_SHARE_PATH / f"{stem}--{default_version}.sql"
    if install_sql_path.exists():
        return install_sql_path
    return None


def _has_extension_sql_artifacts(name: str) -> bool:
    stem = EXTENSION_ARTIFACT_STEMS.get(name)
    if stem is None:
        return True
    control_path = EXTENSION_SHARE_PATH / f"{stem}.control"
    return control_path.exists() and get_extension_install_sql_path(name) is not None




def _detect_extensions():
    global AVAILABLE_EXTENSIONS

    for name in EXTENSION_NAMES:
        pkg_name = EXTENSION_PACKAGES.get(name)
        try:
            if pkg_name:
                ext_pkg = __import__(pkg_name)
                ext_path = ext_pkg.get_extension_path()
                if ext_path and ext_path.exists():
                    AVAILABLE_EXTENSIONS[name] = True
                    _logger.info(f"Detected extension from package {pkg_name}: {name}")
                    continue
        except ImportError:
            pass

        so_files = EXTENSION_SO_FILES.get(name)
        if isinstance(so_files, str):
            so_files = (so_files,)
        detected = False
        for so_file in so_files or ():
            bundled_path = EXTENSION_POSTGRES_LIB_PATH / so_file
            if bundled_path.exists():
                AVAILABLE_EXTENSIONS[name] = _has_extension_sql_artifacts(name)
                if AVAILABLE_EXTENSIONS[name]:
                    _logger.info(f"Detected extension from bundled artifacts: {name}")
                else:
                    _logger.warning(
                        f"Detected bundled library for {name}, but control or SQL "
                        f"extension artifacts are missing"
                    )
                detected = True
                break
        if detected:
            continue

        AVAILABLE_EXTENSIONS[name] = False


def has_extension(name: str) -> bool:
    """Check if a specific extension is available.

    Args:
        name: Extension name (e.g., 'pgvector', 'pg_duckdb', 'vchord')

    Returns:
        True if the extension is available, False otherwise.
    """
    return AVAILABLE_EXTENSIONS.get(name, False)


def list_extensions() -> dict:
    """Return a dictionary of available extensions.

    Returns:
        Dict mapping extension names to availability (True/False)
    """
    return AVAILABLE_EXTENSIONS.copy()


def get_extension_create_name(name: str) -> str:
    """Get the SQL extension creation name for an extension.

    Args:
        name: Extension name (e.g., 'pgvector', 'vchord')

    Returns:
        The SQL name to use when creating the extension.
    """
    create_names = {
        "pgvector": "vector",
        "pg_duckdb": "pg_duckdb",
        "vectorchord": "vchord",
        "age": "age",
        "psql_bm25s": "psql_bm25s",
        "timescaledb": "timescaledb",
        "pg_cron": "pg_cron",
        "pg_net": "pg_net",
    }
    return create_names.get(name, name)


def get_extension_path(name: str) -> Optional[Path]:
    """Get the path to an extension .so file.

    Args:
        name: Extension name (e.g., 'pgvector', 'vchord')

    Returns:
        Path to the .so file, or None if not available.
    """
    pkg_name = EXTENSION_PACKAGES.get(name)
    if pkg_name:
        try:
            ext_pkg = __import__(pkg_name)
            ext_path = ext_pkg.get_extension_path()
            if ext_path:
                return ext_path
        except ImportError:
            pass

    so_files = EXTENSION_SO_FILES.get(name)
    if isinstance(so_files, str):
        so_files = (so_files,)
    for so_file in so_files or ():
        bundled_path = EXTENSION_POSTGRES_LIB_PATH / so_file
        if bundled_path.exists():
            return bundled_path

    return None


_detect_extensions()
