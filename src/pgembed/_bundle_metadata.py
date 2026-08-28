"""Strict loading and runtime validation for the bundled PostgreSQL payload."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Mapping, Optional

from .errors import BundledPostgresMetadataError

BUNDLE_METADATA_SCHEMA_VERSION = 1
BUNDLE_METADATA_FILENAME = "build-metadata.json"
BINARY_VERSION_TIMEOUT_SECONDS = 5
SQL_ONLY_EXTENSIONS = frozenset({"pgmq"})


def _package_path() -> Path:
    return Path(__file__).resolve().parent


BUNDLE_METADATA_PATH = (
    _package_path() / "pginstall" / "share" / "pgembed" / BUNDLE_METADATA_FILENAME
)
POSTGRES_BIN_PATH = _package_path() / "pginstall" / "bin"


@dataclass(frozen=True)
class ExtensionMetadata:
    name: str
    requested: bool
    built: bool
    skipped: bool
    built_for_postgres_major: int
    create_name: str
    preload_name: Optional[str]
    requires_preload: bool
    has_library: bool
    library: Optional[str]
    control: Optional[str]
    install_sql: Optional[str]
    update_sql: tuple[str, ...]
    version: Optional[str]
    source_ref: Optional[str]
    source_commit: Optional[str]
    source_sha256: Optional[str]
    source_submodules: Mapping[str, str]
    skip_reason: Optional[str]


@dataclass(frozen=True)
class BundleMetadata:
    schema_version: int
    bundle_recipe: str
    postgres_major: int
    postgres_version: str
    postgres_source_ref: str
    postgres_source_commit: str
    postgres_binary_version: str
    pg_config_version: str
    configure: str
    build: Mapping[str, Any]
    extensions: Mapping[str, ExtensionMetadata]
    tigerfs: Mapping[str, Any]
    raw: Mapping[str, Any]


_metadata_cache: object = object()
_binary_validation_cache: set[tuple[Path, str]] = set()
_CACHE_MISS = _metadata_cache


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise BundledPostgresMetadataError(f"bundle metadata field {field!r} must be an object")
    return value


def _require_str(mapping: Mapping[str, Any], key: str, field: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BundledPostgresMetadataError(f"bundle metadata field {field}.{key} must be a non-empty string")
    return value


def _require_bool(mapping: Mapping[str, Any], key: str, field: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise BundledPostgresMetadataError(f"bundle metadata field {field}.{key} must be a boolean")
    return value


def _optional_str(mapping: Mapping[str, Any], key: str, field: str) -> Optional[str]:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BundledPostgresMetadataError(f"bundle metadata field {field}.{key} must be a string or null")
    return value


def _safe_relative_path(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    if "\\" in value:
        raise BundledPostgresMetadataError(
            f"bundle metadata field {field} must use a normalized relative POSIX path"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in ("", ".", "..") for part in path.parts):
        raise BundledPostgresMetadataError(
            f"bundle metadata field {field} must be a normalized relative path inside the bundle"
        )
    return value


def _relative_path_tuple(mapping: Mapping[str, Any], key: str, field: str) -> tuple[str, ...]:
    value = mapping.get(key, [])
    if not isinstance(value, list):
        raise BundledPostgresMetadataError(
            f"bundle metadata field {field}.{key} must be an array of relative paths"
        )
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise BundledPostgresMetadataError(
                f"bundle metadata field {field}.{key}[{index}] must be a string"
            )
        normalized = _safe_relative_path(item, f"{field}.{key}[{index}]")
        assert normalized is not None
        result.append(normalized)
    if len(set(result)) != len(result):
        raise BundledPostgresMetadataError(
            f"bundle metadata field {field}.{key} must not contain duplicate paths"
        )
    return tuple(result)


def _string_mapping(mapping: Mapping[str, Any], key: str, field: str) -> Mapping[str, str]:
    value = mapping.get(key, {})
    if not isinstance(value, dict) or not all(
        isinstance(item_key, str) and item_key and isinstance(item_value, str) and item_value
        for item_key, item_value in value.items()
    ):
        raise BundledPostgresMetadataError(
            f"bundle metadata field {field}.{key} must be an object of non-empty strings"
        )
    return value


def _parse_metadata(raw: Any) -> BundleMetadata:
    root = _require_mapping(raw, "root")
    schema = root.get("schema_version")
    if schema != BUNDLE_METADATA_SCHEMA_VERSION:
        raise BundledPostgresMetadataError(
            f"unsupported bundle metadata schema {schema!r}; expected {BUNDLE_METADATA_SCHEMA_VERSION}"
        )
    recipe = _require_str(root, "bundle_recipe", "root")
    postgres = _require_mapping(root.get("postgres"), "postgres")
    major = postgres.get("major")
    if not isinstance(major, int) or isinstance(major, bool) or major <= 0:
        raise BundledPostgresMetadataError("bundle metadata field postgres.major must be a positive integer")

    extension_records: dict[str, ExtensionMetadata] = {}
    extensions = _require_mapping(root.get("extensions"), "extensions")
    for name, value in extensions.items():
        if not isinstance(name, str) or not name:
            raise BundledPostgresMetadataError("extension metadata keys must be non-empty strings")
        field = f"extensions.{name}"
        ext = _require_mapping(value, field)
        ext_major = ext.get("built_for_postgres_major")
        if not isinstance(ext_major, int) or isinstance(ext_major, bool):
            raise BundledPostgresMetadataError(f"bundle metadata field {field}.built_for_postgres_major must be an integer")
        record = ExtensionMetadata(
            name=name,
            requested=_require_bool(ext, "requested", field),
            built=_require_bool(ext, "built", field),
            skipped=_require_bool(ext, "skipped", field),
            built_for_postgres_major=ext_major,
            create_name=_require_str(ext, "create_name", field),
            preload_name=_optional_str(ext, "preload_name", field),
            requires_preload=_require_bool(ext, "requires_preload", field),
            has_library=(
                _require_bool(ext, "has_library", field) if "has_library" in ext else True
            ),
            library=_safe_relative_path(
                _optional_str(ext, "library", field), f"{field}.library"
            ),
            control=_safe_relative_path(
                _optional_str(ext, "control", field), f"{field}.control"
            ),
            install_sql=_safe_relative_path(
                _optional_str(ext, "install_sql", field), f"{field}.install_sql"
            ),
            update_sql=_relative_path_tuple(ext, "update_sql", field),
            version=_optional_str(ext, "version", field),
            source_ref=_optional_str(ext, "source_ref", field),
            source_commit=_optional_str(ext, "source_commit", field),
            source_sha256=_optional_str(ext, "source_sha256", field),
            source_submodules=_string_mapping(ext, "source_submodules", field),
            skip_reason=_optional_str(ext, "skip_reason", field),
        )
        if record.built and record.skipped:
            raise BundledPostgresMetadataError(f"{field} cannot be both built and skipped")
        if record.requested != (record.built or record.skipped):
            raise BundledPostgresMetadataError(
                f"{field} must be built or skipped exactly when it was requested"
            )
        if record.built and record.built_for_postgres_major != major:
            raise BundledPostgresMetadataError(
                f"{field} targets PostgreSQL {record.built_for_postgres_major}, bundle targets {major}"
            )
        if record.built and not (record.source_commit or record.source_sha256):
            raise BundledPostgresMetadataError(
                f"{field} is built but has no immutable source commit or SHA-256 identity"
            )
        artifact_fields = (record.library, record.control, record.install_sql)
        if record.install_sql and record.install_sql in record.update_sql:
            raise BundledPostgresMetadataError(
                f"{field}.update_sql must not repeat the base install_sql path"
            )
        if record.built and not (record.control and record.install_sql):
            raise BundledPostgresMetadataError(
                f"{field} is built but control and install_sql are not recorded"
            )
        if record.built and record.has_library and not record.library:
            raise BundledPostgresMetadataError(
                f"{field} is built but library is not recorded"
            )
        if record.built and not record.has_library and record.library is not None:
            raise BundledPostgresMetadataError(
                f"{field} is SQL-only but a library path is recorded"
            )
        if record.built and not record.has_library and record.name not in SQL_ONLY_EXTENSIONS:
            raise BundledPostgresMetadataError(
                f"{field} cannot be SQL-only"
            )
        if record.built and record.has_library and record.name in SQL_ONLY_EXTENSIONS:
            raise BundledPostgresMetadataError(
                f"{field} is SQL-only and must not attest a native library"
            )
        if not record.built and (any(artifact_fields) or record.update_sql):
            raise BundledPostgresMetadataError(
                f"{field} is not built but installed artifact paths are recorded"
            )
        if record.requires_preload and not record.preload_name:
            raise BundledPostgresMetadataError(
                f"{field} requires preload but has no preload_name"
            )
        if record.skipped and not record.skip_reason:
            raise BundledPostgresMetadataError(f"{field} is skipped but has no skip_reason")
        extension_records[name] = record

    tigerfs = _require_mapping(root.get("tigerfs"), "tigerfs")
    tigerfs_requested = _require_bool(tigerfs, "requested", "tigerfs")
    tigerfs_built = _require_bool(tigerfs, "built", "tigerfs")
    tigerfs_skipped = _require_bool(tigerfs, "skipped", "tigerfs")
    if tigerfs_built and tigerfs_skipped:
        raise BundledPostgresMetadataError("tigerfs cannot be both built and skipped")
    if tigerfs_requested != (tigerfs_built or tigerfs_skipped):
        raise BundledPostgresMetadataError(
            "tigerfs must be built or skipped exactly when it was requested"
        )
    tigerfs_binary = _safe_relative_path(
        _optional_str(tigerfs, "binary", "tigerfs"), "tigerfs.binary"
    )
    tigerfs_binary_version = _optional_str(tigerfs, "binary_version", "tigerfs")
    tigerfs_sha256 = _optional_str(tigerfs, "sha256", "tigerfs")
    tigerfs_skip_reason = _optional_str(tigerfs, "skip_reason", "tigerfs")
    _require_str(tigerfs, "version", "tigerfs")
    if tigerfs_built and not (tigerfs_binary and tigerfs_binary_version and tigerfs_sha256):
        raise BundledPostgresMetadataError(
            "tigerfs is built but binary, binary_version, and sha256 are not all recorded"
        )
    if not tigerfs_built and (tigerfs_binary or tigerfs_binary_version or tigerfs_sha256):
        raise BundledPostgresMetadataError(
            "tigerfs is not built but installed binary metadata is recorded"
        )
    if tigerfs_skipped and not tigerfs_skip_reason:
        raise BundledPostgresMetadataError("tigerfs is skipped but has no skip_reason")
    if not tigerfs_skipped and tigerfs_skip_reason:
        raise BundledPostgresMetadataError(
            "tigerfs is not skipped but a skip_reason is recorded"
        )

    return BundleMetadata(
        schema_version=schema,
        bundle_recipe=recipe,
        postgres_major=major,
        postgres_version=_require_str(postgres, "version", "postgres"),
        postgres_source_ref=_require_str(postgres, "source_ref", "postgres"),
        postgres_source_commit=_require_str(postgres, "source_commit", "postgres"),
        postgres_binary_version=_require_str(postgres, "binary_version", "postgres"),
        pg_config_version=_require_str(postgres, "pg_config_version", "postgres"),
        configure=_require_str(postgres, "configure", "postgres"),
        build=_require_mapping(root.get("build"), "build"),
        extensions=extension_records,
        tigerfs=tigerfs,
        raw=root,
    )


def clear_bundle_metadata_cache() -> None:
    """Clear process caches; primarily useful after an editable-tree rebuild."""
    global _metadata_cache
    _metadata_cache = _CACHE_MISS
    _binary_validation_cache.clear()


def load_bundle_metadata(path: Optional[Path] = None) -> Optional[BundleMetadata]:
    """Load metadata when present; malformed metadata always fails closed."""
    global _metadata_cache
    metadata_path = Path(path) if path is not None else BUNDLE_METADATA_PATH
    use_cache = path is None
    if use_cache and _metadata_cache is not _CACHE_MISS:
        return _metadata_cache  # type: ignore[return-value]
    try:
        text = metadata_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        result = None
    except OSError as exc:
        raise BundledPostgresMetadataError(f"cannot read bundle metadata at {metadata_path}: {exc}") from exc
    else:
        try:
            result = _parse_metadata(json.loads(text))
        except json.JSONDecodeError as exc:
            raise BundledPostgresMetadataError(f"malformed bundle metadata at {metadata_path}: {exc}") from exc
    if use_cache:
        _metadata_cache = result
    return result


def require_bundle_metadata(path: Optional[Path] = None) -> BundleMetadata:
    metadata = load_bundle_metadata(path)
    if metadata is None:
        location = Path(path) if path is not None else BUNDLE_METADATA_PATH
        raise BundledPostgresMetadataError(
            f"bundled PostgreSQL metadata is unavailable at {location}. "
            "The editable source tree may not have been built; run 'make build'."
        )
    return metadata


def _version_from_version_output(output: str, executable: Path) -> str:
    match = re.search(r"PostgreSQL\)?\s+(\d+(?:\.\d+)+)", output)
    if match is None:
        match = re.search(r"\b(\d+(?:\.\d+)+)\b", output)
    if match is None:
        raise BundledPostgresMetadataError(
            f"could not parse PostgreSQL version from {executable}: {output!r}"
        )
    return match.group(1)


def validate_bundled_binaries(
    metadata: Optional[BundleMetadata] = None,
    *,
    bin_path: Optional[Path] = None,
    timeout: float = BINARY_VERSION_TIMEOUT_SECONDS,
) -> BundleMetadata:
    """Attest installed postgres and pg_config on first server use."""
    metadata = metadata or require_bundle_metadata()
    binaries = Path(bin_path) if bin_path is not None else POSTGRES_BIN_PATH
    metadata_identity = json.dumps(metadata.raw, sort_keys=True, separators=(",", ":"))
    cache_key = (binaries.resolve(), metadata_identity)
    if cache_key in _binary_validation_cache:
        return metadata
    recorded_outputs = {
        "postgres": metadata.postgres_binary_version,
        "pg_config": metadata.pg_config_version,
    }
    for name in ("postgres", "pg_config"):
        executable = binaries / name
        if not executable.is_file():
            raise BundledPostgresMetadataError(f"required bundled executable is missing: {executable}")
        try:
            result = subprocess.run(
                [str(executable), "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BundledPostgresMetadataError(f"failed to validate {executable}: {exc}") from exc
        output = (result.stdout or result.stderr).strip()
        found_version = _version_from_version_output(output, executable)
        found_major = int(found_version.split(".", 1)[0])
        if found_major != metadata.postgres_major:
            raise BundledPostgresMetadataError(
                f"{executable} reports PostgreSQL major {found_major}, but bundle metadata "
                f"requires {metadata.postgres_major}: {output!r}"
            )
        if found_version != metadata.postgres_version:
            raise BundledPostgresMetadataError(
                f"{executable} reports PostgreSQL {found_version}, but bundle metadata "
                f"requires exact version {metadata.postgres_version}: {output!r}"
            )
        if output != recorded_outputs[name]:
            raise BundledPostgresMetadataError(
                f"{executable} version output does not match bundle metadata: "
                f"expected {recorded_outputs[name]!r}, found {output!r}"
            )
    _binary_validation_cache.add(cache_key)
    return metadata


try:
    _import_metadata = load_bundle_metadata()
except BundledPostgresMetadataError:
    # Import remains possible so callers can inspect/recover editable installs;
    # strict APIs and get_server() will surface the original metadata failure.
    _import_metadata = None

BUNDLED_PG_MAJOR: Optional[int] = (
    _import_metadata.postgres_major if _import_metadata is not None else None
)
BUNDLED_POSTGRES_VERSION: Optional[str] = (
    _import_metadata.postgres_version if _import_metadata is not None else None
)
