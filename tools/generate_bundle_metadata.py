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
import stat
import subprocess
import tempfile
from typing import Any

SCHEMA_VERSION = 1
BUNDLE_RECIPE = "pgembed-postgresql-18-bundle-v2"
PG_MAJOR = 18
PUBLIC_METADATA_MODE = 0o644
COMMIT_IDENTITY = re.compile(r"^[0-9a-f]{40}$")
SHA256_IDENTITY = re.compile(r"^[0-9a-f]{64}$")
# Components that are not PostgreSQL extensions but still have Makefile pins.
AUXILIARY_LOCK_NAMES = {"libfq", "libtommath", "firebird-client", "tigerfs"}

# Structural catalog only. version / source_ref / source_commit / source_sha256
# are not stored here: built records take identity from --source-lock and
# version from the installed control file.
EXTENSIONS: dict[str, dict[str, Any]] = {
    "pgvector": dict(stem="vector", create_name="vector", preload_name=None, requires_preload=False),
    "vectorchord": dict(stem="vchord", create_name="vchord", preload_name="vchord", requires_preload=True),
    "age": dict(stem="age", create_name="age", preload_name=None, requires_preload=False),
    "psql_bm25s": dict(stem="psql_bm25s", create_name="psql_bm25s", preload_name=None, requires_preload=False),
    "timescaledb": dict(stem="timescaledb", create_name="timescaledb", preload_name="timescaledb", requires_preload=True),
    "pg_cron": dict(stem="pg_cron", create_name="pg_cron", preload_name="pg_cron", requires_preload=True),
    "pg_net": dict(stem="pg_net", create_name="pg_net", preload_name="pg_net", requires_preload=True),
    "pgsql_http": dict(stem="http", create_name="http", preload_name=None, requires_preload=False),
    "plsh": dict(stem="plsh", create_name="plsh", preload_name=None, requires_preload=False),
    "firebird_fdw": dict(
        stem="firebird_fdw",
        create_name="firebird_fdw",
        preload_name=None,
        requires_preload=False,
        submodule_locks=("libfq", "firebird-client", "libtommath"),
    ),
    "pgmq": dict(
        stem="pgmq",
        create_name="pgmq",
        preload_name=None,
        requires_preload=False,
        has_library=False,
    ),
    "pg_partman": dict(
        stem="pg_partman",
        create_name="pg_partman",
        preload_name=None,
        requires_preload=False,
        has_library=False,
    ),
    "pgtap": dict(
        stem="pgtap",
        create_name="pgtap",
        preload_name=None,
        requires_preload=False,
        has_library=False,
    ),
    "pg_jsonschema": dict(
        stem="pg_jsonschema",
        create_name="pg_jsonschema",
        preload_name=None,
        requires_preload=False,
    ),
    "pg_typesafe": dict(
        stem="typesafe",
        create_name="typesafe",
        preload_name=None,
        requires_preload=False,
    ),
    "stannum": dict(
        stem="stannum",
        create_name="stannum",
        preload_name=None,
        requires_preload=False,
    ),
}


class SourceLock:
    __slots__ = ("name", "kind", "identity", "ref")

    def __init__(self, name: str, kind: str, identity: str, ref: str | None) -> None:
        self.name = name
        self.kind = kind
        self.identity = identity
        self.ref = ref


def parse_source_lock(spec: str) -> SourceLock:
    """Parse one Makefile-handoff lock: NAME=KIND:IDENTITY[:REF]."""
    name, sep, rest = spec.partition("=")
    if not sep or not name or not rest:
        raise ValueError(f"invalid --source-lock {spec!r}; expected NAME=KIND:IDENTITY[:REF]")
    kind, sep, rest = rest.partition(":")
    if not sep or not rest:
        raise ValueError(f"invalid --source-lock {spec!r}; expected NAME=KIND:IDENTITY[:REF]")
    if ":" in rest:
        identity, _, ref = rest.partition(":")
    else:
        identity, ref = rest, None
    if not identity or (ref is not None and not ref):
        raise ValueError(f"invalid --source-lock {spec!r}; identity/ref must be non-empty")
    if kind == "commit":
        if not COMMIT_IDENTITY.fullmatch(identity):
            raise ValueError(f"--source-lock {name} commit identity is not a 40-char git SHA: {identity!r}")
    elif kind == "sha256":
        if not SHA256_IDENTITY.fullmatch(identity):
            raise ValueError(f"--source-lock {name} sha256 identity is not a 64-char digest: {identity!r}")
    else:
        raise ValueError(f"--source-lock {name} has unknown kind {kind!r}; expected commit or sha256")
    return SourceLock(name, kind, identity, ref)


def parse_source_locks(specs: list[str] | None) -> dict[str, SourceLock]:
    locks: dict[str, SourceLock] = {}
    for spec in specs or []:
        lock = parse_source_lock(spec)
        previous = locks.get(lock.name)
        if previous is not None:
            raise ValueError(
                f"conflicting --source-lock for {lock.name}: "
                f"{previous.kind}:{previous.identity} vs {lock.kind}:{lock.identity}"
            )
        locks[lock.name] = lock
    known = set(EXTENSIONS) | AUXILIARY_LOCK_NAMES
    unknown = set(locks) - known
    if unknown:
        raise ValueError(f"unknown --source-lock names: {sorted(unknown)}")
    return locks


def lock_identity_fields(lock: SourceLock) -> tuple[str | None, str | None, str]:
    """Return (source_commit, source_sha256, source_ref) from one lock.

    source_ref is the Makefile pin's human-facing name: a real tag when the
    lock carries one, otherwise the identity itself. Commit-only pins such as
    psql_bm25s therefore use source_ref=<the commit>, matching the previous
    catalog precedent. Exactly one of source_commit / source_sha256 is set.
    """
    source_ref = lock.ref if lock.ref else lock.identity
    if lock.kind == "commit":
        return lock.identity, None, source_ref
    return None, lock.identity, source_ref


def _submodules_from_locks(
    source: dict[str, Any], locks: dict[str, SourceLock], *, required: bool
) -> dict[str, str]:
    submodules: dict[str, str] = {}
    for sub_name in source.get("submodule_locks", ()):
        lock = locks.get(sub_name)
        if lock is None:
            if required:
                raise ValueError(f"built extension is missing --source-lock for submodule {sub_name}")
            continue
        _, _, source_ref = lock_identity_fields(lock)
        if lock.kind == "sha256":
            submodules[sub_name] = f"{source_ref}:{lock.identity}"
        else:
            submodules[sub_name] = lock.identity
    return submodules


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
    try:
        text = control.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {control}: {exc}") from exc
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

    locks = parse_source_locks(getattr(args, "source_lock", None))

    suffix = "dylib" if args.host_os == "Darwin" else "so"
    records: dict[str, Any] = {}
    for name, source in EXTENSIONS.items():
        is_requested = name in requested
        is_built = name in built
        is_skipped = name in skipped
        stem = source["stem"]
        has_library = source.get("has_library", True)
        library = lib_dir / f"{stem}.{suffix}"
        control = extension_dir / f"{stem}.control"
        sql_paths: tuple[Path, ...] = ()
        version: str | None = None
        source_commit: str | None = None
        source_sha256: str | None = None
        source_ref: str | None = None
        source_submodules: dict[str, str] = {}
        if is_built:
            lock = locks.get(name)
            if lock is None:
                raise ValueError(f"built extension {name} has no --source-lock")
            source_commit, source_sha256, source_ref = lock_identity_fields(lock)
            if bool(source_commit) == bool(source_sha256):
                raise ValueError(
                    f"built extension {name} source-lock must set exactly one of "
                    f"source_commit or source_sha256"
                )
            if has_library:
                if not library.is_file():
                    raise FileNotFoundError(
                        f"metadata says {name} was built, but {library} is missing"
                    )
            else:
                unexpected_libraries = [
                    lib_dir / f"{stem}.{extra_suffix}"
                    for extra_suffix in ("so", "dylib", "dll")
                    if (lib_dir / f"{stem}.{extra_suffix}").exists()
                ]
                if unexpected_libraries:
                    raise ValueError(
                        f"{name} is SQL-only but native library artifacts are present: "
                        f"{', '.join(str(path) for path in unexpected_libraries)}"
                    )
            if not control.is_file():
                raise FileNotFoundError(
                    f"metadata says {name} was built, but {control} is missing"
                )
            version = _default_version(extension_dir, stem)
            sql_paths = _installation_sql_paths(extension_dir, stem)
            source_submodules = _submodules_from_locks(source, locks, required=True)
        else:
            stale_sql = tuple(extension_dir.glob(f"{stem}--*.sql"))
            if library.exists() or control.exists() or stale_sql:
                state = "skipped" if is_skipped else "not selected"
                raise ValueError(
                    f"{name} was {state} but stale installed artifacts remain"
                )
            # Skipped (requested but not built) may keep the Makefile pin as a
            # catalog expectation so release evidence can still record the recipe.
            # Unselected records must not claim an identity that was never built.
            if is_skipped and name in locks:
                source_commit, source_sha256, source_ref = lock_identity_fields(locks[name])
                source_submodules = _submodules_from_locks(source, locks, required=False)
        records[name] = {
            "requested": is_requested,
            "built": is_built,
            "skipped": is_skipped,
            "built_for_postgres_major": PG_MAJOR,
            "create_name": source["create_name"],
            "preload_name": source["preload_name"],
            "requires_preload": source["requires_preload"],
            "has_library": has_library,
            "library": _relative(library, prefix) if is_built and has_library else None,
            "control": _relative(control, prefix) if is_built else None,
            "install_sql": _relative(sql_paths[0], prefix) if sql_paths else None,
            "update_sql": [_relative(sql, prefix) for sql in sql_paths[1:]],
            "version": version,
            "source_ref": source_ref,
            "source_commit": source_commit,
            "source_sha256": source_sha256,
            "source_submodules": source_submodules,
            "skip_reason": args.skip_reason if is_skipped else None,
        }

    tigerfs_requested = "tigerfs" in requested
    tigerfs_built = "tigerfs" in built
    tigerfs_path = bin_dir / "tigerfs"
    tigerfs_binary_version: str | None = None
    tigerfs_sha256 = args.tigerfs_sha256 or None
    tigerfs_lock = locks.get("tigerfs")
    if tigerfs_lock is not None:
        if tigerfs_lock.kind != "sha256":
            raise ValueError("tigerfs --source-lock must be kind=sha256")
        if tigerfs_sha256 and tigerfs_sha256 != tigerfs_lock.identity:
            raise ValueError("tigerfs --source-lock disagrees with --tigerfs-sha256")
        tigerfs_sha256 = tigerfs_lock.identity
    if tigerfs_built:
        if tigerfs_sha256 is None:
            raise ValueError("built tigerfs has no --source-lock / --tigerfs-sha256")
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
    elif not tigerfs_built:
        # Unselected/skipped tigerfs must not advertise an archive identity.
        if not tigerfs_requested:
            tigerfs_sha256 = None

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
            "sha256": tigerfs_sha256,
            "binary": _relative(tigerfs_path, prefix) if tigerfs_built else None,
            "binary_version": tigerfs_binary_version,
            "skip_reason": args.skip_reason if "tigerfs" in skipped else None,
        },
    }


def _lstat_or_none(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _absolute_keep_dotdot(path: Path) -> str:
    raw = os.fspath(path)
    if not os.path.isabs(raw):
        raw = os.path.join(os.getcwd(), raw)
    return raw


def validate_output_under_prefix(output: Path, install_prefix: Path) -> None:
    """Reject output that escapes *install_prefix* or writes through a child symlink.

    *install_prefix* and its ancestors may be symlinks. Components strictly
    below the prefix may not. ``os.path.realpath`` is used only to equate
    alternate spellings of the prefix; the walk keeps the original output
    components so child symlinks stay visible. ``..`` is not normalized
    away: leaving the prefix is rejected before any directory is created.
    """
    output_raw = _absolute_keep_dotdot(output)
    prefix_raw = _absolute_keep_dotdot(install_prefix)
    prefix_real = os.path.realpath(prefix_raw)

    parts = [part for part in output_raw.split(os.sep) if part and part != os.curdir]
    current = os.sep
    entered = False
    depth = 0

    for part in parts:
        if part == os.pardir:
            if entered and depth == 0:
                raise SystemExit("output path escapes install prefix: %s" % output)
            if entered:
                depth -= 1
            current = os.path.dirname(current) or os.sep
            continue

        current = os.path.join(current, part)
        if entered:
            st = _lstat_or_none(Path(current))
            if st is not None and stat.S_ISLNK(st.st_mode):
                raise SystemExit("output path component is a symlink: %s" % current)
            depth += 1
            continue

        try:
            at_prefix = os.path.realpath(current) == prefix_real
        except OSError:
            at_prefix = False
        if at_prefix:
            entered = True
            depth = 0

    if entered:
        return

    dest_st = _lstat_or_none(Path(output_raw))
    if dest_st is not None and stat.S_ISLNK(dest_st.st_mode):
        raise SystemExit("atomic_write destination is a symlink: %s" % output)
    parent = os.path.dirname(output_raw)
    parent_st = _lstat_or_none(Path(parent))
    if parent_st is not None and stat.S_ISLNK(parent_st.st_mode):
        raise SystemExit("atomic_write parent is a symlink: %s" % parent)
    raise SystemExit("output is not under install prefix: %s" % output)


def atomic_write(path: Path, payload: dict[str, Any], *, mode: int = PUBLIC_METADATA_MODE) -> None:
    """Atomically replace public metadata. ``mode`` is applied before rename."""
    parent = path.parent
    parent_st = _lstat_or_none(parent)
    if parent_st is None:
        parent.mkdir(parents=True, exist_ok=True)
        parent_st = parent.lstat()
    if stat.S_ISLNK(parent_st.st_mode):
        raise SystemExit("atomic_write parent is a symlink: %s" % parent)
    if not stat.S_ISDIR(parent_st.st_mode):
        raise SystemExit("atomic_write parent is not a directory: %s" % parent)
    dest_st = _lstat_or_none(path)
    if dest_st is not None and stat.S_ISLNK(dest_st.st_mode):
        raise SystemExit("atomic_write destination is a symlink: %s" % path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        if os.environ.get("JEV_OVERLAY_CRASH_BEFORE_RENAME") == "1":
            os._exit(91)
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
    result.add_argument(
        "--source-lock",
        action="append",
        default=[],
        metavar="NAME=KIND:IDENTITY[:REF]",
        help="Makefile-verified source identity; repeat once per component.",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    validate_output_under_prefix(args.output, args.install_prefix)
    payload = generate(args)
    atomic_write(args.output, payload)


SHARE_PGEMBED_MODE = 0o755
LIVE = Path()


def ensure_share_pgembed(prefix: Path) -> None:
    """Create or verify share/pgembed as dir/0755. Never a symlink."""
    import stat

    share = prefix / "share"
    if not share.exists():
        share.mkdir(mode=SHARE_PGEMBED_MODE)
        os.chmod(share, SHARE_PGEMBED_MODE)
    path = share / "pgembed"
    if path.is_symlink():
        raise SystemExit("share/pgembed is a symlink")
    if path.exists():
        st = path.lstat()
        if not stat.S_ISDIR(st.st_mode):
            raise SystemExit("share/pgembed exists and is not a directory")
        mode = st.st_mode & 0o777
        if mode != SHARE_PGEMBED_MODE:
            raise SystemExit("share/pgembed mode %s != 0o755" % oct(mode))
        return
    path.mkdir(mode=SHARE_PGEMBED_MODE)
    os.chmod(path, SHARE_PGEMBED_MODE)
    st = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(st.st_mode) or (st.st_mode & 0o777) != SHARE_PGEMBED_MODE:
        raise SystemExit("failed to create share/pgembed as dir 0755")


def freeze_overlay_mode(live: Path, evidence: Path) -> str:
    """Mode is selected from INITIAL LIVE state. Call once before O2; never again."""
    mode_file = evidence / "overlay.mode"
    if mode_file.exists():
        raise SystemExit("overlay.mode already frozen; refuse to re-select from later LIVE state")
    initial = evidence / "build-metadata.live-initial.json"
    if initial.exists():
        raise SystemExit("live-initial already exists; evidence dir is not unique")
    if live.is_file() and not live.is_symlink():
        mode = "live-existing"
        fd = os.open(initial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, live.read_bytes())
        finally:
            os.close(fd)
    else:
        mode = "bootstrap-generate"
    fd = os.open(mode_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, (mode + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return mode


def read_frozen_mode(evidence: Path) -> str:
    mode = (evidence / "overlay.mode").read_text(encoding="utf-8").strip()
    if mode not in ("live-existing", "bootstrap-generate"):
        raise SystemExit("bad overlay.mode %r" % mode)
    return mode


def source_locks_from_stamp(stamp: dict[str, str]) -> list[str]:
    """Turn the copied bundle-config stamp into --source-lock values."""
    locks: list[str] = []
    tag_sha = {
        "pgvector",
        "vectorchord",
        "timescaledb",
        "pgsql_http",
        "firebird_fdw",
        "libfq",
        "libtommath",
        "pgmq",
        "pg_partman",
        "pgtap",
    }
    commit_only = {
        "age",
        "psql_bm25s",
        "plsh",
        "pg_jsonschema",
        "pg_typesafe",
        "stannum",
    }
    tag_commit = {"pg_cron", "pg_net"}
    for name in tag_sha:
        value = stamp.get(name)
        if not value or ":" not in value:
            continue
        tag, _, digest = value.partition(":")
        locks.append(f"{name}=sha256:{digest}:{tag}")
    for name in commit_only:
        value = stamp.get(name)
        if not value:
            continue
        locks.append(f"{name}=commit:{value}")
    for name in tag_commit:
        value = stamp.get(name)
        if not value or ":" not in value:
            continue
        tag, _, commit = value.partition(":")
        locks.append(f"{name}=commit:{commit}:{tag}")
    firebird_client = stamp.get("firebird_client")
    if firebird_client:
        parts = firebird_client.split(":")
        locks.append(f"firebird-client=sha256:{parts[-1]}:{parts[0]}")
    tigerfs = stamp.get("tigerfs")
    if tigerfs:
        parts = tigerfs.split(":")
        locks.append(f"tigerfs=sha256:{parts[-1]}:{parts[0]}")
    return locks


def reconstruct_pg_typesafe(doc: dict, prefix: Path, sha: str) -> dict:
    """Fill extensions.pg_typesafe from post-install artifacts, not EXTENSIONS dict."""
    from datetime import datetime, timezone

    control = prefix / "share/postgresql/extension/typesafe.control"
    version = _default_version(prefix / "share/postgresql/extension", "typesafe")
    assert version == "0.1.0", version
    dlsuffix = "dylib" if (prefix / "lib/postgresql/typesafe.dylib").is_file() else "so"
    library = prefix / f"lib/postgresql/typesafe.{dlsuffix}"
    assert library.is_file()
    sql_paths = _installation_sql_paths(prefix / "share/postgresql/extension", "typesafe")
    doc.setdefault("extensions", {})
    ext = doc["extensions"].setdefault("pg_typesafe", {})
    ext.update({
        "requested": True, "built": True, "skipped": False, "skip_reason": None,
        "built_for_postgres_major": 18,
        "create_name": "typesafe", "preload_name": None,
        "requires_preload": False, "has_library": True,
        "version": version, "source_commit": None,
        "source_ref": "local-overlay:typesafe", "source_sha256": sha,
        "source_submodules": {},
        "library": _relative(library, prefix),
        "control": _relative(control, prefix),
        "install_sql": _relative(sql_paths[0], prefix),
        "update_sql": [_relative(p, prefix) for p in sql_paths[1:]],
    })
    assert not any(str(v).startswith("/") for v in (ext["library"], ext["control"], ext["install_sql"], *ext["update_sql"]))
    doc["generated_at"] = datetime.now(timezone.utc).isoformat()
    return doc


def build_pre(mode: str, evidence: Path, stamp_ns) -> dict:
    """pre 永远来自冻结的初始 LIVE 或 strip 后的 generate candidate，不是 O2 路径。"""
    import copy

    if mode == "live-existing":
        pre = json.loads((evidence / "build-metadata.live-initial.json").read_bytes())
    elif mode == "bootstrap-generate":
        if stamp_ns is None:
            raise SystemExit("--stamp required for bootstrap-generate")
        d = {}
        for line in Path(stamp_ns).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            d[k] = v
        prefix = LIVE.parent.parent.parent
        requested = d["requested_extensions"]
        built_names = []
        for name, source in EXTENSIONS.items():
            control = prefix / "share/postgresql/extension" / (str(source["stem"]) + ".control")
            if control.is_file():
                built_names.append(name)
        if (prefix / "bin/tigerfs").is_file():
            built_names.append("tigerfs")
        req_set = {item for item in requested.split() if item}
        skipped = " ".join(sorted(req_set - set(built_names)))
        tigerfs = d.get("tigerfs", "v0.7.0")
        tigerfs_parts = tigerfs.split(":")
        ns = argparse.Namespace(
            install_prefix=prefix,
            postgres_version=d["postgres_version"],
            postgres_ref=d["postgres_ref"],
            postgres_commit=d["postgres_commit"],
            configure_flags=d["postgres_configure"],
            requested=requested,
            built=" ".join(built_names),
            skipped=skipped,
            skip_reason="unsupported on this build platform",
            host_os=d["host_os"],
            arch=d["host_arch"],
            libc=d["libc"],
            deployment_target=d.get("macosx_deployment_target", ""),
            rust_toolchain=d["rust_toolchain"],
            cargo_pgrx_version=d["cargo_pgrx_version"],
            tigerfs_version=tigerfs_parts[0],
            tigerfs_sha256=tigerfs_parts[-1] if len(tigerfs_parts) > 1 else "",
            source_lock=source_locks_from_stamp(d),
        )
        candidate = generate(ns)
        cfd = os.open(evidence / "build-metadata.generate-candidate.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(cfd, (json.dumps(candidate, indent=2, sort_keys=True) + "\n").encode())
        os.close(cfd)
        pre = candidate
    else:
        raise SystemExit("unknown overlay mode %r" % mode)
    pfd = os.open(evidence / "build-metadata.pre.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(pfd, (json.dumps(pre, indent=2, sort_keys=True) + "\n").encode())
    os.close(pfd)
    return copy.deepcopy(pre)


def run_overlay(prefix, sha, stamp_ns, output=None):
    """output 默认真实 LIVE。O2 必须显式传入 o2-work LIVE。禁止在此处冻结模式。"""
    import copy

    evidence = Path(os.environ["JEV_EVIDENCE"])
    if not (evidence / "overlay.mode").is_file():
        raise SystemExit("overlay.mode missing; freeze from initial LIVE before O2 or real overlay")
    mode = read_frozen_mode(evidence)
    dest = Path(output) if output is not None else LIVE
    marker = evidence / "real-overlay.done"
    o2_root = (evidence / "o2-work").resolve()
    o2_dest = o2_root / "share" / "pgembed" / "build-metadata.json"
    crash = os.environ.get("JEV_OVERLAY_CRASH_BEFORE_RENAME") == "1"
    isolated = dest.resolve() == o2_dest.resolve()
    if isolated:
        if dest.resolve() == LIVE.resolve():
            raise SystemExit("O2 dest must not be real LIVE")
        if not crash:
            raise SystemExit("O2 isolated crash proof requires JEV_OVERLAY_CRASH_BEFORE_RENAME=1")
        try:
            os.mkdir(o2_root)
        except FileExistsError:
            raise SystemExit("o2-work already exists")
    else:
        if dest.resolve() != LIVE.resolve():
            raise SystemExit("real overlay dest must be LIVE")
        if crash:
            raise SystemExit("refuse crash-before-rename on real LIVE")
        if marker.exists():
            raise SystemExit("real overlay already ran; refuse second write")
    write_root = o2_root if isolated else Path(prefix)
    validate_output_under_prefix(dest, write_root)
    if not (evidence / "build-metadata.pre.json").is_file():
        build_pre(mode, evidence, stamp_ns)
    if dest.parent.name == "pgembed" and dest.parent.parent.name == "share":
        ensure_share_pgembed(dest.parent.parent.parent)
    pre = json.loads((evidence / "build-metadata.pre.json").read_bytes())
    doc = copy.deepcopy(pre)
    reconstruct_pg_typesafe(doc, prefix, sha)
    atomic_write(dest, doc)
    if dest.resolve() == LIVE.resolve():
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, b"1\n")
        os.close(fd)


def local_overlay_cli():
    """唯一 WI-3 overlay argv 入口。禁止落入 main() unlink。"""
    import argparse, subprocess, sys
    global LIVE

    p = argparse.ArgumentParser(prog="generate_bundle_metadata.py")
    p.add_argument("--local-overlay", action="store_true", required=True)
    p.add_argument("--freeze-mode", action="store_true")
    p.add_argument("--install-prefix", type=Path, required=True)
    p.add_argument("--pg-typesafe-source-dir", type=Path)
    p.add_argument("--stamp", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if "JEV_EVIDENCE" not in os.environ:
        raise SystemExit("JEV_EVIDENCE is required")
    evidence = Path(os.environ["JEV_EVIDENCE"]).resolve()
    prefix = args.install_prefix.resolve()
    if str(evidence).startswith(str(prefix) + os.sep):
        raise SystemExit("JEV_EVIDENCE must not be under install prefix")
    live = prefix / "share/pgembed/build-metadata.json"
    LIVE = live
    if args.freeze_mode:
        if args.output is not None:
            raise SystemExit("--output forbidden with --freeze-mode")
        print(freeze_overlay_mode(live, evidence))
        return
    if args.pg_typesafe_source_dir is None:
        raise SystemExit("--pg-typesafe-source-dir required")
    src = args.pg_typesafe_source_dir.resolve()
    sha = subprocess.check_output(
        [sys.executable, str(src / "scripts" / "canonical_source_sha256.py"), str(src)],
        text=True,
    ).strip()
    expected = (evidence / "source.sha256").read_text(encoding="utf-8").strip()
    if sha != expected:
        raise SystemExit("canonical sha mismatch vs JEV_EVIDENCE/source.sha256")
    run_overlay(prefix, sha, args.stamp, output=args.output)


if __name__ == "__main__":
    import sys
    if "--local-overlay" in sys.argv:
        local_overlay_cli()
    else:
        main()
