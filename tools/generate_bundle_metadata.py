#!/usr/bin/env python3
"""Validate an installed pgembed payload and atomically emit schema-v1 metadata."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import tempfile
from typing import Any

SCHEMA_VERSION = 1
BUNDLE_RECIPE = "pgembed-postgresql-18-bundle-v1"
PG_MAJOR = 18

EXTENSIONS: dict[str, dict[str, Any]] = {
    "pgvector": dict(version="0.8.2", source_ref="v0.8.2", source_commit=None, source_sha256="69f4019389af05dc1c9548deb8628e62878e6e207c03907f2b8af2016472cdaa", stem="vector", create_name="vector", preload_name=None, requires_preload=False),
    "vectorchord": dict(version="1.1.1", source_ref="1.1.1", source_commit=None, source_sha256="d70b5595bfc852f1f24c05c0a40272e7deecbb0ddf8ffdddec5afa42c2392b1e", stem="vchord", create_name="vchord", preload_name="vchord", requires_preload=True),
    "age": dict(version="1.8.0", source_ref="release/PG18/1.8.0", source_commit="e43dc1a12b78fba4acef9835b2b10379b8d243b4", source_sha256=None, stem="age", create_name="age", preload_name=None, requires_preload=False),
    "psql_bm25s": dict(version=None, source_ref="d1c1db7e6c2a92c2a909e97c51cf2f45c0da808b", source_commit="d1c1db7e6c2a92c2a909e97c51cf2f45c0da808b", source_sha256=None, stem="psql_bm25s", create_name="psql_bm25s", preload_name=None, requires_preload=False),
    "timescaledb": dict(version="2.27.1", source_ref="2.27.1", source_commit=None, source_sha256="f0a940720bb5b0b635dae4d8aeceb13e83b196b8aab8717876af0f45efa47ab6", stem="timescaledb", create_name="timescaledb", preload_name="timescaledb", requires_preload=True),
    "pg_cron": dict(version="1.6.7", source_ref="v1.6.7", source_commit="465b38c737f584d520229f5a1d69d1d44649e4e5", source_sha256=None, stem="pg_cron", create_name="pg_cron", preload_name="pg_cron", requires_preload=True),
    "pg_net": dict(version="0.20.5", source_ref="v0.20.5", source_commit="a8299b11182ea5c974f5e89ae83e70e9e44e9e8f", source_sha256=None, stem="pg_net", create_name="pg_net", preload_name="pg_net", requires_preload=True),
}


def _run_version(executable: Path, *args: str) -> str:
    result = subprocess.run(
        [str(executable), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return (result.stdout or result.stderr).strip()


def _version(text: str) -> str:
    match = re.search(r"PostgreSQL\)?\s+(\d+(?:\.\d+)+)", text)
    if match is None:
        match = re.search(r"\b(\d+(?:\.\d+)+)\b", text)
    if match is None:
        raise ValueError(f"cannot parse PostgreSQL version: {text!r}")
    return match.group(1)


def _default_version(extension_dir: Path, stem: str) -> str:
    control = extension_dir / f"{stem}.control"
    text = control.read_text(encoding="utf-8")
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "default_version":
            version = value.strip().strip("'\"")
            if version:
                return version
    raise ValueError(f"{control} has no default_version")


def _installation_sql_paths(extension_dir: Path, stem: str) -> tuple[Path, ...]:
    """Return a deterministic valid install script plus update path.

    PostgreSQL permits CREATE EXTENSION to start from a directly installable
    version and follow update scripts when the control file's default version
    has no direct ``extension--version.sql`` script.
    """
    default_version = _default_version(extension_dir, stem)
    install_scripts: dict[str, Path] = {}
    update_scripts: dict[str, list[tuple[str, Path]]] = {}
    prefix = f"{stem}--"

    for sql in sorted(extension_dir.glob(f"{stem}--*.sql")):
        filename = sql.name
        if not filename.startswith(prefix) or not filename.endswith(".sql"):
            continue
        versions = filename[len(prefix) : -len(".sql")].split("--")
        if len(versions) == 1 and versions[0]:
            install_scripts[versions[0]] = sql
        elif len(versions) == 2 and all(versions):
            update_scripts.setdefault(versions[0], []).append((versions[1], sql))

    direct = install_scripts.get(default_version)
    if direct is not None:
        return (direct,)

    installable_versions = set(install_scripts)
    candidates: list[tuple[int, str, tuple[str, ...], tuple[Path, ...]]] = []
    for start_version, install_sql in sorted(install_scripts.items()):
        queue: list[tuple[str, tuple[Path, ...]]] = [(start_version, ())]
        visited = {start_version}
        while queue:
            current_version, path = queue.pop(0)
            for next_version, update_sql in sorted(
                update_scripts.get(current_version, ()),
                key=lambda item: (item[0], item[1].name),
            ):
                if next_version in visited:
                    continue
                next_path = (*path, update_sql)
                if next_version == default_version:
                    full_path = (install_sql, *next_path)
                    candidates.append(
                        (len(next_path), start_version, tuple(item.name for item in full_path), full_path)
                    )
                    queue.clear()
                    break
                visited.add(next_version)
                # Match PostgreSQL's installation search: do not route through
                # another directly installable version.
                if next_version not in installable_versions:
                    queue.append((next_version, next_path))

    if not candidates:
        raise FileNotFoundError(
            f"{stem} has no installation script or update path for default version {default_version}"
        )
    return min(candidates, key=lambda item: item[:3])[3]


def _relative(path: Path, prefix: Path) -> str:
    return path.relative_to(prefix).as_posix()


def _split(value: str) -> set[str]:
    return {item for item in value.split() if item}


def generate(args: argparse.Namespace) -> dict[str, Any]:
    prefix = args.install_prefix.resolve()
    bin_dir = prefix / "bin"
    lib_dir = prefix / "lib" / "postgresql"
    extension_dir = prefix / "share" / "postgresql" / "extension"
    postgres_version = _run_version(bin_dir / "postgres", "--version")
    pg_config_version = _run_version(bin_dir / "pg_config", "--version")
    configure = _run_version(bin_dir / "pg_config", "--configure")
    postgres_full_version = _version(postgres_version)
    pg_config_full_version = _version(pg_config_version)
    if (
        postgres_full_version != args.postgres_version
        or pg_config_full_version != args.postgres_version
        or int(postgres_full_version.split(".", 1)[0]) != PG_MAJOR
    ):
        raise ValueError(
            f"installed binaries must both report exact PostgreSQL {args.postgres_version}: "
            f"postgres={postgres_version!r}, pg_config={pg_config_version!r}"
        )
    configure_tokens = set(shlex.split(configure))
    missing_configure_flags = [
        flag for flag in shlex.split(args.configure_flags) if flag not in configure_tokens
    ]
    if missing_configure_flags:
        raise ValueError(
            f"pg_config --configure is missing expected flags {missing_configure_flags}: {configure!r}"
        )

    requested = _split(args.requested)
    built = _split(args.built)
    skipped = _split(args.skipped)
    unknown = (requested | built | skipped) - (set(EXTENSIONS) | {"tigerfs"})
    if unknown:
        raise ValueError(f"unknown extensions: {sorted(unknown)}")
    if built & skipped:
        raise ValueError(f"components cannot be both built and skipped: {sorted(built & skipped)}")
    if (built | skipped) - requested:
        raise ValueError(
            f"built/skipped components must have been requested: {sorted((built | skipped) - requested)}"
        )
    unresolved = requested - built - skipped
    if unresolved:
        raise ValueError(f"requested components were neither built nor skipped: {sorted(unresolved)}")

    suffix = "dylib" if args.host_os == "Darwin" else "so"
    records: dict[str, Any] = {}
    for name, source in EXTENSIONS.items():
        is_requested = name in requested
        is_built = name in built
        is_skipped = name in skipped
        stem = source["stem"]
        library = lib_dir / f"{stem}.{suffix}"
        control = extension_dir / f"{stem}.control"
        sql_paths: tuple[Path, ...] = ()
        if is_built:
            if not library.is_file() or not control.is_file():
                raise FileNotFoundError(
                    f"metadata says {name} was built, but {library} or {control} is missing"
                )
            sql_paths = _installation_sql_paths(extension_dir, stem)
        else:
            stale_sql = tuple(extension_dir.glob(f"{stem}--*.sql"))
            if library.exists() or control.exists() or stale_sql:
                state = "skipped" if is_skipped else "not selected"
                raise ValueError(
                    f"{name} was {state} but stale installed artifacts remain"
                )
        records[name] = {
            "requested": is_requested,
            "built": is_built,
            "skipped": is_skipped,
            "built_for_postgres_major": PG_MAJOR,
            "create_name": source["create_name"],
            "preload_name": source["preload_name"],
            "requires_preload": source["requires_preload"],
            "library": _relative(library, prefix) if is_built else None,
            "control": _relative(control, prefix) if is_built else None,
            "install_sql": _relative(sql_paths[0], prefix) if sql_paths else None,
            "update_sql": [_relative(sql, prefix) for sql in sql_paths[1:]],
            "version": source["version"],
            "source_ref": source["source_ref"],
            "source_commit": source["source_commit"],
            "source_sha256": source["source_sha256"],
            "source_submodules": source.get("source_submodules", {}),
            "skip_reason": args.skip_reason if is_skipped else None,
        }

    tigerfs_requested = "tigerfs" in requested
    tigerfs_built = "tigerfs" in built
    tigerfs_path = bin_dir / "tigerfs"
    tigerfs_binary_version: str | None = None
    if tigerfs_built:
        if not tigerfs_path.is_file() or not os.access(tigerfs_path, os.X_OK):
            raise FileNotFoundError(f"TigerFS executable is missing or not executable: {tigerfs_path}")
        tigerfs_binary_version = _run_version(tigerfs_path, "version")
        expected_tigerfs_version = args.tigerfs_version.removeprefix("v")
        version_line = tigerfs_binary_version.splitlines()[0].strip()
        version_match = re.fullmatch(r"TigerFS\s+v?(\d+\.\d+\.\d+)", version_line)
        if version_match is None or version_match.group(1) != expected_tigerfs_version:
            raise ValueError(
                f"TigerFS reports {tigerfs_binary_version!r}, expected exact version {args.tigerfs_version}"
            )
    elif tigerfs_path.exists():
        raise ValueError("tigerfs was not built but a stale installed binary remains")

    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_recipe": BUNDLE_RECIPE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "postgres": {
            "major": PG_MAJOR,
            "version": args.postgres_version,
            "source_ref": args.postgres_ref,
            "source_commit": args.postgres_commit,
            "binary_version": postgres_version,
            "pg_config_version": pg_config_version,
            "configure": configure,
        },
        "build": {
            "host_os": args.host_os,
            "arch": args.arch,
            "libc": args.libc,
            "deployment_target": args.deployment_target or None,
            "configure_flags": args.configure_flags,
            "icu_enabled": "--without-icu" not in args.configure_flags,
            "rust_toolchain": args.rust_toolchain,
            "cargo_pgrx_version": args.cargo_pgrx_version,
            "python": platform.python_version(),
        },
        "extensions": records,
        "tigerfs": {
            "requested": tigerfs_requested,
            "built": tigerfs_built,
            "skipped": "tigerfs" in skipped,
            "version": args.tigerfs_version,
            "sha256": args.tigerfs_sha256 or None,
            "binary": _relative(tigerfs_path, prefix) if tigerfs_built else None,
            "binary_version": tigerfs_binary_version,
            "skip_reason": args.skip_reason if "tigerfs" in skipped else None,
        },
    }


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--install-prefix", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--postgres-version", required=True)
    result.add_argument("--postgres-ref", required=True)
    result.add_argument("--postgres-commit", required=True)
    result.add_argument("--configure-flags", required=True)
    result.add_argument("--requested", default="")
    result.add_argument("--built", default="")
    result.add_argument("--skipped", default="")
    result.add_argument("--skip-reason", default="unsupported on this build platform")
    result.add_argument("--host-os", required=True)
    result.add_argument("--arch", required=True)
    result.add_argument("--libc", required=True)
    result.add_argument("--deployment-target", default="")
    result.add_argument("--rust-toolchain", required=True)
    result.add_argument("--cargo-pgrx-version", required=True)
    result.add_argument("--tigerfs-version", default="v0.7.0")
    result.add_argument("--tigerfs-sha256", default="")
    return result


def main() -> None:
    args = parser().parse_args()
    args.output.unlink(missing_ok=True)
    payload = generate(args)
    atomic_write(args.output, payload)


if __name__ == "__main__":
    main()
